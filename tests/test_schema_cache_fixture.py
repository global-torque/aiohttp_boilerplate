from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp_apispec import request_schema, response_schema, setup_aiohttp_apispec
from marshmallow import Schema, ValidationError, fields, post_dump

from aiohttp_boilerplate.schemas.fields import Choice, JoinNested
from aiohttp_boilerplate.schemas.validators import DateRangeYears
from aiohttp_boilerplate.test_utils.load_fixtures import LoadFixture
from aiohttp_boilerplate.views.mixins import CacheMixin
from aiohttp_boilerplate.views.options import ObjectView, SchemaOptionsView


class AddressSchema(Schema):
    id = fields.Int(dump_only=True)
    city = fields.Str()


class ProfileSchema(Schema):
    id = fields.Int(dump_only=True)
    address = JoinNested(
        nested=AddressSchema,
        table="addresses",
        joinOn="id = t1.address_id",
        joinType="LEFT JOIN",
    )


class AccountSchema(Schema):
    id = fields.Int(dump_only=True)
    secret_input = fields.Str(load_only=True)
    public_name = fields.Str(attribute="db_name", data_key="name")
    ignored = fields.Str(metadata={"db_field": False})
    expression = fields.Str(metadata={"db_field": "lower({t_index}.email)"})
    profile = JoinNested(
        nested=ProfileSchema,
        table="profiles",
        joinOn="id = t0.profile_id",
        joinType="LEFT JOIN",
    )
    billing_address = JoinNested(
        nested=AddressSchema,
        table="addresses",
        joinOn="id = t0.billing_address_id",
        joinType="LEFT JOIN",
    )
    constant = fields.Constant("account")
    computed = fields.Method("get_computed")

    def get_computed(self, value: Any) -> str:
        return "computed"

    @post_dump
    def marker(self, data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        data["serialized"] = True
        return data


class MarshmallowCompatibilitySchema(Schema):
    status = Choice(["pending", "active"], required=True)
    started_on = fields.Date(validate=DateRangeYears(2020, 2025), required=True)
    address = JoinNested(
        nested=AddressSchema,
        table="addresses",
        joinOn="id = t0.address_id",
        joinType="LEFT JOIN",
        required=True,
    )


def schema_view() -> SchemaOptionsView:
    return object.__new__(SchemaOptionsView)


def test_default_get_schema_warns_without_logging_error(capsys) -> None:
    with pytest.warns(RuntimeWarning, match="Redefine get_schema"):
        assert schema_view().get_schema() is None
    assert capsys.readouterr().err == ""


def test_schema_fields_respect_db_contract_and_unique_join_aliases() -> None:
    result = schema_view().add_fields_from_schema(AccountSchema())

    assert "t0.id as t0__id" in result["fields"]
    assert "t0.db_name as t0__public_name" in result["fields"]
    assert "lower(t0.email) as t0__expression" in result["fields"]
    assert "secret_input" not in result["fields"]
    assert "ignored" not in result["fields"]
    assert "constant" not in result["fields"]
    assert "computed" not in result["fields"]
    assert set(result["aliases"]) == {"t0", "t1", "t2", "t3"}
    assert "profiles as t1 on t1.id = t0.profile_id" in result["sql_tables"]
    assert "addresses as t2 on t2.id = t1.address_id" in result["sql_tables"]
    assert "addresses as t3 on t3.id = t0.billing_address_id" in result["sql_tables"]


@pytest.mark.asyncio
async def test_schema_dump_honors_attribute_data_key_hooks_and_computed_fields() -> None:
    view = object.__new__(ObjectView)
    view.schema = AccountSchema
    view._request = SimpleNamespace(log=SimpleNamespace(debug=lambda *args, **kwargs: None))
    obj = SimpleNamespace(
        data={
            "id": 7,
            "db_name": "Investment account",
            "expression": "owner@example.com",
            "profile": {"id": 3, "address": {"id": 5, "city": "Madrid"}},
            "billing_address": {"id": 6, "city": "Valencia"},
        }
    )

    dumped = await ObjectView.get_data(view, obj)

    assert dumped["name"] == "Investment account"
    assert dumped["constant"] == "account"
    assert dumped["computed"] == "computed"
    assert dumped["serialized"] is True
    assert "secret_input" not in dumped


def test_join_nested_requires_both_structural_values() -> None:
    with pytest.raises(ValueError, match="both table and joinOn"):
        JoinNested(nested=AddressSchema, table="addresses")
    with pytest.raises(ValueError, match="both table and joinOn"):
        JoinNested(nested=AddressSchema, joinOn="id = t0.address_id")


def test_marshmallow_four_load_dump_validation_and_json_schema() -> None:
    schema = MarshmallowCompatibilitySchema()
    loaded = schema.load(
        {
            "status": "active",
            "started_on": "2024-06-30",
            "address": {"city": "Madrid"},
        }
    )

    assert loaded == {
        "status": "active",
        "started_on": date(2024, 6, 30),
        "address": {"city": "Madrid"},
    }
    assert schema.dump(loaded) == {
        "status": "active",
        "started_on": "2024-06-30",
        "address": {"city": "Madrid"},
    }
    with pytest.raises(ValidationError):
        schema.load(
            {
                "status": "unknown",
                "started_on": "2026-01-01",
                "address": {"city": "Madrid"},
            }
        )

    generated = schema_view().json_schema(schema)
    assert generated["$ref"] == "#/definitions/MarshmallowCompatibilitySchema"
    compatibility_definition = generated["definitions"]["MarshmallowCompatibilitySchema"]
    assert compatibility_definition["type"] == "object"
    assert compatibility_definition["properties"]["address"]["$ref"] == (
        "#/definitions/AddressSchema"
    )


def test_marshmallow_four_openapi_generation_uses_component_schemas() -> None:
    @request_schema(MarshmallowCompatibilitySchema)
    @response_schema(AddressSchema, 200, description="Created")
    async def create_address(_request: web.Request) -> web.Response:
        return web.json_response({"city": "Madrid"})

    app = web.Application()
    app.router.add_post("/addresses", create_address)
    setup_aiohttp_apispec(app, in_place=True, openapi_version="3.0.3")
    document = app["swagger_dict"]

    operation = document["paths"]["/addresses"]["post"]
    request_reference = operation["requestBody"]["content"]["application/json"]["schema"]
    response_reference = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert request_reference == {"$ref": "#/components/schemas/MarshmallowCompatibility"}
    assert response_reference == {"$ref": "#/components/schemas/Address"}


class RetrievalBase:
    calls = 0

    async def _get(self) -> dict[str, int]:
        type(self).calls += 1
        return {"calls": type(self).calls}


class CachedRetrieval(CacheMixin, RetrievalBase):
    namespace = "accounts"
    cache_ttl = 60
    order_key = "sort"


class ExpiringCachedRetrieval(CachedRetrieval):
    cache_ttl = 0


@pytest.mark.asyncio
async def test_cache_is_identity_aware_canonical_and_bypassed(aiohttp_client) -> None:
    CachedRetrieval.calls = 0

    async def retrieve(request: web.Request) -> web.Response:
        endpoint = CachedRetrieval()
        endpoint.request = request
        endpoint.cache_identity = lambda req: req.headers["X-User"]
        return web.Response(text=await endpoint._get(), content_type="application/json")

    async def mutate(request: web.Request) -> web.Response:
        endpoint = CachedRetrieval()
        endpoint.request = request
        endpoint.cache_identity = lambda req: req.headers["X-User"]
        return web.Response(text=await endpoint._get(), content_type="application/json")

    app = web.Application()
    app.router.add_get("/accounts", retrieve)
    app.router.add_post("/accounts", mutate)
    client = await aiohttp_client(app)

    first = await client.get("/accounts?tag=b&tag=a", headers={"X-User": "alice"})
    reordered = await client.get("/accounts?tag=a&tag=b", headers={"X-User": "alice"})
    bob = await client.get("/accounts?tag=a&tag=b", headers={"X-User": "bob"})
    skipped_one = await client.get("/accounts?skip=1", headers={"X-User": "alice"})
    skipped_two = await client.get("/accounts?skip=1", headers={"X-User": "alice"})
    mutation_one = await client.post("/accounts", headers={"X-User": "alice"})
    mutation_two = await client.post("/accounts", headers={"X-User": "alice"})
    random_one = await client.get("/accounts?sort=random", headers={"X-User": "alice"})
    random_two = await client.get("/accounts?sort=random", headers={"X-User": "alice"})

    assert await first.json() == await reordered.json()
    assert await bob.json() != await first.json()
    assert await skipped_one.json() != await skipped_two.json()
    assert await mutation_one.json() != await mutation_two.json()
    assert await random_one.json() != await random_two.json()
    assert CachedRetrieval.calls == 8


@pytest.mark.asyncio
async def test_cache_none_identity_bypasses_lookup_and_storage(aiohttp_client) -> None:
    CachedRetrieval.calls = 0

    async def retrieve(request: web.Request) -> web.Response:
        endpoint = CachedRetrieval()
        endpoint.request = request
        endpoint.cache_identity = lambda _request: None
        return web.Response(text=await endpoint._get(), content_type="application/json")

    app = web.Application()
    app.router.add_get("/accounts", retrieve)
    client = await aiohttp_client(app)
    first = await client.get("/accounts")
    second = await client.get("/accounts")

    assert await first.json() != await second.json()
    assert CachedRetrieval.calls == 2


@pytest.mark.asyncio
async def test_cache_response_is_compact_on_miss_hit_expiry_and_bypass(aiohttp_client) -> None:
    CachedRetrieval.calls = 0
    ExpiringCachedRetrieval.calls = 0

    async def retrieve(request: web.Request) -> web.Response:
        endpoint_class = (
            ExpiringCachedRetrieval if request.match_info["mode"] == "expiring" else CachedRetrieval
        )
        endpoint = endpoint_class()
        endpoint.request = request
        endpoint.cache_identity = lambda _request: "alice"
        return web.Response(text=await endpoint._get(), content_type="application/json")

    app = web.Application()
    app.router.add_get("/{mode}", retrieve)
    client = await aiohttp_client(app)

    miss = await client.get("/cached")
    hit = await client.get("/cached")
    expired_once = await client.get("/expiring")
    expired_twice = await client.get("/expiring")
    bypassed = await client.get("/cached?skip=1")

    assert await miss.text() == '{"calls":1}'
    assert await hit.text() == '{"calls":1}'
    assert await expired_once.text() == '{"calls":1}'
    assert await expired_twice.text() == '{"calls":2}'
    assert await bypassed.text() == '{"calls":2}'


class FixtureConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.prepared: list[str] = []
        self.rows: list[tuple[Any, ...]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "OK"

    async def prepare(self, query: str) -> FixtureConnection:
        self.prepared.append(query)
        return self

    async def fetch(self, *args: Any) -> None:
        self.rows.append(args)


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [{"id": 4, "name": "O'Brien"}]])
async def test_fixture_empty_and_populated_sequence_reset(
    tmp_path: Path, rows: list[dict[str, Any]]
) -> None:
    source = tmp_path / "01_widgets.json"
    source.write_text(json.dumps(rows), encoding="utf-8")
    connection = FixtureConnection()
    fixture = LoadFixture(source.name, str(tmp_path))

    await fixture.file2db(connection)

    sequence_query, args = connection.executed[-1]
    assert "COALESCE(MAX(id), 1)" in sequence_query
    assert "MAX(id) IS NOT NULL" in sequence_query
    assert args == ("widgets",)
    assert rows == json.loads(source.read_text(encoding="utf-8"))
    assert len(connection.rows) == len(rows)
