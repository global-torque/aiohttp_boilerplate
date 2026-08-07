"""HTTP exceptions using the framework JSON error envelope."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from aiohttp import web

logger_name = "aiohttp_boilerplate.views"


def _safe_detail(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _safe_detail(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_safe_detail(item) for item in value]
    return "Invalid value"


def error_envelope(status: int, message: str, details: Any = None) -> dict[str, Any]:
    """Build the documented, serialization-safe error response body."""
    safe_details = _safe_detail(details)
    error: dict[str, Any] = {"status": status, "message": message}
    if safe_details is not None:
        error["details"] = safe_details
    return {"error": error}


class JSONHTTPError(web.HTTPException):
    """JSON HTTP exception preserving the requested aiohttp status."""

    def __init__(
        self,
        request: web.Request | None,
        details: Any,
        error_class: type[web.HTTPException] = web.HTTPBadRequest,
    ) -> None:
        status = error_class.status_code
        reason = error_class().reason
        self.status_code = status
        self.request = request
        if request is not None and hasattr(request, "log"):
            request.log.debug("request rejected", extra={"status": status})
        super().__init__(
            text=json.dumps(
                error_envelope(
                    status,
                    reason,
                    None if status >= 500 else details,
                )
            ),
            content_type="application/json",
            reason=reason,
        )
