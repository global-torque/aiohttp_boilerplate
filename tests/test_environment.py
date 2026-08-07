from __future__ import annotations

import pytest

from aiohttp_boilerplate.config import ConfigurationError, Environment, load_config


@pytest.mark.parametrize("value", [None, ""])
def test_required_string_rejects_missing_and_empty_values(value: str | None) -> None:
    source = {} if value is None else {"WALLET_API": value}

    with pytest.raises(
        ConfigurationError,
        match="Required environment variable WALLET_API is missing or empty",
    ):
        Environment(source).str("WALLET_API")


@pytest.mark.parametrize("value", [None, ""])
def test_missing_and_empty_values_use_defaults(value: str | None) -> None:
    source = {} if value is None else {"SETTING": value}
    env = Environment(source)

    assert env.str("SETTING", default="fallback") == "fallback"
    assert env.bool("SETTING", default=True) is True
    assert env.int("SETTING", default=7) == 7
    assert env.float("SETTING", default=2.5) == 2.5
    assert env.list("SETTING", default=["fallback"]) == ["fallback"]


def test_environment_reads_supported_types() -> None:
    env = Environment(
        {
            "TEXT": "  preserved  ",
            "ENABLED": "yes",
            "COUNT": "12",
            "RATIO": "1.25",
            "HOSTS": "api.example.com, admin.example.com,,",
        }
    )

    assert env.str("TEXT") == "  preserved  "
    assert env.bool("ENABLED") is True
    assert env.int("COUNT") == 12
    assert env.float("RATIO") == 1.25
    assert env.list("HOSTS") == ["api.example.com", "admin.example.com"]


@pytest.mark.parametrize(
    ("method", "value", "message"),
    [
        ("bool", "sometimes", "ENABLED must be a boolean"),
        ("bool", "   ", "ENABLED must be a boolean"),
        ("int", "twelve", "COUNT must be an integer"),
        ("float", "many", "RATIO must be a number"),
        ("float", "nan", "RATIO must be finite"),
    ],
)
def test_environment_rejects_invalid_typed_values(method: str, value: str, message: str) -> None:
    name = {
        "bool": "ENABLED",
        "int": "COUNT",
        "float": "RATIO",
    }[method]
    reader = Environment({name: value})

    with pytest.raises(ConfigurationError, match=message):
        getattr(reader, method)(name)


@pytest.mark.parametrize("default", [float("nan"), float("inf"), float("-inf")])
def test_environment_rejects_non_finite_float_defaults(default: float) -> None:
    with pytest.raises(ConfigurationError, match="RATIO must be finite"):
        Environment({}).float("RATIO", default=default)


def test_list_defaults_are_copied() -> None:
    default = ["localhost"]

    result = Environment({}).list("HOSTS", default=default)

    assert result == default
    assert result is not default


@pytest.mark.asyncio
async def test_load_config_treats_empty_optional_values_as_unset() -> None:
    config = await load_config(
        {
            "HOST": "127.0.0.1",
            "PORT": "8085",
            "DB_DATABASE": "app",
            "DB_PASSWORD": "secret",
            "DB_USER": "app",
            "DB_HOST": "postgres",
            "DB_PORT": "5432",
            "DB_MAX_CONNECTIONS": "5",
            "APP_DIR": "tests",
            "DB_MIN_CONNECTIONS": "",
            "AUTH_TIMEOUT_CONNECT": "",
            "OPENAPI": "",
            "CORS_ALLOW_HEADERS": "",
        }
    )

    assert config.postgres["min_size"] == 2
    assert config.auth_timeout_connect == 2.0
    assert config.openapi_enabled is False
    assert config.cors.allow_headers == (
        "Authorization",
        "Content-Type",
        "X-Requested-With",
        "X-Request-ID",
    )
