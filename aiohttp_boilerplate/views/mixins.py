"""Endpoint mixins."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Iterable
from typing import Any, cast
from urllib.parse import urlencode
from weakref import WeakKeyDictionary

from aiohttp import web

_CACHE_STORES: WeakKeyDictionary[web.Application, dict[str, tuple[float, str]]] = (
    WeakKeyDictionary()
)


def _is_cache_bypass(request: web.Request, *, order_key: str = "order") -> bool:
    if any(key in request.query for key in ("skip", "skip_increment")):
        return True
    return any(key == order_key and value == "random" for key, value in request.query.items())


def build_cache_key(
    request: web.Request,
    *,
    namespace: str,
    vary_headers: Iterable[str],
    identity: str,
) -> str:
    """Build a stable key including repeated query values and identity."""
    canonical_query = urlencode(sorted(request.query.items()), doseq=True)
    header_values = "&".join(
        f"{name.lower()}={request.headers.get(name, '')}" for name in sorted(vary_headers)
    )
    return "|".join(
        (
            namespace,
            request.method.upper(),
            request.path,
            canonical_query,
            header_values,
            identity,
        )
    )


def key_builder(function: Callable[..., Any], self: Any, *args: Any, **kwargs: Any) -> str:
    """Legacy synchronous key-builder compatibility helper."""
    return build_cache_key(
        self.request,
        namespace=getattr(self, "namespace", ""),
        vary_headers=getattr(self, "cache_vary_headers", ()),
        identity="",
    )


class CacheMixin:
    """Opt-in, identity-aware TTL-only response caching."""

    namespace = ""
    cache_ttl = 60.0
    cache_vary_headers: tuple[str, ...] = ()
    cache_identity: Callable[[web.Request], str | Any] | None = None
    request: web.Request

    async def skip_page(self) -> None:
        """Compatibility hook invoked before list retrieval."""

    async def _identity(self) -> str | None:
        instance_values = getattr(self, "__dict__", {})
        callback = (
            instance_values["cache_identity"]
            if "cache_identity" in instance_values
            else type(self).cache_identity
        )
        if callback is None:
            return None
        identity = callback(self.request)
        if inspect.isawaitable(identity):
            identity = await identity
        if identity is None:
            return None
        return str(identity)

    async def _cached_call(self, operation: Callable[[], Any]) -> str:
        await self.skip_page()
        identity = await self._identity()
        cacheable = (
            identity is not None
            and self.request.method in {"GET", "HEAD", "OPTIONS"}
            and not _is_cache_bypass(
                self.request,
                order_key=getattr(self, "order_key", "order"),
            )
        )
        if not cacheable:
            return json.dumps(await operation(), separators=(",", ":"))
        assert identity is not None
        store = _CACHE_STORES.setdefault(self.request.app, {})
        key = build_cache_key(
            self.request,
            namespace=self.namespace,
            vary_headers=self.cache_vary_headers,
            identity=identity,
        )
        now = asyncio.get_running_loop().time()
        cached = store.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        if cached is not None:
            store.pop(key, None)
        value = json.dumps(await operation(), separators=(",", ":"))
        store[key] = (now + self.cache_ttl, value)
        return value

    async def _get(self) -> str:
        operation = cast(Any, super())._get
        return await self._cached_call(operation)

    async def _options(self) -> str:
        operation = cast(Any, super())._options
        return await self._cached_call(operation)

    @staticmethod
    async def increment(data: Any, path: str) -> None:
        """Mutation invalidation is intentionally outside the 0.9 TTL-only contract."""

    @staticmethod
    def json_response(data: Any, status: int = 200) -> web.Response:
        if isinstance(data, dict):
            response = cast(Any, super()).json_response(data, status)
            return cast(web.Response, response)
        return web.json_response(text=data, status=status)
