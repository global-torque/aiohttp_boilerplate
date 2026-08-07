"""Small asyncpg SQL adapter with bound values and explicit write scopes."""

from __future__ import annotations

import re
import traceback
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from aiohttp_boilerplate.sql import consts
from aiohttp_boilerplate.sql.exceptions import logger, logger_name

CUSTOM_TRACE = 5
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TABLE_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?"
    r"(?:\s+(?:AS\s+)?[A-Za-z_][A-Za-z0-9_]*)?\s*$",
    re.IGNORECASE,
)
FIELD_LIST_RE = re.compile(r"^[A-Za-z0-9_.*(),\s]+$")
ORDER_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*(?:\s+(?:ASC|DESC))?"
    r"(?:\s*,\s*[A-Za-z_][A-Za-z0-9_.]*(?:\s+(?:ASC|DESC))?)*$",
    re.IGNORECASE,
)


class SQLException(Exception):
    """Raised for invalid SQL adapter input."""


class RawSQL(str):
    """Explicitly reviewed structural SQL that cannot be represented as a value."""


def validate_identifier(value: str, *, kind: str = "identifier") -> str:
    """Validate a PostgreSQL identifier used in a structural position."""
    if not IDENTIFIER_RE.fullmatch(value):
        raise SQLException(f"Invalid {kind}: {value!r}")
    return value


def validate_table(value: str) -> str:
    """Validate a simple table name with an optional schema and alias."""
    if not TABLE_RE.fullmatch(value):
        raise SQLException(f"Invalid table expression: {value!r}")
    return value.strip()


def combine_bound_values(*groups: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve positional value order even when logical parameter names collide."""
    combined: dict[str, Any] = {}
    for group_index, group in enumerate(groups):
        for key, value in group.items():
            unique_key = key
            suffix = group_index
            while unique_key in combined:
                unique_key = f"{key}__{suffix}"
                suffix += 1
            combined[unique_key] = value
    return combined


class SQL:
    """Execute one SQL operation at a time against an asyncpg-like pool."""

    def __init__(self, table: str, db_pool: Any = None, log: Any = None) -> None:
        self.db_pool = db_pool
        self.table = validate_table(table)
        self.query = ""
        self.params: dict[str, Any] = {}
        self.conn: Any = None
        self._transaction_depth = 0
        self.log = logger if log is None else log
        if log is not None and hasattr(log, "with_component"):
            self.log = log.with_component(logger_name)

    def __str__(self) -> str:
        return f"{self.conn} {self.table} {self.query} ({len(self.params)} params)"

    async def get_connection(self) -> Any:
        if self.conn is None:
            if self.db_pool is None:
                raise SQLException("db_pool is not set")
            try:
                self.conn = await self.db_pool.acquire()
            except Exception:
                self.log.error("db pool lost connection")
                raise
        return self.conn

    def prepare_where(self, where: str, params: Mapping[str, Any], index: int = 0) -> str:
        """Replace named placeholders with positional asyncpg placeholders."""
        prepared = where
        for position, key in enumerate(params, start=index + 1):
            validate_identifier(key, kind="parameter name")
            prepared = prepared.replace(f"{{{key}}}", f"${position}")
        missing = re.findall(r"\{([^{}]+)\}", prepared)
        if missing:
            raise SQLException(f"Missing parameters {','.join(missing)}")
        return prepared

    def _log_query(self, operation: str, query: str, params: Mapping[str, Any]) -> None:
        self.log.debug(
            "sql query",
            extra={
                "sql_operation": operation,
                "sql_template": query,
                "sql_parameter_names": list(params),
                "sql_parameter_count": len(params),
            },
        )

    async def release(self) -> None:
        if self.conn is not None and self._transaction_depth == 0:
            await self.db_pool.release(self.conn)
            self.conn = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[SQL]:
        """Reuse one acquired connection for all operations in a transaction."""
        await self.get_connection()
        self._transaction_depth += 1
        try:
            async with self.conn.transaction():
                yield self
        finally:
            self._transaction_depth -= 1
            if self._transaction_depth == 0:
                await self.release()

    async def execute(
        self,
        query: str,
        params: Mapping[str, Any],
        fetch_method: int = consts.EXECUTE,
    ) -> Any:
        methods = {
            consts.EXECUTE: "execute",
            consts.FETCH: "fetch",
            consts.FETCHROW: "fetchrow",
            consts.FETCHVAL: "fetchval",
        }
        if fetch_method not in methods:
            raise SQLException(f"Unsupported fetch method: {fetch_method!r}")
        copied = dict(params)
        self.query = query
        self.params = copied
        await self.get_connection()
        try:
            self._log_query(methods[fetch_method], query, copied)
            call = getattr(self.conn, methods[fetch_method])
            return await call(query, *copied.values())
        finally:
            await self.release()

    async def select(
        self,
        fields: str = "*",
        join: str | RawSQL = "",
        where: str = "",
        order: str = "",
        limit: int | str | None = None,
        offset: int | None = None,
        params: Mapping[str, Any] | None = None,
        many: bool = False,
    ) -> Any:
        copied = dict(params or {})
        if not FIELD_LIST_RE.fullmatch(fields):
            raise SQLException("fields contains unsupported structural SQL")
        if join and not isinstance(join, RawSQL):
            raise SQLException("join must be wrapped in RawSQL after review")
        if order and not ORDER_RE.fullmatch(order):
            raise SQLException("order contains unsupported structural SQL")
        if limit not in {None, ""}:
            try:
                limit = int(limit)
            except (TypeError, ValueError) as exc:
                raise SQLException("limit must be a non-negative integer") from exc
            if limit < 0:
                raise SQLException("limit must be a non-negative integer")
        if offset is not None and (not isinstance(offset, int) or offset < 0):
            raise SQLException("offset must be a non-negative integer")

        query = f"select {fields} from {self.table}"
        if join:
            query += f" {join}" if "join" in join.lower() else f" join {join}"
        if where:
            query += f" where {self.prepare_where(where, copied)}"
        if order:
            query += f" order by {order}"
        if limit not in {None, ""}:
            query += f" limit {limit}"
        if offset is not None:
            query += f" offset {offset}"
        self.query = query
        self.params = copied
        await self.get_connection()
        try:
            self._log_query("select", query, copied)
            if self.log.isEnabledFor(CUSTOM_TRACE):
                self.log.warning("\n".join(str(line) for line in traceback.extract_stack()))
            statement = await self.conn.prepare(query)
            method = statement.fetch if many else statement.fetchrow
            return await method(*copied.values())
        finally:
            await self.release()

    async def insert(self, data: Mapping[str, Any]) -> Any:
        copied = dict(data)
        on_conflict = copied.pop("__on_conflict", "")
        returning = str(copied.pop("__returning", "*"))
        if not copied:
            raise SQLException("insert data cannot be empty")
        for key in copied:
            validate_identifier(key, kind="column")
        if on_conflict and not isinstance(on_conflict, RawSQL):
            raise SQLException("__on_conflict must be wrapped in RawSQL after review")
        if not FIELD_LIST_RE.fullmatch(returning):
            raise SQLException("__returning contains unsupported structural SQL")
        placeholders = ",".join(f"${index}" for index in range(1, len(copied) + 1))
        query = (
            f"insert into {self.table}({','.join(copied)}) values({placeholders}) "
            f"{on_conflict} RETURNING {returning}"
        )
        return await self.execute(query, copied, consts.FETCHROW)

    async def update(self, where: str, params: Mapping[str, Any], data: Mapping[str, Any]) -> int:
        copied_data = dict(data)
        copied_params = dict(params)
        if not where.strip() or not copied_params:
            raise SQLException("update requires a non-empty scope")
        if not copied_data:
            raise SQLException("update data cannot be empty")
        for key in copied_data:
            validate_identifier(key, kind="column")
        assignments = ",".join(f"{key}=${index}" for index, key in enumerate(copied_data, start=1))
        query = f"update {self.table} set {assignments}"
        query += f" where {self.prepare_where(where, copied_params, len(copied_data))}"
        bound = combine_bound_values(copied_data, copied_params)
        result = await self.execute(query, bound)
        return int(result.removeprefix("UPDATE "))

    async def update_all(self, data: Mapping[str, Any]) -> int:
        """Explicitly update every row."""
        copied = dict(data)
        if not copied:
            raise SQLException("update data cannot be empty")
        for key in copied:
            validate_identifier(key, kind="column")
        assignments = ",".join(f"{key}=${index}" for index, key in enumerate(copied, start=1))
        result = await self.execute(f"update {self.table} set {assignments}", copied)
        return int(result.removeprefix("UPDATE "))

    async def delete(self, where: str, params: Mapping[str, Any]) -> int:
        copied = dict(params)
        if not where.strip() or not copied:
            raise SQLException("delete requires a non-empty scope")
        query = f"delete from {self.table} where {self.prepare_where(where, copied)}"
        result = await self.execute(query, copied)
        return int(result.removeprefix("DELETE "))

    async def delete_all(self) -> int:
        """Explicitly delete every row."""
        result = await self.execute(f"delete from {self.table}", {})
        return int(result.removeprefix("DELETE "))

    async def get_count(self, where: str = "", params: Mapping[str, Any] | None = None) -> int:
        copied = dict(params or {})
        query = f"select count(*) as count from {self.table}"
        if where:
            query += f" where {self.prepare_where(where, copied)}"
        return int(await self.execute(query, copied, consts.FETCHVAL))

    async def is_exists(self, where: str, params: Mapping[str, Any]) -> bool:
        copied = dict(params)
        if not where.strip() or not copied:
            raise SQLException("is_exists requires a non-empty scope")
        query = f"SELECT 1 as t FROM {self.table} WHERE {self.prepare_where(where, copied)}"
        return await self.execute(query, copied, consts.FETCHVAL) is not None
