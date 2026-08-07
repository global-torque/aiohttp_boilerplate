"""Structured logger without shared request state."""

from __future__ import annotations

import logging
import os
import warnings
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pythonjsonlogger.json import JsonFormatter

from . import formatters

GCPSeverityMap = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class GCPLogger(logging.Logger):
    """Logger that emits one structured event and never stores a request."""

    default_format = "json"

    def __init__(
        self,
        name: str,
        level: int = logging.NOTSET,
        *,
        format: str | None = None,
        stack_info: bool = False,
        stacklevel: int = 3,
        extra_labels: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(name, level)
        self.format_mode = format or type(self).default_format
        self.default_stack_info = stack_info
        self.default_stacklevel = stacklevel
        self.extra_labels = dict(extra_labels or {})
        handler = logging.StreamHandler()
        if self.format_mode == "json":
            handler.setFormatter(JsonFormatter())
        elif self.format_mode == "colored":
            handler.setFormatter(formatters.ColoredFormatter(formatters.DEFAULT_MSG_FORMAT))
        else:
            handler.setFormatter(formatters.TxtFormatter(formatters.DEFAULT_MSG_FORMAT))
        self.addHandler(handler)
        self.propagate = False

    def new_component_logger(self, name: str) -> GCPLogger:
        """Return an independent logger for another component."""
        return type(self)(
            name,
            self.level,
            format=self.format_mode,
            stack_info=self.default_stack_info,
            stacklevel=self.default_stacklevel,
            extra_labels=self.extra_labels,
        )

    def set_component_name(self, name: str) -> None:
        """Deprecated mutator retained without changing shared logger state."""
        warnings.warn(
            "set_component_name() no longer mutates shared loggers; use "
            "new_component_logger(name)",
            DeprecationWarning,
            stacklevel=2,
        )

    def _structured_extra(self, level: int, supplied: Mapping[str, Any]) -> dict[str, Any]:
        service_context = {
            **self.extra_labels,
            **dict(supplied.get("serviceContext", {}) or {}),
        }
        service_context.setdefault("service_name", os.getenv("SERVICE_NAME"))
        extra = {
            **dict(supplied),
            "component": supplied.get("component", self.name),
            "serviceContext": service_context,
            "level": GCPSeverityMap.get(level, "INFO").lower(),
            "severity": GCPSeverityMap.get(level, "INFO"),
            "time": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        return extra

    def _log(
        self,
        level: int,
        msg: object,
        args: tuple[object, ...] | Mapping[str, object],
        exc_info: Any = None,
        extra: Mapping[str, Any] | None = None,
        stack_info: bool | None = None,
        stacklevel: int | None = None,
    ) -> None:
        super()._log(
            level,
            msg,
            args,
            exc_info=exc_info,
            extra=self._structured_extra(level, extra or {}),
            stack_info=self.default_stack_info if stack_info is None else stack_info,
            stacklevel=self.default_stacklevel if stacklevel is None else stacklevel,
        )
