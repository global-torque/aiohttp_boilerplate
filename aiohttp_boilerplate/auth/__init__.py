"""Authentication client helpers with application-owned sessions."""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import Any, cast

import aiohttp
import jwt
from aiohttp import web

from aiohttp_boilerplate.bootstrap.web_app import AUTH_SESSION_KEY
from aiohttp_boilerplate.config import APP_CONFIG_KEY
from aiohttp_boilerplate.views.exceptions import JSONHTTPError
from aiohttp_boilerplate.views.request import replace_request_context


async def validate_token(
    headers: Mapping[str, str],
    auth_url: str,
    session: aiohttp.ClientSession | None = None,
) -> Mapping[str, Any]:
    """Validate credentials through the auth service using a reusable session."""
    if not auth_url:
        raise JSONHTTPError(
            None,
            {"auth_url": "Authentication service URL is not configured"},
            web.HTTPServiceUnavailable,
        )
    owned_session = session is None
    if owned_session:
        warnings.warn(
            "validate_token() without an injected session is deprecated",
            DeprecationWarning,
            stacklevel=2,
        )
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10, connect=2, sock_read=5)
        )
    assert session is not None
    try:
        try:
            async with session.get(auth_url, headers=dict(headers)) as response:
                if response.status in {400, 401, 403}:
                    raise JSONHTTPError(
                        None,
                        {"credentials": ["Invalid credentials"]},
                        web.HTTPForbidden,
                    )
                if response.status not in {200, 204}:
                    raise JSONHTTPError(
                        None,
                        {"upstream": ["Authentication service unavailable"]},
                        web.HTTPServiceUnavailable,
                    )
                if response.status == 204:
                    return {}
                try:
                    payload = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as exc:
                    raise JSONHTTPError(
                        None,
                        {"upstream": ["Malformed authentication response"]},
                        web.HTTPBadGateway,
                    ) from exc
                if not isinstance(payload, Mapping):
                    raise JSONHTTPError(
                        None,
                        {"upstream": ["Authentication response must be an object"]},
                        web.HTTPBadGateway,
                    )
                return dict(payload)
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise JSONHTTPError(
                None,
                {"upstream": ["Authentication service unavailable"]},
                web.HTTPServiceUnavailable,
            ) from exc
    finally:
        if owned_session:
            await session.close()


def decode_token_unsafe(token: str) -> Mapping[str, Any]:
    """Decode JWT claims without verification; never use for authentication."""
    payload = jwt.decode(token, options={"verify_signature": False})
    if not isinstance(payload, Mapping):
        raise ValueError("JWT payload must be an object")
    return payload


def encode_token(token: str) -> Mapping[str, Any]:
    """Deprecated compatibility alias for explicitly unsafe claim parsing."""
    warnings.warn(
        "encode_token() is deprecated and unsafe; use decode_token_unsafe() only "
        "for non-authentication inspection",
        DeprecationWarning,
        stacklevel=2,
    )
    return decode_token_unsafe(token)


class Auth:
    """Mixin that authenticates an aiohttp view request."""

    request: web.Request

    def __init__(self) -> None:
        self.auth: Mapping[str, Any] = {}

    async def auth_user(self, check_permissions: bool = False) -> None:
        headers: dict[str, str] = {}
        for name in ("Authorization", "X-Session-Token", "Cookie"):
            token = self.request.headers.get(name)
            if token:
                headers[name] = token
                break
        config = self.request.app[APP_CONFIG_KEY]
        cast(Any, self.request).log.debug("checking credentials")
        self.auth = await validate_token(
            headers,
            config.auth_url,
            self.request.app[AUTH_SESSION_KEY],
        )
        identity = self.auth.get("user_id", self.auth.get("id", self.auth.get("user")))
        replace_request_context(self.request, user=identity)
        if check_permissions:
            await self.check_permission()

    async def check_permission(self) -> None:
        """Override to enforce endpoint-specific permission checks."""
