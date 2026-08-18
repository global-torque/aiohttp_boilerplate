"""Small framework-owned response middleware."""

from collections.abc import Awaitable, Callable

from aiohttp import hdrs, web


@web.middleware
async def erase_header_server(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Avoid advertising the server implementation."""
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        exc.headers[hdrs.SERVER] = ""
        raise
    response.headers[hdrs.SERVER] = ""
    return response
