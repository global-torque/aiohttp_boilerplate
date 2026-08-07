"""Database-backed models with explicit write scopes."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, cast

from aiohttp import web

from aiohttp_boilerplate.sql import (
    SQL,
    SQLException,
    combine_bound_values,
    validate_identifier,
)
from aiohttp_boilerplate.views import JSONHTTPError


class Manager:
    """Thin model wrapper around :class:`SQL`."""

    __table__: str

    def __init__(
        self, db_pool: Any, is_list: bool = False, storage: type[SQL] | None = None, log: Any = None
    ) -> None:
        object.__setattr__(self, "log", log)
        object.__setattr__(self, "is_list", is_list)
        object.__setattr__(self, "db_pool", db_pool)
        object.__setattr__(self, "table", f"{self.__table__} as t0")
        object.__setattr__(self, "data", [] if is_list else {})
        self.set_storage(self.table, storage, db_pool)

    def items(self) -> Any:
        if not isinstance(self.data, dict):
            raise TypeError("list managers do not expose items()")
        return self.data.items()

    def __getitem__(self, key: Any) -> Any:
        return self.data[key]

    def __getattr__(self, key: str) -> Any:
        data = object.__getattribute__(self, "data")
        if isinstance(data, dict):
            return data.get(key)
        raise AttributeError(key)

    def __setattr__(self, key: str, value: Any) -> None:
        if key in {"log", "table", "sql", "is_list", "data", "db_pool"} or not hasattr(
            self, "data"
        ):
            object.__setattr__(self, key, value)
            return
        if self.is_list:
            raise AttributeError("cannot set model fields on a list manager")
        if key not in self.data:
            raise AttributeError(f"{key} property does not exist; call set_data() first")
        self.data[key] = value

    def set_storage(self, table: str, storage: type[SQL] | None, db_pool: Any) -> None:
        storage_type = SQL if storage is None else storage
        object.__setattr__(self, "sql", storage_type(table, db_pool, log=self.log))

    def set_data(self, data: Any = None) -> None:
        """Replace model state, including empty reload results."""
        if self.is_list:
            records = [] if data is None else data
            if isinstance(records, Mapping) or not isinstance(records, Sequence):
                raise TypeError("list manager data must be a sequence of records")
            models: list[Manager] = []
            for record in records:
                new_obj = self.__class__(db_pool=self.db_pool, log=self.log)
                new_obj.set_data(dict(record))
                models.append(new_obj)
            object.__setattr__(self, "data", models)
            return
        if data is None:
            object.__setattr__(self, "data", {})
            return
        try:
            copied = dict(data)
        except (TypeError, ValueError) as exc:
            raise TypeError("model data must be a mapping or record") from exc
        object.__setattr__(self, "data", copied)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.data)

    async def get_by_id(self, id: Any, fields: str = "*") -> Manager:
        if fields != "*" and "id" not in {item.strip() for item in fields.split(",")}:
            fields = f"id,{fields}"
        await self.select(fields=fields, where="id={id}", params={"id": id})
        if (self.is_list and not self.data) or (not self.is_list and self.data.get("id") is None):
            raise JSONHTTPError(
                None,
                {"object": [f"{self.__class__.__name__} not found"]},
                web.HTTPNotFound,
            )
        return self

    async def get_by(self, fields: str = "*", **filters: Any) -> Manager:
        if not filters:
            raise SQLException("get_by requires at least one filter")
        if fields != "*" and "id" not in {item.strip() for item in fields.split(",")}:
            fields = f"id,{fields}"
        where = " AND ".join(f"{key}={{{key}}}" for key in filters)
        await self.select(fields=fields, where=where, params=filters)
        if (self.is_list and not self.data) or (not self.is_list and self.data.get("id") is None):
            raise JSONHTTPError(
                None,
                {"object": [f"{self.__class__.__name__} not found"]},
                web.HTTPNotFound,
            )
        return self

    async def select(
        self,
        fields: str = "*",
        join: str = "",
        where: str = "",
        order: str = "",
        limit: int | str | None = None,
        offset: int | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        data = await self.sql.select(
            fields=fields,
            join=join,
            where=where,
            order=order,
            limit=limit,
            offset=offset,
            params=params,
            many=self.is_list,
        )
        self.set_data(data)
        return self

    async def insert(
        self, data: Mapping[str, Any] | None = None, load: int = 0, **kwargs: Any
    ) -> Manager | int:
        copied = {**dict(data or {}), **kwargs}
        raw_result = await self.sql.insert(data=copied)
        merged = {**copied, **dict(raw_result or {})}
        self.set_data(merged)
        if load == 1:
            await self.select(where="id={id}", params={"id": self.id})
        return self

    def _write_scope(
        self, where: str, params: Mapping[str, Any] | None
    ) -> tuple[str, dict[str, Any]]:
        copied = dict(params or {})
        object_id = None if self.is_list else self.data.get("id")
        if object_id is not None:
            object_id_key = "__object_id"
            suffix = 1
            while object_id_key in copied or f"{{{object_id_key}}}" in where:
                object_id_key = f"__object_id_{suffix}"
                suffix += 1
            where = (
                f"({where}) AND id={{{object_id_key}}}"
                if where.strip()
                else f"id={{{object_id_key}}}"
            )
            copied[object_id_key] = object_id
        if not where.strip() or not copied:
            raise SQLException("write requires a non-empty scope")
        return where, copied

    async def update(
        self,
        where: str = "",
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> int:
        copied_data = {**dict(data or {}), **kwargs}
        scoped_where, scoped_params = self._write_scope(where, params)
        updated = await self.sql.update(scoped_where, scoped_params, copied_data)
        if not self.is_list:
            self.data.update(copied_data)
        return cast(int, updated)

    async def delete(self, where: str = "", params: Mapping[str, Any] | None = None) -> int:
        scoped_where, scoped_params = self._write_scope(where, params)
        deleted = await self.sql.delete(scoped_where, scoped_params)
        self.set_data([] if self.is_list else {})
        return cast(int, deleted)

    async def update_all(self, data: Mapping[str, Any]) -> int:
        return cast(int, await self.sql.update_all(data))

    async def delete_all(self) -> int:
        deleted = await self.sql.delete_all()
        self.set_data([] if self.is_list else {})
        return cast(int, deleted)

    async def get_count(self, where: str = "", params: Mapping[str, Any] | None = None) -> int:
        return cast(int, await self.sql.get_count(where=where, params=params))

    async def is_exists(
        self,
        where: str = "",
        params: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> bool:
        copied = {**dict(params or {}), **kwargs}
        return cast(bool, await self.sql.is_exists(where, copied))


class JsonbManager(Manager):
    """Safely mutate one JSONB column using bound payloads and paths."""

    __key_name__: str | None = None
    __update_type__ = "update"

    def _key(self) -> str:
        if self.__key_name__ is None:
            raise SQLException("JsonbManager.__key_name__ is not configured")
        return validate_identifier(self.__key_name__, kind="JSONB column")

    @staticmethod
    def _scope(where: str, params: Mapping[str, Any]) -> dict[str, Any]:
        copied = dict(params)
        if not where.strip() or not copied:
            raise SQLException("JSONB write requires a non-empty scope")
        return copied

    async def select(
        self,
        fields: str = "*",
        join: str = "",
        where: str = "",
        order: str = "",
        limit: int | str | None = None,
        offset: int | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        key = self._key()
        selected = key if fields == "*" else fields
        data = await self.sql.select(
            fields=selected,
            where=where,
            order=order,
            limit=limit,
            params=params,
            many=False,
        )
        if not data or key not in data:
            raise JSONHTTPError(None, {"object": ["No object updated"]}, web.HTTPBadRequest)
        self.set_data(data[key])
        return self.data

    async def insert(
        self,
        data: Any = None,
        load: Any = 0,
        *args: Any,
        **kwargs: Any,
    ) -> int:
        if isinstance(data, str) and isinstance(load, Mapping) and args:
            where = data
            params = load
            data = args[0]
            args = args[1:]
        else:
            where = cast(str, kwargs.pop("where", ""))
            params = cast(Mapping[str, Any], kwargs.pop("params", {}))
        if args:
            raise TypeError("Too many positional JSONB insert arguments")
        if kwargs:
            raise TypeError(f"Unsupported JSONB insert arguments: {sorted(kwargs)}")
        key = self._key()
        copied = self._scope(where, params)
        bound = combine_bound_values({"json_data": data}, copied)
        prepared_where = self.sql.prepare_where(where, copied, 1)
        if self.__update_type__ == "append":
            expression = f"jsonb_set({key}, ARRAY[jsonb_array_length({key})::text], $1::jsonb)"
        elif self.__update_type__ == "update":
            expression = f"{key} || $1::jsonb"
        else:
            raise SQLException(f"Unsupported JSONB update type {self.__update_type__!r}")
        query = f"UPDATE {self.table} SET {key}={expression} WHERE {prepared_where}"
        result = await self.sql.execute(query, bound)
        count = int(result.removeprefix("UPDATE "))
        if count == 0:
            raise JSONHTTPError(None, {"object": ["No object updated"]}, web.HTTPBadRequest)
        return count

    async def update(
        self,
        where: str = "",
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> int:
        copied_data: Any = data if data is not None else kwargs
        params = params or {}
        if "index" not in params:
            return await self.insert(copied_data, where=where, params=params)
        key = self._key()
        copied = dict(params)
        index = copied.pop("index")
        scoped = self._scope(where, copied)
        bound = combine_bound_values(
            {
                "json_path": [str(index)],
                "json_data": copied_data,
            },
            scoped,
        )
        prepared_where = self.sql.prepare_where(where, scoped, 2)
        query = (
            f"UPDATE {self.table} SET {key}=jsonb_set({key}, $1::text[], $2::jsonb) "
            f"WHERE {prepared_where}"
        )
        result = await self.sql.execute(query, bound)
        return int(result.removeprefix("UPDATE "))

    async def delete(self, where: str = "", params: Mapping[str, Any] | None = None) -> int:
        key = self._key()
        copied = dict(params or {})
        if "index" not in copied:
            raise SQLException("JSONB delete requires index")
        index = copied.pop("index")
        scoped = self._scope(where, copied)
        bound = combine_bound_values({"json_index": index}, scoped)
        prepared_where = self.sql.prepare_where(where, scoped, 1)
        cast = "integer" if isinstance(index, int) else "text"
        query = f"UPDATE {self.table} SET {key}={key}-$1::{cast} " f"WHERE {prepared_where}"
        result = await self.sql.execute(query, bound)
        return int(result.removeprefix("UPDATE "))
