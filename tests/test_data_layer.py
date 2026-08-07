from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import pytest

from aiohttp_boilerplate.models import JsonbManager, Manager
from aiohttp_boilerplate.sql import SQL, SQLException
from aiohttp_boilerplate.views.request import RequestContext, RequestLoggerAdapter


class FakeTransaction:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeTransaction:
        self.connection.events.append("begin")
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.connection.events.append("rollback" if exc else "commit")


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self.events: list[str] = []
        self.next_row: Mapping[str, Any] | None = {"id": 1}
        self.next_rows: list[Mapping[str, Any]] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append(("execute", query, args))
        return "DELETE 1" if query.lower().startswith("delete") else "UPDATE 1"

    async def fetch(self, query: str, *args: Any) -> list[Mapping[str, Any]]:
        self.calls.append(("fetch", query, args))
        return self.next_rows

    async def fetchrow(self, query: str, *args: Any) -> Mapping[str, Any] | None:
        self.calls.append(("fetchrow", query, args))
        return self.next_row

    async def fetchval(self, query: str, *args: Any) -> int:
        self.calls.append(("fetchval", query, args))
        return 1

    async def prepare(self, query: str) -> FakePrepared:
        return FakePrepared(self, query)


class FakePrepared:
    def __init__(self, connection: FakeConnection, query: str) -> None:
        self.connection = connection
        self.query = query

    async def fetchrow(self, *args: Any) -> Mapping[str, Any] | None:
        return await self.connection.fetchrow(self.query, *args)

    async def fetch(self, *args: Any) -> list[Mapping[str, Any]]:
        return await self.connection.fetch(self.query, *args)


class FakePool:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.acquired = 0
        self.released = 0
        self.closed = 0

    async def acquire(self) -> FakeConnection:
        self.acquired += 1
        return self.connection

    async def release(self, connection: FakeConnection) -> None:
        assert connection is self.connection
        self.released += 1

    async def close(self) -> None:
        self.closed += 1


class Widget(Manager):
    __table__ = "widgets"


class WidgetDocument(JsonbManager):
    __table__ = "widgets"
    __key_name__ = "document"


@pytest.mark.asyncio
async def test_sql_rejects_unscoped_writes_before_pool_acquisition() -> None:
    pool = FakePool()
    sql = SQL("widgets", pool)

    with pytest.raises(SQLException, match="non-empty scope"):
        await sql.update("", {}, {"name": "unsafe"})
    with pytest.raises(SQLException, match="non-empty scope"):
        await sql.delete("", {})
    with pytest.raises(SQLException, match="cannot be empty"):
        await sql.update("id={id}", {"id": 1}, {})

    assert pool.acquired == 0


@pytest.mark.asyncio
async def test_manager_scopes_id_zero_and_combines_predicate_with_and() -> None:
    pool = FakePool()
    widget = Widget(pool)
    widget.set_data({"id": 0, "name": "before"})

    params = {"tenant_id": 9}
    await widget.update(where="tenant_id={tenant_id}", params=params, data={"name": "after"})

    _, query, args = pool.connection.calls[-1]
    assert query == "update widgets as t0 set name=$1 where (tenant_id=$2) AND id=$3"
    assert args == ("after", 9, 0)
    assert params == {"tenant_id": 9}


@pytest.mark.asyncio
async def test_manager_object_id_does_not_overwrite_predicate_id_parameter() -> None:
    pool = FakePool()
    widget = Widget(pool)
    widget.set_data({"id": 7, "name": "before"})
    params = {"id": 99}

    await widget.update(where="parent_id={id}", params=params, data={"name": "after"})

    _, query, args = pool.connection.calls[-1]
    assert query == ("update widgets as t0 set name=$1 " "where (parent_id=$2) AND id=$3")
    assert args == ("after", 99, 7)
    assert params == {"id": 99}


@pytest.mark.asyncio
async def test_sql_preserves_duplicate_data_and_scope_parameter_names() -> None:
    pool = FakePool()

    await SQL("widgets", pool).update("id={id}", {"id": 7}, {"id": 8})

    _, query, args = pool.connection.calls[-1]
    assert query == "update widgets set id=$1 where id=$2"
    assert args == (8, 7)


@pytest.mark.asyncio
async def test_manager_get_by_without_filters_does_not_acquire() -> None:
    pool = FakePool()
    with pytest.raises(SQLException, match="at least one filter"):
        await Widget(pool).get_by()
    assert pool.acquired == 0


@pytest.mark.asyncio
async def test_jsonb_update_binds_path_payload_and_preserves_inputs() -> None:
    pool = FakePool()
    manager = WidgetDocument(pool)
    malicious_index = "0}'); DELETE FROM accounts; --"
    params = {"tenant_id": 7, "index": malicious_index}
    payload = {"owner": "O'Brien", "nested": {"sql": "'; DROP TABLE widgets; --"}}

    await manager.update("tenant_id={tenant_id}", params, payload)

    _, query, args = pool.connection.calls[-1]
    assert query == (
        "UPDATE widgets as t0 SET document=jsonb_set(document, $1::text[], $2::jsonb) "
        "WHERE tenant_id=$3"
    )
    assert args[0] == [malicious_index]
    assert args[1] == payload
    assert malicious_index not in query
    assert "DROP TABLE" not in query
    assert params == {"tenant_id": 7, "index": malicious_index}
    assert payload["owner"] == "O'Brien"


@pytest.mark.asyncio
async def test_jsonb_rejects_unscoped_write_without_acquisition() -> None:
    pool = FakePool()
    with pytest.raises(SQLException, match="non-empty scope"):
        await WidgetDocument(pool).insert("", {}, {"name": "unsafe"})
    assert pool.acquired == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword_call", [False, True])
async def test_jsonb_insert_preserves_legacy_and_keyword_call_shapes(
    keyword_call: bool,
) -> None:
    pool = FakePool()
    manager = WidgetDocument(pool)
    payload = {"name": "safe"}

    if keyword_call:
        updated = await manager.insert(
            data=payload,
            where="tenant_id={tenant_id}",
            params={"tenant_id": 7},
        )
    else:
        updated = await manager.insert("tenant_id={tenant_id}", {"tenant_id": 7}, payload)

    assert updated == 1
    _, query, args = pool.connection.calls[-1]
    assert query == "UPDATE widgets as t0 SET document=document || $1::jsonb WHERE tenant_id=$2"
    assert args == (payload, 7)


@pytest.mark.asyncio
async def test_model_list_reload_replaces_data_and_iterator_yields_models() -> None:
    pool = FakePool()
    widgets = Widget(pool, is_list=True)
    widgets.set_data([{"id": 1}, {"id": 2}])
    widgets.set_data([{"id": 3}])

    result = list(widgets)
    assert [item.id for item in result] == [3]
    assert all(isinstance(item, Widget) for item in result)


@pytest.mark.asyncio
async def test_transaction_reuses_connection_and_releases_once() -> None:
    pool = FakePool()
    sql = SQL("widgets", pool)

    async with sql.transaction():
        await sql.update("id={id}", {"id": 1}, {"name": "one"})
        await sql.update("id={id}", {"id": 2}, {"name": "two"})

    assert pool.acquired == 1
    assert pool.released == 1
    assert pool.connection.events == ["begin", "commit"]


@pytest.mark.asyncio
async def test_select_supports_request_logger_adapter_and_releases_connection() -> None:
    pool = FakePool()
    log = RequestLoggerAdapter(logging.getLogger("test.sql"), RequestContext("request-id"))

    result = await SQL("widgets", pool, log=log).select(where="id={id}", params={"id": 1})

    assert result == {"id": 1}
    assert pool.acquired == 1
    assert pool.released == 1


@pytest.mark.asyncio
async def test_invalid_fetch_mode_rejects_before_acquisition() -> None:
    pool = FakePool()
    with pytest.raises(SQLException, match="Unsupported fetch method"):
        await SQL("widgets", pool).execute("select 1", {}, "fetch_secret")
    assert pool.acquired == 0
