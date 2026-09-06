from __future__ import annotations

import importlib

import pytest
from aiohttp import web
from marshmallow import Schema, fields, validate

from aiohttp_boilerplate.bootstrap.web_app import create_app
from aiohttp_boilerplate.config import AppConfig
from aiohttp_boilerplate.views.options import SchemaOptionsView

bootstrap = importlib.import_module("aiohttp_boilerplate.bootstrap.web_app")
ORIGIN = "https://app.example.com"
PATH = "/investment/1/cancel"


class Cancellation(Schema):
    cancelation_reason = fields.String(required=True, validate=validate.Length(min=4))


class CancelView(SchemaOptionsView):
    def get_schema(self) -> type[Schema]:
        return Cancellation

    async def on_start(self) -> None:
        raise AssertionError("OPTIONS must not authenticate or load investment data")

    async def put(self) -> web.Response:
        return web.json_response({"updated": True})


def make_app(monkeypatch, *, cors=True):
    def routes(config, app):
        app.router.add_view("/investment/{id}/cancel", CancelView)

    monkeypatch.setattr(bootstrap, "_load_routes", routes)
    monkeypatch.setattr(bootstrap, "_load_optional_setup", lambda *args: None)
    config = AppConfig.from_mapping(
        {
            "app_dir": "unused",
            "CORS_ALLOWED_ORIGINS": ORIGIN if cors else "",
            "CORS_ALLOW_CREDENTIALS": True,
            "CORS_EXPOSE_HEADERS": "X-Request-ID",
        }
    )
    return create_app(config, db_pool=object(), auth_session=object())


async def assert_schema(response):
    assert response.status == 200
    assert response.content_type == "application/json"
    document = await response.json()
    schema = document["definitions"]["Cancellation"]
    assert document["$ref"] == "#/definitions/Cancellation"
    assert schema["required"] == ["cancelation_reason"]
    assert schema["properties"]["cancelation_reason"]["type"] == "string"
    assert schema["properties"]["cancelation_reason"]["minLength"] == 4


@pytest.mark.parametrize("cors", [False, True])
async def test_plain_options_returns_schema_without_preflight_headers(
    aiohttp_client, monkeypatch, cors
):
    client = await aiohttp_client(make_app(monkeypatch, cors=cors))
    await assert_schema(await client.options(PATH))
    response = await client.put(PATH)
    assert response.status == 200
    assert await response.json() == {"updated": True}
    for method in ("GET", "PATCH", "POST", "DELETE", "HEAD"):
        assert (await client.request(method, PATH)).status == 405


async def test_browser_options_and_preflight_return_schema(aiohttp_client, monkeypatch):
    client = await aiohttp_client(make_app(monkeypatch))
    # A browser can preflight its explicit OPTIONS request before requesting the schema.
    for method in ("PUT", "OPTIONS"):
        response = await client.options(
            PATH,
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        await assert_schema(response)
        assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
        assert response.headers["Access-Control-Allow-Credentials"] == "true"
        assert response.headers["Access-Control-Allow-Methods"] == method
        assert response.headers["Access-Control-Allow-Headers"] == "CONTENT-TYPE"

    response = await client.options(PATH, headers={"Origin": ORIGIN})
    await assert_schema(response)
    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert response.headers["Access-Control-Expose-Headers"] == "X-Request-ID"


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://untrusted.example", "Access-Control-Request-Method": "PUT"},
        {"Origin": ORIGIN, "Access-Control-Request-Method": "DELETE"},
        {"Access-Control-Request-Method": "PUT"},
        {
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "X-Not-Allowed",
        },
    ],
)
async def test_invalid_preflight_is_still_rejected(aiohttp_client, monkeypatch, headers):
    client = await aiohttp_client(make_app(monkeypatch))
    response = await client.options(PATH, headers=headers)
    assert response.status == 403
    assert "definitions" not in await response.json()


async def test_untrusted_origin_does_not_get_cors_headers(aiohttp_client, monkeypatch):
    client = await aiohttp_client(make_app(monkeypatch))
    response = await client.options(PATH, headers={"Origin": "https://untrusted.example"})
    await assert_schema(response)
    assert "Access-Control-Allow-Origin" not in response.headers


async def test_get_companion_can_reuse_options_without_duplicate_cors_headers(
    aiohttp_client, monkeypatch
):
    class SchemaGet(CancelView):
        async def get(self):
            return await self.options()

    def routes(config, app):
        app.router.add_get("/schema", SchemaGet)

    app = make_app(monkeypatch)
    monkeypatch.setattr(bootstrap, "_load_routes", routes)
    app = create_app(app.conf, db_pool=object(), auth_session=object())
    client = await aiohttp_client(app)
    response = await client.get("/schema", headers={"Origin": ORIGIN})
    await assert_schema(response)
    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
