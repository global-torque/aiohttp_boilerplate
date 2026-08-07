import warnings
from collections.abc import Mapping, Sequence
from typing import Any, cast

from aiohttp import web

from .exceptions import JSONHTTPError, logger_name
from .retrieve import RetrieveView

ALLOW_ORDER = ["asc", "desc"]


class ListView(RetrieveView):
    order_fields: list[str] = []
    order_default = ""
    order_map_table: dict[str, str] = {}
    order_key = "order"

    limit_default = 50
    limit_max_default = 100

    def __init__(self, request: web.Request) -> None:
        super().__init__(request)
        self.log = cast(Any, request).log.with_component(logger_name)
        self.objects = self.get_objects()
        self.limit = self.get_limit()
        self.order = self.get_order()
        self.offset = self.get_offset()
        self.count: int | None = None

    # Return model object
    def get_model(self) -> type[Any] | None:
        warnings.warn("Redefine get_schema in inherited class", RuntimeWarning, stacklevel=2)
        return None

    # Return objects list
    def get_objects(self) -> Any:
        model = self.get_model()
        if model is None:
            raise RuntimeError("ListView requires a model")
        return model(
            is_list=True,
            db_pool=cast(Any, self.request.app).db_pool,
            log=self.log,
        )

    @staticmethod
    def str_to_int(value: str | int) -> int | None:
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    # Return limit for sql query
    def get_limit(self) -> int:
        limit = self.str_to_int(self.request.query.get("limit", self.limit_default))

        if limit is None or limit < 0:
            raise JSONHTTPError(
                self.request,
                {"limit": ["Must be a non-negative integer"]},
                web.HTTPBadRequest,
            )
        if limit > self.limit_max_default:
            return self.limit_max_default

        return limit

    # Return offset for sql query
    def get_offset(self) -> int | None:
        raw = self.request.query.get("offset")
        if raw is None:
            return None
        offset = self.str_to_int(raw)
        if offset is None or offset < 0:
            raise JSONHTTPError(
                self.request,
                {"offset": ["Must be a non-negative integer"]},
                web.HTTPBadRequest,
            )
        return offset

    # Return order
    def get_order(self) -> str:
        order = self.request.query.get(self.order_key, "")
        prepared_order = self.order_default
        if not order:
            return prepared_order

        field, f_order = order, "desc"
        if ":" in order:
            parts = order.split(":")
            if len(parts) != 2:
                raise JSONHTTPError(
                    self.request,
                    {"order": ["Unsupported ordering"]},
                    web.HTTPBadRequest,
                )
            field, f_order = parts

        if field not in self.order_fields or f_order not in ALLOW_ORDER:
            raise JSONHTTPError(
                self.request,
                {"order": ["Unsupported ordering"]},
                web.HTTPBadRequest,
            )
        prepared_order = f"{field} {f_order}"
        table_alias = self.order_map_table.get(field, "t0")
        if table_alias:
            prepared_order = table_alias + "." + prepared_order
        return prepared_order

    def join_beautiful_rows(
        self, aliases: Mapping[str, str], data: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:

        beautiful_data: list[dict[str, Any]] = []
        for row in data:
            temp = super().join_beautiful_output(aliases, row)
            beautiful_data.append(temp)

        return beautiful_data

    async def perform_get(self, fields: str = "", **kwargs: Any) -> None:
        self.log.debug("perform list query", extra={"field_count": fields.count(",") + 1})
        aliases, fields = self.join_prepare_fields(fields)
        raw_data = await self.objects.sql.select(fields=fields, many=True, **kwargs)
        self.objects.set_data(self.join_beautiful_rows(aliases, raw_data))

    async def perform_get_count(self, where: str, params: Mapping[str, Any]) -> int:
        self.log.debug("perform count query", extra={"parameter_count": len(params)})
        return cast(int, await self.objects.sql.get_count(where=where, params=params))

    async def get_count(self, where: str = "", params: Mapping[str, Any] | None = None) -> int:
        params = params or {}
        if self.count is None:
            self.count = await self.perform_get_count(where, params)
        return self.count

    async def get_data(self, objects: Sequence[Any]) -> list[Any]:
        data: list[Any] = []
        for obj in objects:
            data.append(await super().get_data(obj))

        return data

    async def combine_response(self) -> dict[str, Any]:
        return {
            "data": await self.get_data(self.objects.data),
            "count": await self.get_count(
                where=self.where,
                params=self.params,
            ),
        }

    async def _get(self) -> dict[str, Any]:
        await self.on_start()

        await self.before_get()
        await self.perform_get(
            fields=self.fields,
            where=self.where,
            limit=self.limit,
            offset=self.offset,
            order=self.order,
            params=self.params,
        )
        await self.after_get()
        return await self.combine_response()

    async def get(self) -> web.Response:
        return self.json_response(await self._get())
