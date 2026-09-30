"""Task-owned HTTP transactions shared by middleware, views, models and raw helpers."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from aiohttp import web
from asyncpg import Connection

Callback = Callable[[], Any]
ScopeKind = Literal["middleware", "view"]
log = logging.getLogger(__name__)


class TransactionScopeError(RuntimeError):
    """An HTTP transaction cannot safely complete or be accessed here."""


@dataclass
class TransactionScope:
    """Mutable lifecycle shared with inherited contexts so expiry remains visible."""

    pool: Any
    connection: Connection
    task: asyncio.Task[Any] | None
    kind: ScopeKind
    request: web.Request
    lifecycle: Literal["active", "finishing", "expired", "callbacks"] = "active"
    rollback_only: bool = False
    response_violation: bool = False
    callbacks: list[Callback] = field(default_factory=list)


_SCOPE: ContextVar[TransactionScope | None] = ContextVar("http_transaction", default=None)
TRANSACTION_SCOPE_KEY: web.RequestKey[TransactionScope] = web.RequestKey(
    "http_transaction", TransactionScope
)


def application_pool(app: web.Application) -> Any:
    """Resolve the canonical pool key, retaining the legacy attribute alias."""
    from aiohttp_boilerplate.bootstrap.web_app import DB_POOL_KEY

    if DB_POOL_KEY in app:
        return app[DB_POOL_KEY]
    pool = getattr(app, "db_pool", None)
    if pool is None:
        raise TransactionScopeError("Application database pool is not configured")
    return pool


def current_scope(pool: Any = None) -> TransactionScope | None:
    """Validate inherited state before callers inspect or acquire a connection."""
    scope = _SCOPE.get()
    if scope is None:
        return None
    if scope.lifecycle != "active":
        raise TransactionScopeError("HTTP transaction scope has expired or is completing")
    if scope.task is not asyncio.current_task():
        raise TransactionScopeError("HTTP transaction connections cannot be used by child tasks")
    if pool is not None and scope.pool is not pool:
        raise TransactionScopeError("HTTP transaction belongs to a different database pool")
    return scope


def get_request_connection(request: web.Request) -> Connection:
    """Return the active connection for this request and task; never retain it."""
    scope = current_scope(application_pool(request.app))
    if scope is None or scope.request is not request:
        raise TransactionScopeError("No active HTTP transaction for this request")
    return scope.connection


class RequestConnectionMixin:
    """Initialization-free, read-only connection accessor for existing view bases."""

    request: web.Request

    @property
    def conn(self) -> Connection:
        """Return this request task's connection only while its HTTP scope is active."""
        return get_request_connection(self.request)


@asynccontextmanager
async def acquire_connection(pool: Any) -> AsyncIterator[Connection]:
    """Borrow an HTTP connection, otherwise acquire and release a standalone one."""
    scope = current_scope(pool)
    if scope is not None:
        yield scope.connection
        return
    connection = await pool.acquire()
    try:
        yield connection
    finally:
        await pool.release(connection)


@asynccontextmanager
async def joined_transaction(pool: Any) -> AsyncIterator[TransactionScope | None]:
    """Mark an owning request rollback-only when a joined command fails."""
    scope = current_scope(pool)
    try:
        yield scope
    except BaseException:
        if scope is not None:
            scope.rollback_only = True
        raise


def on_commit(callback: Callback) -> None:
    """Register synchronous or async work after the HTTP owner releases its connection."""
    scope = current_scope()
    if scope is None:
        raise TransactionScopeError("on_commit requires an active HTTP transaction")
    if not callable(callback):
        raise TypeError("on_commit requires a callable")
    scope.callbacks.append(callback)


@asynccontextmanager
async def recovery_savepoint() -> AsyncIterator[Connection]:
    """Explicitly recover an intentional database failure and discard its callbacks."""
    scope = current_scope()
    if scope is None:
        raise TransactionScopeError("recovery_savepoint requires an active HTTP transaction")
    rollback_only, checkpoint = scope.rollback_only, len(scope.callbacks)
    try:
        transaction = scope.connection.transaction()
        await transaction.start()
    except BaseException:
        scope.rollback_only = True
        raise
    try:
        yield scope.connection
    except BaseException:
        try:
            await transaction.rollback()
        except BaseException:
            scope.rollback_only = True
            raise
        scope.rollback_only = rollback_only
        del scope.callbacks[checkpoint:]
        raise
    else:
        try:
            await transaction.commit()
        except BaseException:
            scope.rollback_only = True
            raise


def _protect_preparation(request: web.Request) -> None:
    scope = _SCOPE.get() or request.get(TRANSACTION_SCOPE_KEY)
    if scope is not None and scope.lifecycle in {"active", "finishing"}:
        scope.rollback_only = True
        scope.response_violation = True
        raise TransactionScopeError("Responses cannot be prepared before the HTTP transaction ends")


_original_prepare = web.StreamResponse.prepare


async def _guarded_prepare(self: web.StreamResponse, request: web.Request) -> Any:
    _protect_preparation(request)
    return await _original_prepare(self, request)


async def _guard_on_prepare(request: web.Request, response: web.StreamResponse) -> None:
    _protect_preparation(request)


def install_response_guard(app: web.Application) -> None:
    """Install an inert-unless-scoped guard, including for view-only applications."""
    cast(Any, web.StreamResponse).prepare = _guarded_prepare
    if not app.on_response_prepare.frozen and _guard_on_prepare not in app.on_response_prepare:
        app.on_response_prepare.insert(0, _guard_on_prepare)


def _buffered_response(response: web.StreamResponse) -> web.Response:
    if (
        not isinstance(response, web.Response)
        or isinstance(response, web.HTTPException)
        or response.prepared
        or response._eof_sent
        or (response.body is not None and not isinstance(response.body, bytes | bytearray))
    ):
        raise TransactionScopeError("Atomic handlers require an unprepared buffered web.Response")
    return response


async def _dispatch(handler: Callable[[], Awaitable[web.StreamResponse]]) -> web.Response:
    try:
        response = await handler()
    except web.HTTPException as exc:
        if exc.status >= 400:
            raise
        response = web.Response(
            status=exc.status, reason=exc.reason, body=exc.body, headers=exc.headers
        )
        response.cookies.update(exc.cookies)
    return _buffered_response(response)


async def http_transaction(
    request: web.Request,
    handler: Callable[[], Awaitable[web.StreamResponse]],
    *,
    kind: ScopeKind,
) -> web.Response:
    """Run complete buffered dispatch, joining an existing owner without a savepoint."""
    pool = application_pool(request.app)
    scope = current_scope(pool)
    if scope is not None:
        if scope.request is not request:
            raise TransactionScopeError("HTTP transaction belongs to a different request")
        try:
            response = await _dispatch(handler)
            if response.status >= 400:
                scope.rollback_only = True
            return response
        except BaseException:
            scope.rollback_only = True
            raise

    install_response_guard(request.app)
    connection = await pool.acquire()
    scope = TransactionScope(pool, connection, asyncio.current_task(), kind, request)
    token = _SCOPE.set(scope)
    request[TRANSACTION_SCOPE_KEY] = scope
    transaction = None
    committed = False
    try:
        transaction = connection.transaction(isolation="read_committed")
        await transaction.start()
        response = await _dispatch(handler)
        if response.status >= 400:
            await transaction.rollback()
        else:
            if scope.rollback_only or scope.response_violation:
                raise TransactionScopeError("HTTP transaction is rollback-only")
            await connection.execute("SELECT 1")
            scope.lifecycle = "finishing"
            await transaction.commit()
            committed = True
    except BaseException:
        if transaction is not None:
            with suppress(Exception):
                await transaction.rollback()
        raise
    finally:
        scope.lifecycle = "expired"
        request.pop(TRANSACTION_SCOPE_KEY, None)
        _SCOPE.reset(token)
        await pool.release(connection)
    if committed:
        scope.lifecycle = "callbacks"
        callback_token = _SCOPE.set(scope)
        try:
            for callback in scope.callbacks:
                try:
                    result = callback()
                    if inspect.isawaitable(result):
                        await result
                except (Exception, asyncio.CancelledError):
                    log.exception("HTTP transaction on_commit callback failed")
        finally:
            scope.lifecycle = "expired"
            _SCOPE.reset(callback_token)
    return response
