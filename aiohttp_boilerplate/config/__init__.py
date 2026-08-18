"""Application configuration loaded explicitly at startup."""

from __future__ import annotations

import builtins
import importlib
import math
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeVar, overload
from urllib.parse import urlsplit

from aiohttp import web
from yarl import URL

HTTP_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class ConfigurationError(ValueError):
    """Raised when deployment configuration is invalid."""


class _Missing:
    """Sentinel type for environment values without a default."""


_MISSING = _Missing()
_EnvironmentValue = TypeVar("_EnvironmentValue")


class Environment:
    """Read required and optional typed values from an environment mapping.

    Missing values and empty strings are treated as unset. An unset value
    raises ``ConfigurationError`` unless the caller provides a default.
    """

    def __init__(self, source: Mapping[str, str] | None = None) -> None:
        """Create a reader over ``source`` or the process environment."""
        self._source = os.environ if source is None else source

    def _read(
        self,
        name: builtins.str,
        default: _EnvironmentValue | _Missing,
    ) -> builtins.str | _EnvironmentValue:
        value = self._source.get(name)
        if value is not None and value != "":
            return value
        if isinstance(default, _Missing):
            raise ConfigurationError(f"Required environment variable {name} is missing or empty")
        return default

    @overload
    def str(self, name: builtins.str) -> builtins.str: ...

    @overload
    def str(self, name: builtins.str, default: builtins.str) -> builtins.str: ...

    @overload
    def str(self, name: builtins.str, default: None) -> builtins.str | None: ...

    def str(
        self,
        name: builtins.str,
        default: builtins.str | None | _Missing = _MISSING,
    ) -> builtins.str | None:
        """Read a string, requiring a non-empty value when no default is given."""
        value = self._read(name, default)
        if value is None or isinstance(value, builtins.str):
            return value
        raise TypeError("string defaults must be str or None")

    @overload
    def bool(self, name: builtins.str) -> builtins.bool: ...

    @overload
    def bool(self, name: builtins.str, default: builtins.bool) -> builtins.bool: ...

    @overload
    def bool(self, name: builtins.str, default: None) -> builtins.bool | None: ...

    def bool(
        self,
        name: builtins.str,
        default: builtins.bool | None | _Missing = _MISSING,
    ) -> builtins.bool | None:
        """Read a boolean using common true and false spellings."""
        value = self._read(name, default)
        if value is None or isinstance(value, builtins.bool):
            return value
        return _as_bool(value, setting=name)

    @overload
    def int(self, name: builtins.str) -> builtins.int: ...

    @overload
    def int(self, name: builtins.str, default: builtins.int) -> builtins.int: ...

    @overload
    def int(self, name: builtins.str, default: None) -> builtins.int | None: ...

    def int(
        self,
        name: builtins.str,
        default: builtins.int | None | _Missing = _MISSING,
    ) -> builtins.int | None:
        """Read an integer value."""
        value = self._read(name, default)
        if value is None or isinstance(value, builtins.int):
            return value
        return _as_int(value, setting=name)

    @overload
    def float(self, name: builtins.str) -> builtins.float: ...

    @overload
    def float(self, name: builtins.str, default: builtins.float) -> builtins.float: ...

    @overload
    def float(self, name: builtins.str, default: None) -> builtins.float | None: ...

    def float(
        self,
        name: builtins.str,
        default: builtins.float | None | _Missing = _MISSING,
    ) -> builtins.float | None:
        """Read a finite floating-point value."""
        value = self._read(name, default)
        if value is None:
            return None
        if isinstance(value, builtins.float):
            result = value
        else:
            try:
                result = builtins.float(value)
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(f"{name} must be a number, got {value!r}") from exc
        if not math.isfinite(result):
            raise ConfigurationError(f"{name} must be finite, got {value!r}")
        return result

    @overload
    def list(self, name: builtins.str) -> builtins.list[builtins.str]: ...

    @overload
    def list(
        self,
        name: builtins.str,
        default: builtins.list[builtins.str] | tuple[builtins.str, ...],
    ) -> builtins.list[builtins.str]: ...

    @overload
    def list(self, name: builtins.str, default: None) -> builtins.list[builtins.str] | None: ...

    def list(
        self,
        name: builtins.str,
        default: (
            builtins.list[builtins.str] | tuple[builtins.str, ...] | None | _Missing
        ) = _MISSING,
    ) -> builtins.list[builtins.str] | None:
        """Read a comma-separated list, trimming entries and omitting empties."""
        value = self._read(name, default)
        if value is None:
            return None
        if isinstance(value, builtins.str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, builtins.list | tuple):
            return [builtins.str(item) for item in value]
        raise TypeError("list defaults must be list, tuple, or None")


def _immutable_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze(item) for key, item in (value or {}).items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _immutable_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _as_bool(value: Any, *, setting: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigurationError(f"{setting} must be a boolean, got {value!r}")


def _as_int(value: Any, *, setting: str, minimum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{setting} must be an integer, got {value!r}") from exc
    if minimum is not None and result < minimum:
        raise ConfigurationError(f"{setting} must be at least {minimum}, got {result}")
    return result


def _as_float(value: Any, *, setting: str, minimum: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{setting} must be a number, got {value!r}") from exc
    if not math.isfinite(result) or result <= minimum:
        raise ConfigurationError(f"{setting} must be greater than {minimum}, got {result}")
    return result


def _csv(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value.split(",") if isinstance(value, str) else value
    return tuple(str(item).strip() for item in values if str(item).strip())


def _header_names(value: Any, *, setting: str) -> tuple[str, ...]:
    names = _csv(value)
    for name in names:
        if not HTTP_TOKEN_RE.fullmatch(name):
            raise ConfigurationError(f"{setting} contains invalid header name {name!r}")
    return names


def _has_invalid_raw_characters(value: str) -> bool:
    return any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value
    )


def canonicalize_origins(value: Any) -> tuple[str, ...]:
    """Validate and canonicalize exact serialized HTTP(S) origins."""
    origins: list[str] = []
    seen: set[str] = set()
    for raw_origin in _csv(value):
        try:
            parsed = URL(raw_origin)
            split = urlsplit(raw_origin)
            if (
                _has_invalid_raw_characters(raw_origin)
                or parsed.scheme not in {"http", "https"}
                or not parsed.host
                or "*" in parsed.host
                or parsed.user is not None
                or parsed.password is not None
                or split.path
                or "?" in raw_origin
                or "#" in raw_origin
                or parsed.query_string
                or parsed.fragment
                or split.netloc.endswith(":")
                or raw_origin in {"*", "null"}
            ):
                raise ValueError
            canonical = str(parsed.origin())
        except (TypeError, ValueError):
            raise ConfigurationError(
                f"CORS_ALLOWED_ORIGINS contains invalid origin {raw_origin!r}"
            ) from None
        if canonical not in seen:
            origins.append(canonical)
            seen.add(canonical)
    return tuple(origins)


@dataclass(frozen=True, slots=True)
class CorsConfig:
    """Validated exact-origin CORS configuration."""

    allowed_origins: tuple[str, ...] = ()
    allow_credentials: bool = False
    allow_headers: tuple[str, ...] = (
        "Authorization",
        "Content-Type",
        "X-Requested-With",
        "X-Request-ID",
    )
    expose_headers: tuple[str, ...] = ()
    max_age: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_origins", canonicalize_origins(self.allowed_origins))
        object.__setattr__(
            self,
            "allow_credentials",
            _as_bool(self.allow_credentials, setting="CORS_ALLOW_CREDENTIALS"),
        )
        object.__setattr__(
            self,
            "allow_headers",
            _header_names(self.allow_headers, setting="CORS_ALLOW_HEADERS"),
        )
        object.__setattr__(
            self,
            "expose_headers",
            _header_names(self.expose_headers, setting="CORS_EXPOSE_HEADERS"),
        )
        if self.max_age is not None:
            object.__setattr__(
                self,
                "max_age",
                _as_int(self.max_age, setting="CORS_MAX_AGE", minimum=0),
            )

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> CorsConfig:
        origins_value = values.get("CORS_ALLOWED_ORIGINS", values.get("cors_allowed_origins", ""))
        if (values.get("DOMAIN") or values.get("domain")) and not _csv(origins_value):
            raise ConfigurationError(
                "DOMAIN is no longer supported; set CORS_ALLOWED_ORIGINS to an "
                "explicit comma-separated origin list"
            )
        max_age_value = values.get("CORS_MAX_AGE", values.get("cors_max_age"))
        max_age = (
            None
            if max_age_value in {None, ""}
            else _as_int(max_age_value, setting="CORS_MAX_AGE", minimum=0)
        )
        return cls(
            allowed_origins=canonicalize_origins(origins_value),
            allow_credentials=_as_bool(
                values.get(
                    "CORS_ALLOW_CREDENTIALS",
                    values.get("cors_allow_credentials", False),
                ),
                setting="CORS_ALLOW_CREDENTIALS",
            ),
            allow_headers=_header_names(
                values.get(
                    "CORS_ALLOW_HEADERS",
                    values.get(
                        "cors_allow_headers",
                        "Authorization,Content-Type,X-Requested-With,X-Request-ID",
                    ),
                ),
                setting="CORS_ALLOW_HEADERS",
            ),
            expose_headers=_header_names(
                values.get("CORS_EXPOSE_HEADERS", values.get("cors_expose_headers", "")),
                setting="CORS_EXPOSE_HEADERS",
            ),
            max_age=max_age,
        )


_APP_CONFIG_VALIDATION_TOKEN = object()


@dataclass(frozen=True, slots=True)
class AppConfig(Mapping[str, Any]):
    """Immutable validated application configuration with 0.9 mapping compatibility."""

    web_run: Mapping[str, Any] = field(default_factory=lambda: _immutable_mapping({}))
    log: Mapping[str, Any] = field(default_factory=lambda: _immutable_mapping({}))
    postgres: Mapping[str, Any] = field(default_factory=lambda: _immutable_mapping({}))
    openapi_enabled: bool = False
    openapi: Mapping[str, Any] = field(default_factory=lambda: _immutable_mapping({}))
    hostname: str = ""
    cache_disabled: bool = False
    app_dir: str = "app"
    cors: CorsConfig = field(default_factory=CorsConfig)
    auth_url: str = ""
    auth_timeout_connect: float = 2.0
    auth_timeout_read: float = 5.0
    auth_timeout_total: float = 10.0
    shutdown_timeout: float = 30.0
    middlewares: tuple[str, ...] = ()
    extras: Mapping[str, Any] = field(default_factory=lambda: _immutable_mapping({}))
    _validation_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._validation_token is _APP_CONFIG_VALIDATION_TOKEN:
            object.__setattr__(self, "web_run", _immutable_mapping(self.web_run))
            object.__setattr__(self, "log", _immutable_mapping(self.log))
            object.__setattr__(self, "postgres", _immutable_mapping(self.postgres))
            object.__setattr__(self, "openapi", _immutable_mapping(self.openapi))
            object.__setattr__(self, "middlewares", tuple(self.middlewares))
            object.__setattr__(self, "extras", _immutable_mapping(self.extras))
            return

        normalized = type(self).from_mapping(self._compat())
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, getattr(normalized, name))

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> AppConfig:
        web_run = dict(values.get("web_run", {}))
        if "port" in web_run:
            web_run["port"] = _as_int(web_run["port"], setting="web_run.port", minimum=1)
            if web_run["port"] > 65535:
                raise ConfigurationError("web_run.port must be at most 65535")

        postgres = dict(values.get("postgres", {}))
        if "port" in postgres:
            postgres["port"] = _as_int(postgres["port"], setting="postgres.port", minimum=1)
        if "min_size" in postgres:
            postgres["min_size"] = _as_int(
                postgres["min_size"], setting="postgres.min_size", minimum=0
            )
        if "max_size" in postgres:
            postgres["max_size"] = _as_int(
                postgres["max_size"], setting="postgres.max_size", minimum=1
            )
        if postgres.get("min_size", 0) > postgres.get("max_size", float("inf")):
            raise ConfigurationError("postgres.min_size cannot exceed postgres.max_size")
        if "port" in postgres and postgres["port"] > 65535:
            raise ConfigurationError("postgres.port must be at most 65535")
        if "statement_cache_size" in postgres:
            postgres["statement_cache_size"] = _as_int(
                postgres["statement_cache_size"],
                setting="postgres.statement_cache_size",
                minimum=0,
            )
        if "max_inactive_connection_lifetime" in postgres:
            postgres["max_inactive_connection_lifetime"] = _as_float(
                postgres["max_inactive_connection_lifetime"],
                setting="postgres.max_inactive_connection_lifetime",
                minimum=-1.0,
            )

        openapi = dict(values.get("openapi", {}))
        openapi.setdefault("version", "1.0.0")
        openapi.setdefault("openapi_version", "3.0.3")

        cors_values = dict(values)
        nested_cors = values.get("cors")
        if isinstance(nested_cors, CorsConfig):
            cors = nested_cors
        else:
            if isinstance(nested_cors, Mapping):
                cors_values.update(nested_cors)
            cors = CorsConfig.from_mapping(cors_values)

        log = dict(values.get("log", {}))
        if "stackinfo" in log:
            log["stackinfo"] = _as_bool(log["stackinfo"], setting="log.stackinfo")
        if "stacklevel" in log:
            log["stacklevel"] = _as_int(log["stacklevel"], setting="log.stacklevel", minimum=0)

        auth_url = str(values.get("AUTH_URL", values.get("auth_url", "")))
        if auth_url:
            try:
                parsed_auth_url = URL(auth_url)
                split_auth_url = urlsplit(auth_url)
            except (TypeError, ValueError) as exc:
                raise ConfigurationError("AUTH_URL must be an absolute HTTP(S) URL") from exc
            if (
                _has_invalid_raw_characters(auth_url)
                or split_auth_url.netloc.endswith(":")
                or parsed_auth_url.scheme not in {"http", "https"}
                or not parsed_auth_url.host
            ):
                raise ConfigurationError("AUTH_URL must be an absolute HTTP(S) URL")

        middleware_values = values.get("middlewares", ())
        middlewares: tuple[str, ...]
        if middleware_values is None:
            middlewares = ()
        elif isinstance(middleware_values, str):
            middlewares = (middleware_values,)
        else:
            middlewares = tuple(str(item) for item in middleware_values)

        known_settings = {
            "web_run",
            "log",
            "postgres",
            "openapi_enabled",
            "openapi",
            "hostname",
            "AIOCACHE_DISABLE",
            "app_dir",
            "AUTH_URL",
            "auth_url",
            "AUTH_TIMEOUT_CONNECT",
            "AUTH_TIMEOUT_READ",
            "AUTH_TIMEOUT_TOTAL",
            "SHUTDOWN_TIMEOUT",
            "middlewares",
            "cors",
            "CORS_ALLOWED_ORIGINS",
            "cors_allowed_origins",
            "CORS_ALLOW_CREDENTIALS",
            "cors_allow_credentials",
            "CORS_ALLOW_HEADERS",
            "cors_allow_headers",
            "CORS_EXPOSE_HEADERS",
            "cors_expose_headers",
            "CORS_MAX_AGE",
            "cors_max_age",
            "DOMAIN",
            "domain",
        }
        extras = {key: item for key, item in values.items() if key not in known_settings}

        return cls(
            web_run=_immutable_mapping(web_run),
            log=_immutable_mapping(log),
            postgres=_immutable_mapping(postgres),
            openapi_enabled=_as_bool(
                values.get("openapi_enabled", False), setting="openapi_enabled"
            ),
            openapi=_immutable_mapping(openapi),
            hostname=str(values.get("hostname", "")),
            cache_disabled=_as_bool(
                values.get("AIOCACHE_DISABLE", False), setting="AIOCACHE_DISABLE"
            ),
            app_dir=str(values.get("app_dir", "app")),
            cors=cors,
            auth_url=auth_url,
            auth_timeout_connect=_as_float(
                values.get("AUTH_TIMEOUT_CONNECT", 2.0),
                setting="AUTH_TIMEOUT_CONNECT",
            ),
            auth_timeout_read=_as_float(
                values.get("AUTH_TIMEOUT_READ", 5.0), setting="AUTH_TIMEOUT_READ"
            ),
            auth_timeout_total=_as_float(
                values.get("AUTH_TIMEOUT_TOTAL", 10.0), setting="AUTH_TIMEOUT_TOTAL"
            ),
            shutdown_timeout=_as_float(
                values.get("SHUTDOWN_TIMEOUT", 30.0), setting="SHUTDOWN_TIMEOUT"
            ),
            middlewares=middlewares,
            extras=_immutable_mapping(extras),
            _validation_token=_APP_CONFIG_VALIDATION_TOKEN,
        )

    def _compat(self) -> dict[str, Any]:
        return {
            **self.extras,
            "web_run": self.web_run,
            "log": self.log,
            "postgres": self.postgres,
            "openapi_enabled": self.openapi_enabled,
            "openapi": self.openapi,
            "hostname": self.hostname,
            "AIOCACHE_DISABLE": self.cache_disabled,
            "app_dir": self.app_dir,
            "AUTH_URL": self.auth_url,
            "AUTH_TIMEOUT_CONNECT": self.auth_timeout_connect,
            "AUTH_TIMEOUT_READ": self.auth_timeout_read,
            "AUTH_TIMEOUT_TOTAL": self.auth_timeout_total,
            "SHUTDOWN_TIMEOUT": self.shutdown_timeout,
            "middlewares": self.middlewares,
            "CORS_ALLOWED_ORIGINS": self.cors.allowed_origins,
            "CORS_ALLOW_CREDENTIALS": self.cors.allow_credentials,
            "CORS_ALLOW_HEADERS": self.cors.allow_headers,
            "CORS_EXPOSE_HEADERS": self.cors.expose_headers,
            "CORS_MAX_AGE": self.cors.max_age,
        }

    def __getitem__(self, key: str) -> Any:
        return self._compat()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._compat())

    def __len__(self) -> int:
        return len(self._compat())


APP_CONFIG_KEY: web.AppKey[AppConfig] = web.AppKey("config", AppConfig)


async def load_config(
    environment: Mapping[str, str] | None = None,
) -> AppConfig:
    """Load deployment configuration exactly once when startup requests it."""
    source = os.environ if environment is None else environment
    env = Environment(source)
    values: dict[str, Any] = {
        "web_run": {
            "host": env.str("HOST"),
            "port": env.int("PORT"),
        },
        "log": {
            "level": env.str("LOG_LEVEL", default="INFO"),
            "format": env.str("LOG_FORMAT", default="json"),
            "stackinfo": env.bool("LOG_STACK_INFO", default=False),
            "stacklevel": env.int("LOG_STACK_LEVEL", default=3),
        },
        "postgres": {
            "database": env.str("DB_DATABASE"),
            "password": env.str("DB_PASSWORD"),
            "user": env.str("DB_USER"),
            "host": env.str("DB_HOST"),
            "port": env.int("DB_PORT"),
            "min_size": env.int("DB_MIN_CONNECTIONS", default=2),
            "max_size": env.int("DB_MAX_CONNECTIONS"),
            "statement_cache_size": env.int("DB_STATEMENT_CACHE_SIZE", default=0),
            "max_inactive_connection_lifetime": env.float(
                "DB_MAX_INACTIVE_CONNECTION_LIFETIME", default=300.0
            ),
        },
        "openapi_enabled": env.bool("OPENAPI", default=False),
        "openapi": {
            "title": env.str("OPENAPI_TITLE", default="/docs"),
            "version": env.str("APPLICATION_VERSION", default="1.0.0"),
            "url": env.str("OPENAPI_URL", default="/api/docs/swagger.json"),
            "swagger_path": env.str("OPENAPI_PATH", default="/docs"),
            "description": env.str("OPENAPI_DESCRIPTION", default=""),
            "openapi_version": env.str("OPENAPI_SPEC_VERSION", default="3.0.3"),
        },
        "hostname": env.str("HOSTNAME", default=""),
        "AIOCACHE_DISABLE": env.bool("AIOCACHE_DISABLE", default=False),
        "app_dir": env.str("APP_DIR", default="app"),
        "AUTH_URL": env.str("AUTH_URL", default=""),
        "AUTH_TIMEOUT_CONNECT": env.float("AUTH_TIMEOUT_CONNECT", default=2.0),
        "AUTH_TIMEOUT_READ": env.float("AUTH_TIMEOUT_READ", default=5.0),
        "AUTH_TIMEOUT_TOTAL": env.float("AUTH_TIMEOUT_TOTAL", default=10.0),
        "SHUTDOWN_TIMEOUT": env.float("SHUTDOWN_TIMEOUT", default=30.0),
        "CORS_ALLOWED_ORIGINS": env.str("CORS_ALLOWED_ORIGINS", default=""),
        "CORS_ALLOW_CREDENTIALS": env.bool("CORS_ALLOW_CREDENTIALS", default=False),
        "CORS_ALLOW_HEADERS": env.str(
            "CORS_ALLOW_HEADERS",
            default="Authorization,Content-Type,X-Requested-With,X-Request-ID",
        ),
        "CORS_EXPOSE_HEADERS": env.str("CORS_EXPOSE_HEADERS", default=""),
        "CORS_MAX_AGE": env.int("CORS_MAX_AGE", default=None),
    }
    if "DOMAIN" in source:
        values["DOMAIN"] = source["DOMAIN"]

    app_dir = str(values["app_dir"])
    module_name = f"{app_dir}.config"
    try:
        app_module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
    else:
        values.update(app_module.config)
    return AppConfig.from_mapping(values)


def get_config(name: str | None = None) -> Any:
    """Reject the legacy import-time configuration access pattern."""
    raise RuntimeError(
        "get_config() no longer loads deployment state implicitly; call "
        "await load_config() at startup and use app[APP_CONFIG_KEY]"
    )
