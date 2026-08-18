"""aiohttp access logger with safe structured fields."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from aiohttp.abc import AbstractAccessLogger

from aiohttp_boilerplate.views.request import REQUEST_CONTEXT_KEY

from .gcp_logger import GCPSeverityMap


def filter_request_logs(record: logging.LogRecord) -> bool:
    """Filter health/metrics when an access-log record contains a path."""
    context = getattr(record, "serviceContext", {}) or {}
    request = context.get("httpRequest", {}) if isinstance(context, dict) else {}
    return request.get("path") not in {"/healthcheck", "/metrics"}


# Historical misspelling retained through 0.9.
filerRequestsLogs = filter_request_logs


class AccessLoggerRequestResponse(AbstractAccessLogger):
    """Emit one event per request without credentials or cookies."""

    def __init__(self, logger: logging.Logger, log_format: str) -> None:
        super().__init__(logger, log_format=log_format)
        self.logger.addFilter(filter_request_logs)

    def log(self, request: Any, response: Any, elapsed: float) -> None:
        level = logging.INFO
        if response.status >= 500:
            level = logging.ERROR
        elif response.status >= 400:
            level = logging.WARNING
        context = request.get(REQUEST_CONTEXT_KEY)
        service_context = {
            "httpRequest": {
                "method": request.method,
                "url": request.path,
                "path": request.path,
                "userAgent": request.headers.get("User-Agent", ""),
                "responseStatusCode": response.status,
                "remoteIp": request.remote,
                "latency": elapsed,
                "protocol": request.scheme,
            }
        }
        if context is not None:
            service_context["request_id"] = context.request_id
            if context.msg_id is not None:
                service_context["msg_id"] = context.msg_id
            if context.user is not None:
                service_context["user"] = context.user
        self.logger.log(
            level,
            "completed handling request",
            extra={
                "component": "access-log",
                "severity": GCPSeverityMap[level],
                "level": GCPSeverityMap[level].lower(),
                "time": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "serviceContext": service_context,
            },
        )
