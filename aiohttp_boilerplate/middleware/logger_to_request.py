"""Install isolated request context and logging."""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable

from aiohttp import hdrs, web

from aiohttp_boilerplate.views.request import RequestContext, install_request_context

REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id(request: web.Request) -> str:
    """Return the caller request ID or generate an isolated ID."""
    return request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex


@web.middleware
async def logger_to_request(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """Attach a fresh immutable context and ensure the ID reaches every response."""
    git_commit = os.getenv("GIT_COMMIT")
    build_date = os.getenv("BUILD_DATE")
    repository = os.getenv("REPOSITORY")
    source_reference = None
    if repository:
        source_reference = {
            "repository": repository,
            "revisionId": git_commit or "",
        }
    context = RequestContext(
        request_id=get_request_id(request),
        msg_id=request.headers.get("X-Message-ID"),
        service_name=os.getenv("SERVICE_NAME"),
        version=(f"{git_commit}:{build_date}" if git_commit or build_date else None),
        source_reference=source_reference,
    )
    install_request_context(request, context)
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        exc.headers[REQUEST_ID_HEADER] = context.request_id
        raise
    response.headers[REQUEST_ID_HEADER] = context.request_id
    response.headers.setdefault(hdrs.SERVER, "")
    return response
