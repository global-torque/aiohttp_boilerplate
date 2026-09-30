"""aiohttp application factory and explicit resource ownership."""

from __future__ import annotations

import importlib
import inspect
import warnings
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast

import aiohttp
import aiohttp_cors
from aiohttp import hdrs, web
from aiohttp_apispec import setup_aiohttp_apispec

from aiohttp_boilerplate.config import APP_CONFIG_KEY, AppConfig
from aiohttp_boilerplate.dbpool import pg as db
from aiohttp_boilerplate.middleware.defaults import erase_header_server
from aiohttp_boilerplate.middleware.errors import json_error_middleware
from aiohttp_boilerplate.middleware.logger_to_request import logger_to_request
from aiohttp_boilerplate.middleware.transactions import atomic_request_middleware
from aiohttp_boilerplate.transactions import install_response_guard
from aiohttp_boilerplate.views.request import REQUEST_CONTEXT_KEY

DB_POOL_KEY: web.AppKey[Any] = web.AppKey("db_pool", object)
AUTH_SESSION_KEY: web.AppKey[aiohttp.ClientSession] = web.AppKey(
    "auth_session", aiohttp.ClientSession
)
CORS_KEY: web.AppKey[aiohttp_cors.CorsConfig] = web.AppKey("cors", aiohttp_cors.CorsConfig)
SHUTDOWN_TIMEOUT_KEY: web.AppKey[float] = web.AppKey("shutdown_timeout", float)


def _mutable_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _mutable_copy(item) for key, item in value.items()}
    if isinstance(value, tuple | frozenset):
        return [_mutable_copy(item) for item in value]
    return value


async def healthcheck(request: web.Request) -> web.Response:
    """Return readiness for the framework route."""
    return web.Response(text="Ok")


def _load_optional_setup(module_name: str, app: web.Application) -> None:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        return
    setup = getattr(module, "setup", None)
    if setup is not None:
        setup(app)


def _load_routes(config: AppConfig, app: web.Application) -> None:
    module_name = f"{config.app_dir}.routes"
    routes = importlib.import_module(module_name)
    setup_routes = getattr(routes, "setup_routes", None)
    if setup_routes is None:
        raise RuntimeError(f"{module_name} must define setup_routes(app)")
    setup_routes(app)


def _load_middlewares(paths: tuple[str, ...]) -> list[Any]:
    loaded: list[Any] = []
    for path in paths:
        try:
            module_name, attribute = path.rsplit(".", 1)
        except ValueError as exc:
            raise RuntimeError(f"Invalid middleware import path {path!r}") from exc
        middleware = getattr(importlib.import_module(module_name), attribute)
        if not callable(middleware):
            raise RuntimeError(f"Configured middleware {path!r} is not callable")
        loaded.append(middleware)
    return loaded


def _validate_cors_routes(routes: tuple[web.AbstractRoute, ...]) -> None:
    for route in routes:
        resource = route.resource
        path = resource.canonical if resource is not None else repr(resource)
        handler = route.handler
        if route.method == hdrs.METH_OPTIONS:
            raise RuntimeError(
                f"Explicit OPTIONS route for {path} is incompatible with aiohttp-cors; "
                "remove it and configure ResourceOptions"
            )
        handler_class = cast(type[Any], handler) if inspect.isclass(handler) else None
        is_view = handler_class is not None and issubclass(handler_class, web.View)
        if (
            is_view
            and handler_class is not None
            and not issubclass(handler_class, aiohttp_cors.CorsViewMixin)
        ):
            raise RuntimeError(
                f"View {handler.__name__} for {path} must inherit aiohttp_cors.CorsViewMixin"
            )
        if route.method == hdrs.METH_ANY and not is_view:
            raise RuntimeError(
                f"Wildcard route for {path} is incompatible with aiohttp-cors; "
                "register explicit methods"
            )


async def _request_id_on_prepare(request: web.Request, response: web.StreamResponse) -> None:
    context = request.get(REQUEST_CONTEXT_KEY)
    if context is not None:
        response.headers.setdefault("X-Request-ID", context.request_id)


def _setup_cors(app: web.Application, config: AppConfig) -> None:
    routes = tuple(app.router.routes())
    _validate_cors_routes(routes)
    if not config.cors.allowed_origins:
        return
    defaults = {
        origin: cast(Any, aiohttp_cors.ResourceOptions)(
            allow_credentials=config.cors.allow_credentials,
            allow_headers=config.cors.allow_headers,
            expose_headers=config.cors.expose_headers,
            max_age=config.cors.max_age,
        )
        for origin in config.cors.allowed_origins
    }
    cors = aiohttp_cors.setup(app, defaults=defaults)
    app[CORS_KEY] = cors
    registered: set[int] = set()
    for route in routes:
        if id(route) not in registered:
            cors.add(route)
            registered.add(id(route))


def _resource_contexts(
    config: AppConfig,
    *,
    db_pool: Any,
    auth_session: aiohttp.ClientSession | None,
    take_ownership: bool,
) -> tuple[Any, Any]:
    async def database_context(app: web.Application) -> AsyncIterator[None]:
        resource = db_pool
        owned = take_ownership and resource is not None
        if resource is None:
            resource = await db.create_pool(config.postgres)
            owned = True
        try:
            if app.get(DB_POOL_KEY) is not resource:
                app[DB_POOL_KEY] = resource
                warnings.warn(
                    "app.db_pool is deprecated; use app[DB_POOL_KEY]",
                    DeprecationWarning,
                    stacklevel=2,
                )
                app.db_pool = resource
            yield
        finally:
            if owned:
                await resource.close()

    async def auth_context(app: web.Application) -> AsyncIterator[None]:
        resource = auth_session
        owned = take_ownership and resource is not None
        if resource is None:
            timeout = aiohttp.ClientTimeout(
                total=config.auth_timeout_total,
                connect=config.auth_timeout_connect,
                sock_read=config.auth_timeout_read,
            )
            resource = aiohttp.ClientSession(timeout=timeout)
            owned = True
        try:
            if app.get(AUTH_SESSION_KEY) is not resource:
                app[AUTH_SESSION_KEY] = resource
                app.auth_session = resource
            yield
        finally:
            if owned:
                await resource.close()

    return database_context, auth_context


def create_app(
    config: AppConfig | Mapping[str, Any],
    *,
    db_pool: Any = None,
    auth_session: aiohttp.ClientSession | None = None,
    take_ownership: bool = False,
) -> web.Application:
    """Create an app whose resources have explicit independent ownership."""
    validated = config if isinstance(config, AppConfig) else AppConfig.from_mapping(config)
    app = web.Application(
        middlewares=[
            logger_to_request,
            json_error_middleware,
            erase_header_server,
            atomic_request_middleware,
            *_load_middlewares(validated.middlewares),
        ]
    )
    install_response_guard(app)
    app[APP_CONFIG_KEY] = validated
    app[SHUTDOWN_TIMEOUT_KEY] = validated.shutdown_timeout
    warnings.warn(
        "app.conf is deprecated; use app[APP_CONFIG_KEY]",
        DeprecationWarning,
        stacklevel=2,
    )
    app.conf = validated
    if db_pool is not None:
        app[DB_POOL_KEY] = db_pool
        warnings.warn(
            "app.db_pool is deprecated; use app[DB_POOL_KEY]",
            DeprecationWarning,
            stacklevel=2,
        )
        app.db_pool = db_pool
    if auth_session is not None:
        app[AUTH_SESSION_KEY] = auth_session
        app.auth_session = auth_session

    database_context, auth_context = _resource_contexts(
        validated,
        db_pool=db_pool,
        auth_session=auth_session,
        take_ownership=take_ownership,
    )
    app.cleanup_ctx.append(database_context)
    app.cleanup_ctx.append(auth_context)
    app.on_response_prepare.append(_request_id_on_prepare)

    app.router.add_get("/healthcheck", healthcheck, name="healthcheck")
    _load_optional_setup(f"{validated.app_dir}.tasks", app)
    _load_routes(validated, app)
    if validated.openapi_enabled:
        setup_aiohttp_apispec(app, **_mutable_copy(validated.openapi))
    _setup_cors(app, validated)
    return app


def start_web_app(
    conf: AppConfig | Mapping[str, Any], db_pool: Any, loop: Any = None
) -> web.Application:
    """Deprecated wrapper preserving ownership of the injected legacy pool."""
    warnings.warn(
        "start_web_app() is deprecated; use create_app()",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_app(conf, db_pool=db_pool, take_ownership=True)
