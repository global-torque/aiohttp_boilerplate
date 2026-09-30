"""Optional whole-request transaction boundary inside framework error handling."""

from collections.abc import Awaitable, Callable

from aiohttp import web

from aiohttp_boilerplate.config import APP_CONFIG_KEY
from aiohttp_boilerplate.transactions import http_transaction


@web.middleware
async def atomic_request_middleware(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    if request.method not in request.app[APP_CONFIG_KEY].atomic_request_methods:
        return await handler(request)
    return await http_transaction(request, lambda: handler(request), kind="middleware")
