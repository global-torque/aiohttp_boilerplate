"""HTTP transaction acceptance tests; PostgreSQL tests require an explicit disposable DSN."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from unittest.mock import Mock

import asyncpg
import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from aiohttp_boilerplate.bootstrap.web_app import DB_POOL_KEY
from aiohttp_boilerplate.config import AppConfig, ConfigurationError
from aiohttp_boilerplate.sql import SQL
from aiohttp_boilerplate.transactions import (
    TransactionScopeError,
    acquire_connection,
    get_request_connection,
    http_transaction,
    on_commit,
    recovery_savepoint,
)
from aiohttp_boilerplate.views import AtomicView, AtomicViewMixin


class Transaction:
    def __init__(self, conn):
        self.conn = conn

    async def start(self):
        self.conn.events.append("begin")

    async def commit(self):
        self.conn.events.append("commit")
        if self.conn.fail_commit:
            raise RuntimeError("commit failed")

    async def rollback(self):
        self.conn.events.append("rollback")


class Connection:
    def __init__(self):
        self.events = []
        self.fail_commit = False

    def transaction(self, **kwargs):
        assert kwargs == {"isolation": "read_committed"} or kwargs == {}
        return Transaction(self)

    async def execute(self, query, *args):
        self.events.append(query)
        return "UPDATE 1"


class Pool:
    def __init__(self):
        self.conn = Connection()
        self.acquired = self.released = 0

    async def acquire(self):
        self.acquired += 1
        return self.conn

    async def release(self, conn):
        assert conn is self.conn
        self.released += 1
        conn.events.append("release")


def request(pool, method="POST"):
    app = web.Application()
    app[DB_POOL_KEY] = pool
    req = make_mocked_request(method, "/", app=app)
    req.log = Mock()
    return req


def test_methods_validation():
    assert AppConfig().atomic_request_methods == ()
    assert AppConfig(atomic_request_methods=("POST", "DELETE")).atomic_request_methods == (
        "POST",
        "DELETE",
    )
    for methods in [("GET",), ("post",), "POST", ("POST", "POST")]:
        with pytest.raises(ConfigurationError):
            AppConfig.from_mapping({"atomic_request_methods": methods})
    assert not any(
        method in AtomicViewMixin.__dict__ for method in ("post", "put", "patch", "delete")
    )


@pytest.mark.parametrize("status", [200, 204, 400, 500])
async def test_owner_borrows_and_completes_once(status):
    pool = Pool()
    req = request(pool)
    old = SQL("widgets", pool)

    async def handler():
        assert get_request_connection(req) is pool.conn
        await old.execute("write one", {})
        assert old.conn is None
        async with acquire_connection(pool) as conn:
            assert conn is pool.conn
        async with old.transaction():
            await SQL("widgets", pool).execute("write two", {})
        return web.Response(status=status)

    response = await http_transaction(req, handler, kind="middleware")
    assert response.status == status
    assert pool.acquired == pool.released == 1
    assert pool.conn.events.count("begin") == 1
    assert ("commit" in pool.conn.events) == (status < 400)
    with pytest.raises(TransactionScopeError):
        get_request_connection(req)


async def test_joined_error_replaced_marks_owner_rollback_only():
    pool = Pool()
    req = request(pool)

    async def failure():
        return web.Response(status=400)

    async def outer():
        await http_transaction(req, failure, kind="view")
        return web.Response()

    with pytest.raises(TransactionScopeError):
        await http_transaction(req, outer, kind="middleware")
    assert pool.conn.events == ["begin", "rollback", "release"]


async def test_wrong_pool_child_task_and_cached_borrow_rejected():
    pool, other = Pool(), Pool()
    req = request(pool)
    cached = SQL("widgets", pool)

    async def handler():
        await cached.get_connection()
        with pytest.raises(TransactionScopeError):
            await asyncio.create_task(cached.get_connection())
        with pytest.raises(TransactionScopeError):
            async with acquire_connection(other):
                pass
        assert get_request_connection(req) is pool.conn
        return web.Response()

    await http_transaction(req, handler, kind="view")
    with pytest.raises(TransactionScopeError):
        await cached.get_connection()
    assert other.acquired == 0


@pytest.mark.parametrize("status", [204, 302])
async def test_success_exception_metadata(status):
    pool = Pool()
    req = request(pool)

    async def handler():
        exc = web.HTTPNoContent() if status == 204 else web.HTTPFound("/target")
        exc.headers["X-Result"] = "yes"
        exc.set_cookie("session", "value")
        raise exc

    response = await http_transaction(req, handler, kind="view")
    assert type(response) is web.Response
    assert response.status == status
    assert response.headers["X-Result"] == "yes"
    assert response.cookies["session"].value == "value"
    assert "commit" in pool.conn.events


async def test_callback_release_order_and_database_blocking():
    pool = Pool()
    req = request(pool)

    async def callback():
        assert pool.conn.events[-2:] == ["commit", "release"]
        with pytest.raises(TransactionScopeError):
            get_request_connection(req)
        with pytest.raises(TransactionScopeError):
            async with acquire_connection(pool):
                pass
        pool.conn.events.append("callback")
        raise RuntimeError("callback failure is logged")

    async def handler():
        on_commit(callback)
        return web.Response()

    assert (await http_transaction(req, handler, kind="view")).status == 200
    assert pool.conn.events[-1] == "callback"


@pytest.mark.parametrize("response", [web.StreamResponse(), web.FileResponse(__file__)])
async def test_unbuffered_response_rejected(response):
    pool = Pool()

    async def handler():
        return response

    with pytest.raises(TransactionScopeError):
        await http_transaction(request(pool), handler, kind="view")
    assert "commit" not in pool.conn.events


async def test_caught_early_prepare_cannot_commit():
    pool = Pool()
    req = request(pool)

    async def handler():
        with pytest.raises(TransactionScopeError):
            await web.Response().prepare(req)
        return web.Response()

    with pytest.raises(TransactionScopeError):
        await http_transaction(req, handler, kind="view")
    assert "commit" not in pool.conn.events


async def test_commit_failure_and_cancellation_cleanup():
    for cancellation in [False, True]:
        pool = Pool()
        pool.conn.fail_commit = not cancellation

        async def handler(cancellation=cancellation):
            if cancellation:
                raise asyncio.CancelledError
            return web.Response()

        with pytest.raises(asyncio.CancelledError if cancellation else RuntimeError):
            await http_transaction(request(pool), handler, kind="view")
        assert pool.released == 1


@pytest.fixture
async def postgres_pool():
    dsn = os.environ.get("ATOMIC_TEST_DSN")
    if not dsn:
        pytest.fail("Set ATOMIC_TEST_DSN to a disposable PostgreSQL database")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    async with pool.acquire() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS atomic_widgets (id integer PRIMARY KEY)")
        await conn.execute("TRUNCATE atomic_widgets")
    yield pool
    await pool.close()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("strategy", ["middleware", "view", "combined", "neither"])
@pytest.mark.parametrize("failure", [False, True])
async def test_postgres_strategy_matrix(postgres_pool, aiohttp_client, method, strategy, failure):
    from aiohttp_boilerplate.config import APP_CONFIG_KEY
    from aiohttp_boilerplate.middleware.transactions import atomic_request_middleware
    from aiohttp_boilerplate.transactions import install_response_guard

    pool = postgres_pool
    app = web.Application(middlewares=[atomic_request_middleware])
    app[DB_POOL_KEY] = pool
    app[APP_CONFIG_KEY] = AppConfig(
        atomic_request_methods=(method,) if strategy in ["middleware", "combined"] else ()
    )
    install_response_guard(app)

    class Plain(web.View):
        async def write(self):
            await SQL("atomic_widgets", pool).execute("INSERT INTO atomic_widgets VALUES (1)", {})
            return web.Response(status=400 if failure else 204)

        post = put = patch = delete = write

    class Atomic(AtomicViewMixin, Plain):
        pass

    app.router.add_view("/", Atomic if strategy in ["view", "combined"] else Plain)
    client = await aiohttp_client(app)
    response = await client.request(method, "/")
    assert response.status == (400 if failure else 204)
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM atomic_widgets")
    assert count == (0 if failure and strategy != "neither" else 1)


async def test_postgres_aborted_sql_and_explicit_recovery(postgres_pool):
    pool = postgres_pool
    req = request(pool)

    async def handler():
        async with recovery_savepoint():
            await SQL("atomic_widgets", pool).execute("INSERT INTO atomic_widgets VALUES (1)", {})
        with suppress(asyncpg.UniqueViolationError):
            async with recovery_savepoint():
                on_commit(lambda: pytest.fail("rolled-back savepoint callback"))
                await SQL("atomic_widgets", pool).execute(
                    "INSERT INTO atomic_widgets VALUES (1)", {}
                )
        return web.Response()

    await http_transaction(req, handler, kind="view")

    async def aborted():
        with suppress(asyncpg.UniqueViolationError):
            await SQL("atomic_widgets", pool).execute("INSERT INTO atomic_widgets VALUES (1)", {})
        return web.Response()

    with pytest.raises(asyncpg.InFailedSQLTransactionError):
        await http_transaction(req, aborted, kind="view")


async def test_cached_connection_property_and_child_release_are_checked():
    pool = Pool()
    req = request(pool)
    sql = SQL("widgets", pool)

    async def handler():
        await sql.get_connection()

        async def child():
            with pytest.raises(TransactionScopeError):
                _ = sql.conn
            with pytest.raises(TransactionScopeError):
                await sql.release()

        await asyncio.create_task(child())
        assert sql.conn is pool.conn
        return web.Response()

    await http_transaction(req, handler, kind="view")
    with pytest.raises(TransactionScopeError):
        _ = sql.conn


async def test_options_mixin_preserves_custom_constructor():
    from aiohttp_boilerplate.views import OptionsViewMixin

    class Custom(web.View):
        async def request_data(self):
            return {"custom": True}

    class Final(AtomicViewMixin, OptionsViewMixin, Custom):
        async def _options(self):
            return await self.request_data()

        async def delete(self):
            return web.Response(status=204)

    pool = Pool()
    response = await Final(request(pool, "OPTIONS"))
    assert response.text == '{"custom": true}'
    assert pool.acquired == 0


async def test_transaction_factory_failure_releases_and_clears():
    pool = Pool()
    req = request(pool)
    pool.conn.transaction = Mock(side_effect=RuntimeError("factory failure"))

    async def handler():
        pytest.fail("must not dispatch")

    with pytest.raises(RuntimeError, match="factory failure"):
        await http_transaction(req, handler, kind="view")
    assert pool.acquired == pool.released == 1
    with pytest.raises(TransactionScopeError):
        get_request_connection(req)
    async with acquire_connection(pool):
        pass
    assert pool.acquired == pool.released == 2


async def test_savepoint_cannot_recover_early_response_preparation():
    pool = Pool()
    req = request(pool)

    async def handler():
        with pytest.raises(TransactionScopeError):
            async with recovery_savepoint():
                await web.Response().prepare(req)
        return web.Response()

    with pytest.raises(TransactionScopeError):
        await http_transaction(req, handler, kind="view")
    assert "commit" not in pool.conn.events


def framework_app(
    monkeypatch, pool, view, *, methods=(), middlewares=(), cors=False, openapi=False
):
    import importlib

    from aiohttp_boilerplate.bootstrap.web_app import create_app

    bootstrap = importlib.import_module("aiohttp_boilerplate.bootstrap.web_app")
    monkeypatch.setattr(
        bootstrap, "_load_routes", lambda config, app: app.router.add_view("/", view)
    )
    monkeypatch.setattr(bootstrap, "_load_optional_setup", lambda *args: None)
    monkeypatch.setattr(bootstrap, "_load_middlewares", lambda paths: list(middlewares))
    return create_app(
        AppConfig.from_mapping(
            {
                "atomic_request_methods": methods,
                "CORS_ALLOWED_ORIGINS": "https://app.example.com" if cors else "",
                "CORS_ALLOW_CREDENTIALS": cors,
                "openapi_enabled": openapi,
            }
        ),
        db_pool=pool,
        auth_session=object(),
    )


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
@pytest.mark.parametrize("strategy", ["middleware", "view", "combined"])
@pytest.mark.parametrize("failure", ["none", "auth", "validation", "hook", "serialization"])
async def test_postgres_crud_full_lifecycle(
    postgres_pool,
    aiohttp_client,
    monkeypatch,
    method,
    strategy,
    failure,
):
    from functools import wraps

    from marshmallow import Schema, fields

    from aiohttp_boilerplate.models import Manager
    from aiohttp_boilerplate.views import AtomicCreateView, AtomicUpdateView
    from aiohttp_boilerplate.views.create import CreateView
    from aiohttp_boilerplate.views.update import UpdateView

    pool = postgres_pool

    class Widget(Manager):
        __table__ = "atomic_widgets"

    class WidgetSchema(Schema):
        id = fields.Integer(required=True)

    existing = Widget(pool)
    if method != "POST":
        await existing.insert({"id": 1})

    async def write(value):
        await Widget(pool).insert({"id": value})

    @web.middleware
    async def application(request, handler):
        await write(3)
        response = await handler(request)
        await write(7)
        return response

    def authenticated(handler):
        @wraps(handler)
        async def wrapped(self):
            await write(2)
            if failure == "auth":
                raise web.HTTPForbidden()
            response = await handler(self)
            await write(6)
            return response

        return wrapped

    base = (
        (AtomicCreateView if method == "POST" else AtomicUpdateView)
        if strategy in ["view", "combined"]
        else (CreateView if method == "POST" else UpdateView)
    )

    class View(base):
        def get_model(self):
            return Widget

        def get_schema(self):
            return WidgetSchema

        async def validate(self, data):
            if failure == "validation":
                raise web.HTTPBadRequest()
            return data

        async def before_update(self, data):
            self.obj = existing
            return data

        async def after_create(self, data):
            async with acquire_connection(pool) as conn:
                await conn.execute("INSERT INTO atomic_widgets VALUES (4)")
            if failure == "hook":
                raise RuntimeError("audit failure")
            return {}

        after_update = after_create

        async def get_data(self, obj):
            await obj.get_by_id(10)
            await write(5)
            return {"id": obj.id, "result": object() if failure == "serialization" else "ok"}

    setattr(View, method.lower(), authenticated(getattr(View, method.lower())))
    app = framework_app(
        monkeypatch,
        pool,
        View,
        methods=(method,) if strategy in ["middleware", "combined"] else (),
        middlewares=[application],
    )
    client = await aiohttp_client(app)
    response = await client.request(method, "/", json={"id": 10})
    assert response.status == (
        {"auth": 403, "validation": 400}.get(failure, 500)
        if failure != "none"
        else 201
        if method == "POST"
        else 200
    )
    async with pool.acquire() as conn:
        ids = [row["id"] for row in await conn.fetch("SELECT id FROM atomic_widgets ORDER BY id")]
    if failure == "none":
        assert ids == [2, 3, 4, 5, 6, 7, 10]
    else:
        assert ids == ([1] if method != "POST" else []) + ([3] if strategy == "view" else [])


@pytest.mark.parametrize("combined", [False, True])
async def test_postgres_view_boundary_and_callback_ownership(
    postgres_pool,
    aiohttp_client,
    monkeypatch,
    combined,
):
    pool = postgres_pool
    events = []

    @web.middleware
    async def later_error(request, handler):
        response = await handler(request)
        events.append("middleware after")
        assert response.status == 204
        raise RuntimeError("after view")

    class View(AtomicView):
        async def delete(self):
            await SQL("atomic_widgets", pool).execute("INSERT INTO atomic_widgets VALUES (1)", {})
            on_commit(lambda: events.append("callback"))
            return web.Response(status=204)

    app = framework_app(
        monkeypatch, pool, View, methods=("DELETE",) if combined else (), middlewares=[later_error]
    )
    client = await aiohttp_client(app)
    assert (await client.delete("/")).status == 500
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM atomic_widgets") == (0 if combined else 1)
    assert events == (["middleware after"] if combined else ["callback", "middleware after"])


async def test_nested_dispatch_adapter_exclusions_and_callbacks(monkeypatch, aiohttp_client):
    from aiohttp_boilerplate.views import OptionsViewMixin
    from aiohttp_boilerplate.views.options import OptionsView

    pool = Pool()
    events = []

    class Target(AtomicView):
        atomic_methods = ("POST",)

        async def post(self):
            assert self.conn is pool.conn
            on_commit(lambda: events.append("callback"))
            return web.Response(status=204)

        async def delete(self):
            # Exclusion cannot escape the adapter/middleware owner.
            assert self.conn is pool.conn
            return web.Response(status=204)

    class Adapter(AtomicViewMixin, OptionsViewMixin, web.View):
        async def post(self):
            return await Target(self.request)

        async def delete(self):
            return await Target(self.request).delete()

    app = framework_app(monkeypatch, pool, Adapter, methods=("POST", "DELETE"))
    client = await aiohttp_client(app)
    for method in ["POST", "DELETE"]:
        assert (await client.request(method, "/")).status == 204
    assert pool.acquired == pool.released == 2
    assert pool.conn.events.count("begin") == pool.conn.events.count("commit") == 2
    assert events == ["callback"]

    # Atomic classes preserve inherited 405/Allow and add no methods.
    class OnlyPost(AtomicView):
        atomic_methods = ()

        async def post(self):
            return web.Response()

    app2 = framework_app(monkeypatch, pool, OnlyPost, cors=True)
    client2 = await aiohttp_client(app2)
    for method in ["GET", "HEAD", "PUT", "PATCH", "DELETE"]:
        result = await client2.request(method, "/", headers={"Origin": "https://app.example.com"})
        assert result.status == 405
        assert set(result.headers["Allow"].split(",")) == {"OPTIONS", "POST"}
    for headers in [
        {},
        {"Origin": "https://app.example.com"},
        {
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
        },
    ]:
        assert (await client2.options("/", headers=headers)).status == 200
    assert (
        await client2.options(
            "/",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "DELETE",
            },
        )
    ).status == 403
    assert (await client2.post("/")).status == 200
    assert pool.acquired == 2
    assert not issubclass(OptionsView, AtomicViewMixin)


async def test_success_exception_through_joined_sql_remains_rollback_only():
    pool = Pool()
    req = request(pool)

    async def handler():
        async with SQL("widgets", pool).transaction():
            raise web.HTTPNoContent()

    with pytest.raises(TransactionScopeError, match="rollback-only"):
        await http_transaction(req, handler, kind="view")
    assert pool.conn.events == ["begin", "rollback", "release"]


async def test_deferred_payload_and_prepared_response_rejected():
    from aiohttp.payload import BytesPayload

    pool = Pool()
    for response in [web.Response(body=BytesPayload(b"deferred")), web.Response()]:
        if not response.body:
            response._eof_sent = True

        async def handler(response=response):
            return response

        with pytest.raises(TransactionScopeError):
            await http_transaction(request(pool), handler, kind="view")
    assert "commit" not in pool.conn.events


async def test_expired_child_context_cannot_acquire_or_replace_owner():
    pool, other = Pool(), Pool()
    req = request(pool)
    ready = asyncio.Event()
    task = None

    async def child():
        await ready.wait()
        with pytest.raises(TransactionScopeError):
            async with acquire_connection(other):
                pass
        with pytest.raises(TransactionScopeError):
            await SQL("widgets", pool).get_connection()

    async def handler():
        nonlocal task
        task = asyncio.create_task(child())
        return web.Response()

    await http_transaction(req, handler, kind="view")
    ready.set()
    await task
    assert pool.acquired == 1
    assert other.acquired == 0


async def test_conflicting_standalone_cached_connection_is_not_replaced():
    pool = Pool()
    sql = SQL("widgets", pool)
    conflict = object()
    sql.conn = conflict

    async def handler():
        with pytest.raises(TransactionScopeError):
            await sql.get_connection()
        assert sql._conn is conflict
        return web.Response()

    await http_transaction(request(pool), handler, kind="view")
    assert pool.acquired == pool.released == 1


async def test_postgres_concurrent_requests_use_one_connection_safely(
    postgres_pool,
    aiohttp_client,
    monkeypatch,
):
    pool = postgres_pool

    class View(AtomicView):
        async def post(self):
            value = int(await self.request.text())
            await self.conn.execute("INSERT INTO atomic_widgets VALUES ($1)", value)
            await asyncio.sleep(0.01)
            assert await self.conn.fetchval("SELECT count(*) FROM atomic_widgets") >= 1
            return web.Response(status=204)

    client = await aiohttp_client(framework_app(monkeypatch, pool, View, methods=("POST",)))
    responses = await asyncio.wait_for(
        asyncio.gather(*[client.post("/", data=str(value)) for value in range(5)]), timeout=5
    )
    assert all(response.status == 204 for response in responses)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM atomic_widgets") == 5


async def test_explicit_connection_assignment_still_borrows_owner():
    pool = Pool()
    req = request(pool)
    sql = SQL("widgets", pool)

    async def handler():
        sql.conn = get_request_connection(req)

        async def child():
            with pytest.raises(TransactionScopeError):
                sql.conn = None

        await asyncio.create_task(child())
        await sql.release()
        assert pool.released == 0
        return web.Response()

    await http_transaction(req, handler, kind="view")
    assert pool.acquired == pool.released == 1


async def test_callback_cancelled_error_keeps_committed_response():
    pool = Pool()
    events = []

    async def callback():
        raise asyncio.CancelledError("callback failed")

    async def handler():
        on_commit(callback)
        on_commit(lambda: events.append("second callback"))
        return web.Response(status=204)

    assert (await http_transaction(request(pool), handler, kind="view")).status == 204
    assert events == ["second callback"]


async def test_ordinary_view_accessor_under_middleware(monkeypatch, aiohttp_client):
    from aiohttp_boilerplate.views.options import OptionsView

    pool = Pool()

    class View(OptionsView):
        async def post(self):
            assert self.conn is pool.conn
            with pytest.raises(AttributeError):
                self.conn = object()
            return web.Response(status=204)

    client = await aiohttp_client(framework_app(monkeypatch, pool, View, methods=("POST",)))
    assert (await client.post("/")).status == 204


async def test_masked_inherited_method_does_not_acquire():
    class Parent(AtomicViewMixin, web.View):
        async def post(self):
            return web.Response()

    class Adapter(Parent):
        post = None

    pool = Pool()
    with pytest.raises(web.HTTPMethodNotAllowed):
        await Adapter(request(pool))
    assert pool.acquired == 0


@pytest.mark.parametrize("start_failure", [False, True])
async def test_savepoint_setup_failure_is_rollback_only(start_failure):
    pool = Pool()

    async def handler():
        original = pool.conn.transaction
        if start_failure:
            transaction = Transaction(pool.conn)

            async def fail_start():
                raise RuntimeError("savepoint setup")

            transaction.start = fail_start
            pool.conn.transaction = Mock(return_value=transaction)
        else:
            pool.conn.transaction = Mock(side_effect=RuntimeError("savepoint setup"))
        with pytest.raises(RuntimeError, match="savepoint setup"):
            async with recovery_savepoint():
                pass
        pool.conn.transaction = original
        return web.Response()

    with pytest.raises(TransactionScopeError, match="rollback-only"):
        await http_transaction(request(pool), handler, kind="view")


@pytest.mark.parametrize("combined", [False, True])
async def test_atomic_http_prepare_guard_prevents_bytes(aiohttp_client, monkeypatch, combined):
    pool = Pool()

    class View(AtomicView):
        async def post(self):
            response = web.Response(text="must never be sent")
            with pytest.raises(TransactionScopeError):
                await response.prepare(self.request)
            assert not response.prepared
            assert self.request._payload_writer.output_size == 0
            return web.Response(text="replacement success")

    client = await aiohttp_client(
        framework_app(monkeypatch, pool, View, methods=("POST",) if combined else ())
    )
    response = await client.post("/")
    assert response.status == 500
    assert "success" not in await response.text()
    assert pool.conn.events == ["begin", "rollback", "release"]


@pytest.mark.parametrize("strategy", ["middleware", "view", "combined"])
@pytest.mark.parametrize("status", [204, 302])
async def test_http_success_control_flow(aiohttp_client, monkeypatch, strategy, status):
    from aiohttp_boilerplate.views.options import OptionsView

    pool = Pool()
    base = OptionsView if strategy == "middleware" else AtomicView

    class View(base):
        async def delete(self):
            exception = (
                web.HTTPNoContent(reason="Completed")
                if status == 204
                else web.HTTPFound("/destination", reason="Moved", body=b"redirect body")
            )
            exception.headers["X-Result"] = "kept"
            exception.set_cookie("result", "yes", httponly=True)
            raise exception

    client = await aiohttp_client(
        framework_app(monkeypatch, pool, View, methods=("DELETE",) if strategy != "view" else ())
    )
    response = await client.delete("/", allow_redirects=False)
    assert response.status == status
    assert response.headers["X-Result"] == "kept"
    assert response.cookies["result"].value == "yes"
    assert response.cookies["result"]["httponly"]
    assert response.reason == ("Completed" if status == 204 else "Moved")
    if status == 302:
        assert response.headers["Location"] == "/destination"
        assert await response.read() == b"redirect body"
    assert pool.conn.events == ["begin", "SELECT 1", "commit", "release"]


async def test_atomic_openapi_and_read_methods_stay_unchanged(aiohttp_client, monkeypatch):
    from functools import wraps

    from aiohttp_apispec import docs

    from aiohttp_boilerplate.views import OptionsViewMixin

    pool = Pool()

    def forwarding(handler):
        @wraps(handler)
        async def wrapper(self):
            return await handler(self)

        return wrapper

    class Target(AtomicView):
        @docs(summary="Explicit DELETE operation")
        async def delete(self):
            return web.Response(status=204)

    class Adapter(AtomicViewMixin, OptionsViewMixin, web.View):
        @docs(summary="Read operation")
        async def get(self):
            return web.Response()

        async def head(self):
            return web.Response()

        delete = forwarding(Target.delete)

    app = framework_app(
        monkeypatch, pool, Adapter, methods=("POST", "PUT", "PATCH", "DELETE"), openapi=True
    )
    client = await aiohttp_client(app)
    for method in ["GET", "HEAD", "OPTIONS"]:
        assert (await client.request(method, "/")).status == 200
    assert (await client.get("/healthcheck")).status == 200
    assert pool.acquired == 0
    spec = app["swagger_dict"]
    assert set(spec["paths"]["/"]) == {"get", "delete"}
    assert spec["paths"]["/"]["delete"]["summary"] == "Explicit DELETE operation"
    assert (await client.delete("/")).status == 204
    assert pool.acquired == pool.released == 1


async def test_postgres_standalone_nested_transaction_retains_savepoint(postgres_pool):
    pool = postgres_pool
    sql = SQL("atomic_widgets", pool)
    async with sql.transaction():
        await sql.execute("INSERT INTO atomic_widgets VALUES (1)", {})
        with pytest.raises(RuntimeError):
            async with sql.transaction():
                await sql.execute("INSERT INTO atomic_widgets VALUES (2)", {})
                raise RuntimeError("recover nested standalone transaction")
        assert await sql.conn.fetchval("SELECT count(*) FROM atomic_widgets") == 1
        await sql.execute("INSERT INTO atomic_widgets VALUES (3)", {})
    assert sql.conn is None
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM atomic_widgets") == 2


@pytest.mark.parametrize("failure", ["acquire", "start"])
async def test_owner_setup_failure_never_calls_handler(failure):
    from unittest.mock import AsyncMock

    pool = Pool()
    req = request(pool)
    if failure == "acquire":
        pool.acquire = AsyncMock(side_effect=RuntimeError("setup failure"))
    else:
        transaction = Transaction(pool.conn)
        transaction.start = AsyncMock(side_effect=RuntimeError("setup failure"))
        pool.conn.transaction = Mock(return_value=transaction)

    async def handler():
        pytest.fail("must not dispatch")

    with pytest.raises(RuntimeError, match="setup failure"):
        await http_transaction(req, handler, kind="view")
    with pytest.raises(TransactionScopeError):
        get_request_connection(req)
    assert pool.released == (1 if failure == "start" else 0)


@pytest.mark.parametrize("pool_style", ["canonical", "alias", "conflicting-alias"])
async def test_model_views_resolve_canonical_pool_and_legacy_alias(pool_style):
    from aiohttp_boilerplate.models import Manager
    from aiohttp_boilerplate.views import AtomicCreateView
    from aiohttp_boilerplate.views.list import ListView

    class Widget(Manager):
        __table__ = "widgets"

    class Create(AtomicCreateView):
        def get_model(self):
            return Widget

        def get_schema(self):
            return None

    class List(ListView):
        def get_model(self):
            return Widget

        def get_schema(self):
            return None

    pool = Pool()
    req = request(pool)
    if pool_style == "alias":
        del req.app[DB_POOL_KEY]
        req.app.db_pool = pool
    elif pool_style == "conflicting-alias":
        req.app.db_pool = object()
    for view_type in [Create, List]:
        view = view_type(req)
        assert view.db_pool is pool
        assert view.obj.db_pool is pool
        if isinstance(view, List):
            assert view.objects.db_pool is pool
    assert pool.acquired == 0
