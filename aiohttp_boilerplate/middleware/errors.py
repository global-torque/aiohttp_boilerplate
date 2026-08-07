"""Central JSON error handling."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiohttp import hdrs, web

from aiohttp_boilerplate.views.exceptions import error_envelope


@web.middleware
async def json_error_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Return one JSON envelope while preserving all HTTP statuses."""
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if exc.content_type == "application/json" and exc.text:
            raise
        headers = exc.headers.copy()
        headers.pop(hdrs.CONTENT_TYPE, None)
        headers.pop(hdrs.CONTENT_LENGTH, None)
        return web.json_response(
            error_envelope(
                exc.status,
                "Internal Server Error" if exc.status >= 500 else exc.reason,
            ),
            status=exc.status,
            headers=headers,
        )
    except Exception:
        if hasattr(request, "log"):
            request.log.error("unhandled request exception")
        return web.json_response(
            error_envelope(500, "Internal Server Error"),
            status=500,
        )
