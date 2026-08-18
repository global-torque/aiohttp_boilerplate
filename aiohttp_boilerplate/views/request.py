"""Typed request-scoped context and logging helpers."""

from __future__ import annotations

import logging
import warnings
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from aiohttp import web


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Immutable non-secret context associated with one request."""

    request_id: str
    msg_id: str | None = None
    user: str | int | None = None
    component: str = "aiohttp:server"
    service_name: str | None = None
    version: str | None = None
    source_reference: Mapping[str, str] | None = None
    extra_data: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.source_reference is not None:
            object.__setattr__(
                self,
                "source_reference",
                MappingProxyType(dict(self.source_reference)),
            )
        object.__setattr__(self, "extra_data", MappingProxyType(dict(self.extra_data)))


REQUEST_CONTEXT_KEY: web.RequestKey[RequestContext] = web.RequestKey(
    "request_context", RequestContext
)


class RequestLoggerAdapter(logging.LoggerAdapter[logging.Logger]):
    """Immutable logger adapter that adds only safe request metadata."""

    context: RequestContext

    def __init__(self, logger: logging.Logger, context: RequestContext) -> None:
        object.__setattr__(self, "_frozen", False)
        super().__init__(logger, MappingProxyType({}))
        self.context = context
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError("RequestLoggerAdapter is immutable; create a replacement")
        object.__setattr__(self, name, value)

    def process(
        self, msg: object, kwargs: MutableMapping[str, Any]
    ) -> tuple[object, MutableMapping[str, Any]]:
        supplied = dict(kwargs.get("extra") or {})
        service_context = dict(supplied.pop("serviceContext", {}) or {})
        service_context["request_id"] = self.context.request_id
        if self.context.msg_id is not None:
            service_context["msg_id"] = self.context.msg_id
        if self.context.user is not None:
            service_context["user"] = self.context.user
        if self.context.service_name is not None:
            service_context["service_name"] = self.context.service_name
        if self.context.version is not None:
            service_context["version"] = self.context.version
        if self.context.source_reference is not None:
            service_context["sourceReference"] = self.context.source_reference
        service_context.update(self.context.extra_data)
        supplied.update(
            {
                "component": self.context.component,
                "trace": self.context.request_id,
                "serviceContext": service_context,
            }
        )
        kwargs["extra"] = supplied
        return msg, kwargs

    def with_context(self, context: RequestContext) -> RequestLoggerAdapter:
        return type(self)(self.logger, context)

    def with_component(self, component: str) -> RequestLoggerAdapter:
        return self.with_context(replace(self.context, component=component))

    def set_component_name(self, name: str) -> None:
        warnings.warn(
            "set_component_name() is deprecated; assign log.with_component(name)",
            DeprecationWarning,
            stacklevel=2,
        )


REQUEST_LOG_KEY: web.RequestKey[RequestLoggerAdapter] = web.RequestKey(
    "request_logger", RequestLoggerAdapter
)


def install_request_context(request: web.Request, context: RequestContext) -> None:
    """Atomically replace typed request context, logger, and 0.9 aliases."""
    base_logger = logging.getLogger(context.component)
    adapter = RequestLoggerAdapter(base_logger, context)
    request[REQUEST_CONTEXT_KEY] = context
    request[REQUEST_LOG_KEY] = adapter
    warnings.warn(
        "request.context and request.log are deprecated compatibility aliases; use "
        "typed RequestKeys",
        DeprecationWarning,
        stacklevel=2,
    )
    request.context = context
    request.log = adapter


def replace_request_context(request: web.Request, **changes: Any) -> RequestContext:
    """Replace a request's frozen context before subsequent awaits or logs."""
    current = request[REQUEST_CONTEXT_KEY]
    context = replace(current, **changes)
    install_request_context(request, context)
    return context


# Compatibility name retained through 0.9.
Context = RequestContext
