"""Import setup_routes from an application's routes module; create widgets(id integer primary key).

For middleware-only or combined ownership set application configuration:
    atomic_request_methods = ("POST", "PUT", "PATCH", "DELETE")
For view-only ownership leave atomic_request_methods = ().
"""

from functools import wraps
from typing import Any

from aiohttp import web
from marshmallow import Schema, fields

from aiohttp_boilerplate.models import Manager
from aiohttp_boilerplate.transactions import acquire_connection
from aiohttp_boilerplate.views import (
    AtomicCreateView,
    AtomicUpdateView,
    AtomicView,
    AtomicViewMixin,
    OptionsViewMixin,
)


class Widget(Manager):
    __table__ = "widgets"


class WidgetSchema(Schema):
    id = fields.Integer(required=True)


class WidgetCreate(AtomicCreateView):
    def get_model(self) -> type[Widget]:
        return Widget

    def get_schema(self) -> type[WidgetSchema]:
        return WidgetSchema


class WidgetUpdate(AtomicUpdateView):
    def get_model(self) -> type[Widget]:
        return Widget

    def get_schema(self) -> type[WidgetSchema]:
        return WidgetSchema

    async def on_start(self) -> None:
        await self.obj.get_by_id(await self.get_id())


class WidgetRemoval(AtomicView):
    """An explicit custom DELETE operation with an empty buffered response."""

    async def delete(self) -> web.Response:
        widget = Widget(self.db_pool)
        await widget.get_by_id(int(self.request.match_info["id"]))
        await widget.delete()
        # Additional SQL/model calls here borrow the same connection.
        async with acquire_connection(self.db_pool) as conn:
            await conn.execute("SELECT 1")
        return web.Response(status=204)


class CreateAdapter(AtomicViewMixin, OptionsViewMixin, web.View):
    """Only POST/OPTIONS are exposed, and direct forwarding is inside this scope."""

    @wraps(WidgetCreate.post)
    async def post(self) -> web.Response:
        return await WidgetCreate(self.request).post()

    async def _options(self) -> dict[str, Any]:
        return {"command": "create-widget"}


def setup_routes(app: web.Application) -> None:
    app.router.add_view("/widgets", WidgetCreate)
    app.router.add_view("/widgets/{id}", WidgetUpdate)
    app.router.add_view("/widgets/{id}/removal", WidgetRemoval)
    app.router.add_view("/widget-command", CreateAdapter)
