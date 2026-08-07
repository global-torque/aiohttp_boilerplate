from __future__ import annotations

import asyncio
import importlib
import logging
from typing import Any

import aiohttp_cors
import pytest
from aiohttp import web

from aiohttp_boilerplate.auth import validate_token
from aiohttp_boilerplate.bootstrap.web_app import create_app
from aiohttp_boilerplate.config import (
    AppConfig,
    ConfigurationError,
    CorsConfig,
    canonicalize_origins,
)
from aiohttp_boilerplate.middleware.logger_to_request import logger_to_request
from aiohttp_boilerplate.views.exceptions import JSONHTTPError, error_envelope
from aiohttp_boilerplate.views.request import (
    RequestContext,
    RequestLoggerAdapter,
    replace_request_context,
)

bootstrap_module = importlib.import_module("aiohttp_boilerplate.bootstrap.web_app")


class CloseCounter:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class PoolCounter(CloseCounter):
    async def acquire(self) -> Any:
        raise AssertionError("not used")

    async def release(self, connection: Any) -> None:
        raise AssertionError("not used")


def configure_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    def setup(_config: AppConfig, app: web.Application) -> None:
        async def ok(request: web.Request) -> web.Response:
            return web.json_response({"ok": True})

        async def rejected(request: web.Request) -> web.Response:
            raise web.HTTPBadRequest(reason="Bad widget")

        async def internal_http_error(request: web.Request) -> web.Response:
            raise web.HTTPInternalServerError(reason="SQL SELECT secret DB_PASSWORD=hunter2")

        async def crash(request: web.Request) -> web.Response:
            raise RuntimeError("Authorization=Bearer top-secret")

        app.router.add_get("/widgets", ok)
        app.router.add_get("/rejected", rejected)
        app.router.add_get("/internal-http-error", internal_http_error)
        app.router.add_get("/crash", crash)

    monkeypatch.setattr(bootstrap_module, "_load_routes", setup)
    monkeypatch.setattr(bootstrap_module, "_load_optional_setup", lambda *args: None)


def config_with_cors(origins: str = "") -> AppConfig:
    return AppConfig.from_mapping(
        {
            "app_dir": "unused",
            "CORS_ALLOWED_ORIGINS": origins,
            "CORS_ALLOW_CREDENTIALS": True,
            "CORS_EXPOSE_HEADERS": "X-Request-ID",
            "CORS_MAX_AGE": 600,
        }
    )


def test_origin_canonicalization_and_legacy_domain_contract() -> None:
    assert canonicalize_origins(
        "HTTPS://Example.COM:443,https://example.com,http://example.com:8080"
    ) == ("https://example.com", "http://example.com:8080")
    with pytest.raises(ConfigurationError, match="invalid origin"):
        canonicalize_origins("https://example.com/path")
    with pytest.raises(ConfigurationError, match="invalid origin"):
        canonicalize_origins("https://example.com/")
    with pytest.raises(ConfigurationError, match="invalid origin"):
        canonicalize_origins("https://*.example.com")
    with pytest.raises(ConfigurationError, match="invalid origin"):
        canonicalize_origins("https://example.com\n.evil.test")
    with pytest.raises(ConfigurationError, match="invalid origin"):
        canonicalize_origins("https://example.com:")
    assert canonicalize_origins("http://[::1]:80,https://[::1]:8443") == (
        "http://[::1]",
        "https://[::1]:8443",
    )
    with pytest.raises(ConfigurationError, match="DOMAIN is no longer supported"):
        AppConfig.from_mapping({"DOMAIN": "example.com"})


def test_app_config_is_immutable_and_separates_versions() -> None:
    config = AppConfig.from_mapping(
        {
            "web_run": {"host": "127.0.0.1", "port": "8080"},
            "openapi": {"version": "2.4.0", "openapi_version": "3.0.3"},
            "wallet_api": {"url": "https://wallet.example.test"},
        }
    )
    assert config.web_run["port"] == 8080
    assert config.openapi["version"] == "2.4.0"
    assert config.openapi["openapi_version"] == "3.0.3"
    assert config["wallet_api"]["url"] == "https://wallet.example.test"
    with pytest.raises(TypeError):
        config.web_run["port"] = 9000
    with pytest.raises(TypeError):
        config["wallet_api"]["url"] = "https://attacker.test"
    with pytest.raises(ConfigurationError, match="postgres.port"):
        AppConfig.from_mapping({"postgres": {"port": 0, "max_size": 1}})
    with pytest.raises(ConfigurationError, match="postgres.max_size"):
        AppConfig.from_mapping({"postgres": {"port": 5432, "max_size": 0}})


def test_direct_app_config_constructor_validates_and_freezes() -> None:
    with pytest.raises(ConfigurationError, match="web_run.port"):
        AppConfig(web_run={"port": "not-a-port"})
    with pytest.raises(ConfigurationError, match="postgres.max_size"):
        AppConfig(postgres={"max_size": 0})

    config = AppConfig(web_run={"host": "127.0.0.1", "port": "8080"})
    assert config.web_run["port"] == 8080
    with pytest.raises(TypeError):
        config.web_run["port"] = 99999


@pytest.mark.parametrize(
    "auth_url",
    ["http://auth.example\n.evil.test/validate", "https://auth.example:"],
)
def test_auth_url_rejects_malformed_raw_values(auth_url: str) -> None:
    with pytest.raises(ConfigurationError, match="AUTH_URL"):
        AppConfig.from_mapping({"AUTH_URL": auth_url})
    with pytest.raises(ConfigurationError, match="AUTH_URL"):
        AppConfig(auth_url=auth_url)


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("CORS_ALLOW_HEADERS", "Authorization\r\nInjected: yes"),
        ("CORS_EXPOSE_HEADERS", "X-Good\r\nInjected: yes"),
    ],
)
def test_cors_header_names_reject_control_characters(setting: str, value: str) -> None:
    with pytest.raises(ConfigurationError, match=setting):
        AppConfig.from_mapping({setting: value})

    keyword = "allow_headers" if setting == "CORS_ALLOW_HEADERS" else "expose_headers"
    with pytest.raises(ConfigurationError, match=setting):
        CorsConfig(**{keyword: (value,)})


def test_request_context_nested_metadata_is_immutable() -> None:
    context = RequestContext(
        request_id="request-1",
        source_reference={"repository": "widgets"},
        extra_data={"customer": "alice"},
    )
    with pytest.raises(TypeError):
        context.source_reference["repository"] = "attacker"
    with pytest.raises(TypeError):
        context.extra_data["customer"] = "bob"
    adapter = RequestLoggerAdapter(logging.getLogger(), context)
    with pytest.raises(AttributeError, match="immutable"):
        adapter.context = RequestContext(request_id="attacker")


def test_error_envelope_redacts_nested_non_serializable_values() -> None:
    envelope = error_envelope(
        400,
        "Bad Request",
        {"field": [ValueError("secret validation value")]},
    )
    assert envelope["error"]["details"] == {"field": ["Invalid value"]}
    internal = JSONHTTPError(
        None,
        {"sql": "SELECT secret DB_PASSWORD=hunter2"},
        web.HTTPInternalServerError,
    )
    assert "hunter2" not in internal.text


def test_cors_route_validation_rejects_view_without_mixin() -> None:
    class BareView(web.View):
        async def get(self) -> web.Response:
            return web.Response()

    app = web.Application()
    app.router.add_get("/bare", BareView)
    with pytest.raises(RuntimeError, match="must inherit aiohttp_cors.CorsViewMixin"):
        bootstrap_module._validate_cors_routes(tuple(app.router.routes()))


def test_cors_route_validation_rejects_options_and_non_view_wildcards() -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response()

    options_app = web.Application()
    options_app.router.add_options("/explicit", handler)
    with pytest.raises(RuntimeError, match="Explicit OPTIONS route for /explicit"):
        bootstrap_module._validate_cors_routes(tuple(options_app.router.routes()))

    wildcard_app = web.Application()
    wildcard_app.router.add_route("*", "/wildcard", handler)
    with pytest.raises(RuntimeError, match="Wildcard route for /wildcard"):
        bootstrap_module._validate_cors_routes(tuple(wildcard_app.router.routes()))

    class CompatibleView(aiohttp_cors.CorsViewMixin, web.View):
        async def get(self) -> web.Response:
            return web.Response()

    migrated_app = web.Application()
    migrated_app.router.add_view("/migrated", CompatibleView)
    bootstrap_module._validate_cors_routes(tuple(migrated_app.router.routes()))


@pytest.mark.asyncio
async def test_cors_exact_origin_preflight_and_router_boundary(
    aiohttp_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_routes(monkeypatch)
    pool = PoolCounter()
    auth_session = CloseCounter()
    app = create_app(
        config_with_cors("https://api.example.com"),
        db_pool=pool,
        auth_session=auth_session,
    )
    client = await aiohttp_client(app)

    allowed = await client.get("/widgets", headers={"Origin": "https://api.example.com"})
    assert allowed.status == 200
    assert allowed.headers["Access-Control-Allow-Origin"] == "https://api.example.com"
    assert allowed.headers["Access-Control-Allow-Credentials"] == "true"
    assert allowed.headers["Access-Control-Expose-Headers"] == "X-Request-ID"

    attacker = await client.get("/widgets", headers={"Origin": "https://api.example.com.evil.test"})
    assert "Access-Control-Allow-Origin" not in attacker.headers

    preflight = await client.options(
        "/widgets",
        headers={
            "Origin": "https://api.example.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert preflight.status == 200
    assert preflight.headers["Access-Control-Allow-Methods"] == "GET"
    assert preflight.headers["Access-Control-Allow-Headers"] == "AUTHORIZATION"
    assert preflight.headers["Access-Control-Max-Age"] == "600"
    assert "Access-Control-Expose-Headers" not in preflight.headers

    registered_error = await client.get("/rejected", headers={"Origin": "https://api.example.com"})
    assert registered_error.status == 400
    assert registered_error.headers["Access-Control-Allow-Origin"] == "https://api.example.com"
    assert (await registered_error.json())["error"]["status"] == 400

    missing = await client.get("/missing", headers={"Origin": "https://api.example.com"})
    assert missing.status == 404
    assert "Access-Control-Allow-Origin" not in missing.headers

    method_not_allowed = await client.post(
        "/widgets", headers={"Origin": "https://api.example.com"}
    )
    assert method_not_allowed.status == 405
    assert "Access-Control-Allow-Origin" not in method_not_allowed.headers

    health = await client.get("/healthcheck", headers={"Origin": "https://api.example.com"})
    assert health.status == 200
    assert health.headers["Access-Control-Allow-Origin"] == "https://api.example.com"


@pytest.mark.asyncio
async def test_openapi_thaws_nested_config_and_excludes_generated_options(
    aiohttp_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_routes(monkeypatch)
    config = AppConfig.from_mapping(
        {
            "app_dir": "unused",
            "openapi_enabled": True,
            "openapi": {
                "title": "Widgets",
                "version": "0.9.0",
                "openapi_version": "3.0.3",
                "url": "/api/docs/swagger.json",
                "swagger_path": "/docs",
                "servers": [{"url": "/investment-api"}],
            },
            "CORS_ALLOWED_ORIGINS": "https://api.example.com",
        }
    )
    app = create_app(config, db_pool=PoolCounter(), auth_session=CloseCounter())
    client = await aiohttp_client(app)

    response = await client.get(
        "/api/docs/swagger.json",
        headers={"Origin": "https://api.example.com"},
    )
    document = await response.json()

    assert response.status == 200
    assert response.headers["Access-Control-Allow-Origin"] == "https://api.example.com"
    assert document["servers"] == [{"url": "/investment-api"}]
    assert all("options" not in operations for operations in document["paths"].values())


@pytest.mark.asyncio
async def test_disabled_cors_adds_no_preflight_routes(
    aiohttp_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_routes(monkeypatch)
    app = create_app(config_with_cors(), db_pool=PoolCounter(), auth_session=CloseCounter())
    assert not any(route.method == "OPTIONS" for route in app.router.routes())
    client = await aiohttp_client(app)
    response = await client.get("/widgets", headers={"Origin": "https://api.example.com"})
    assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.asyncio
async def test_internal_errors_do_not_expose_or_log_secrets(
    aiohttp_client, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    configure_routes(monkeypatch)
    app = create_app(config_with_cors(), db_pool=PoolCounter(), auth_session=CloseCounter())
    client = await aiohttp_client(app)

    http_error = await client.get("/internal-http-error")
    crash = await client.get("/crash", headers={"X-Request-ID": "crash-request"})

    assert http_error.status == 500
    assert await http_error.json() == {"error": {"status": 500, "message": "Internal Server Error"}}
    assert crash.status == 500
    assert crash.headers["X-Request-ID"] == "crash-request"
    assert "top-secret" not in await crash.text()
    assert "top-secret" not in caplog.text
    assert "hunter2" not in caplog.text


@pytest.mark.asyncio
async def test_injected_resource_ownership_closes_exactly_once(
    aiohttp_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_routes(monkeypatch)
    pool = PoolCounter()
    session = CloseCounter()
    app = create_app(
        config_with_cors(),
        db_pool=pool,
        auth_session=session,
        take_ownership=True,
    )
    client = await aiohttp_client(app)
    await client.get("/healthcheck")
    await client.close()
    assert pool.closed == 1
    assert session.closed == 1


@pytest.mark.asyncio
async def test_injected_resources_remain_caller_owned(
    aiohttp_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_routes(monkeypatch)
    pool = PoolCounter()
    session = CloseCounter()
    app = create_app(config_with_cors(), db_pool=pool, auth_session=session)
    assert app[bootstrap_module.DB_POOL_KEY] is pool
    assert app.db_pool is pool
    assert app[bootstrap_module.AUTH_SESSION_KEY] is session
    assert app.auth_session is session
    client = await aiohttp_client(app)
    await client.close()
    assert pool.closed == 0
    assert session.closed == 0


@pytest.mark.asyncio
async def test_factory_resources_close_once_after_repeated_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_routes(monkeypatch)
    pool = PoolCounter()
    session = CloseCounter()

    async def create_pool(_settings: Any) -> PoolCounter:
        return pool

    monkeypatch.setattr(bootstrap_module.db, "create_pool", create_pool)
    monkeypatch.setattr(bootstrap_module.aiohttp, "ClientSession", lambda **kwargs: session)
    runner = web.AppRunner(create_app(config_with_cors()))
    await runner.setup()
    await runner.cleanup()
    await runner.cleanup()
    assert pool.closed == 1
    assert session.closed == 1


@pytest.mark.asyncio
async def test_later_startup_failure_releases_earlier_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_routes(monkeypatch)
    pool = PoolCounter()

    async def create_pool(_settings: Any) -> PoolCounter:
        return pool

    def fail_auth_session(**_kwargs: Any) -> None:
        raise RuntimeError("auth session startup failed")

    monkeypatch.setattr(bootstrap_module.db, "create_pool", create_pool)
    monkeypatch.setattr(bootstrap_module.aiohttp, "ClientSession", fail_auth_session)
    runner = web.AppRunner(create_app(config_with_cors()))
    with pytest.raises(RuntimeError, match="auth session startup failed"):
        await runner.setup()
    await runner.cleanup()
    assert pool.closed == 1


class FakeResponse:
    def __init__(self, status: int, payload: Any = None, error: Exception | None = None) -> None:
        self.status = status
        self.payload = payload
        self.error = error

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def json(self, *, content_type: Any = None) -> Any:
        if self.error:
            raise self.error
        return self.payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, headers: dict[str, str]) -> FakeResponse:
        self.calls.append((url, headers))
        return self.response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_status", "expected"),
    [
        (FakeResponse(200, {"user_id": 42}), None, {"user_id": 42}),
        (FakeResponse(204), None, {}),
        (FakeResponse(200, [1, 2]), 502, None),
        (FakeResponse(200, error=ValueError("bad json")), 502, None),
        (FakeResponse(401, {"error": "no"}), 403, None),
        (FakeResponse(500, {"error": "down"}), 503, None),
    ],
)
async def test_auth_upstream_contract(
    response: FakeResponse, expected_status: int | None, expected: Any
) -> None:
    session = FakeSession(response)
    if expected_status is None:
        assert (
            await validate_token({"Authorization": "Bearer token"}, "http://auth", session)
            == expected
        )
    else:
        with pytest.raises(JSONHTTPError) as raised:
            await validate_token({"Authorization": "Bearer token"}, "http://auth", session)
        assert raised.value.status == expected_status
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_request_context_is_isolated_under_interleaving(aiohttp_client) -> None:
    alice_entered = asyncio.Event()
    bob_finished_identity = asyncio.Event()

    async def user(request: web.Request) -> web.Response:
        identity = request.match_info["identity"]
        if identity == "alice":
            alice_entered.set()
            await bob_finished_identity.wait()
        replace_request_context(request, user=identity)
        if identity == "bob":
            bob_finished_identity.set()
        await asyncio.sleep(0)
        return web.json_response(
            {"user": request.context.user, "request_id": request.context.request_id}
        )

    app = web.Application(middlewares=[logger_to_request])
    app.router.add_get("/{identity}", user)
    client = await aiohttp_client(app)
    alice_task = asyncio.create_task(
        client.get("/alice", headers={"X-Request-ID": "request-alice"})
    )
    await alice_entered.wait()
    bob = await client.get("/bob", headers={"X-Request-ID": "request-bob"})
    alice = await alice_task

    assert await alice.json() == {"user": "alice", "request_id": "request-alice"}
    assert await bob.json() == {"user": "bob", "request_id": "request-bob"}
    assert alice.headers["X-Request-ID"] == "request-alice"
    assert bob.headers["X-Request-ID"] == "request-bob"
