# Migrating to 0.11.0

Python 3.12–3.14, aiohttp `>=3.14.3,<3.15` and the existing Marshmallow 4 requirements are unchanged. The release
adds HTTP transactions without enabling them by default. Ordinary `CreateView` and `UpdateView` retain their
existing local write transactions when neither strategy is enabled. Model constructors are unchanged.

## Choose the owner

For a service with database work in application middleware, set application configuration explicitly:

```python
config = AppConfig.from_mapping({
    "app_dir": "app",
    "atomic_request_methods": ("POST", "PUT", "PATCH", "DELETE"),
})
app = create_app(config)
```

When using `load_config()`, put `atomic_request_methods` in your application's `config` mapping. Both this setting
and a view's `atomic_methods` accept a tuple/list of unique uppercase write methods only: `POST`, `PUT`, `PATCH`,
`DELETE`. The empty tuple disables selection. Strings, lowercase names, duplicate methods, and read methods raise
`ConfigurationError`. No method selection creates a route or adds a handler.

Middleware order is request logging → JSON errors → server header → transaction → application middleware → handler.
Authentication, validation, every model/raw helper call, hooks, response loading and JSON encoding must finish
inside this boundary. Logging, error and header middleware outside it must not access the database or defer success
encoding. GET, HEAD, OPTIONS and the framework GET healthcheck retain their previous behavior.

For individual views, leave middleware selection empty and change the base class:

```python
from aiohttp_boilerplate.views import AtomicCreateView, AtomicUpdateView

class WidgetCreate(AtomicCreateView):
    def get_model(self):
        return Widget

    def get_schema(self):
        return WidgetSchema

class WidgetUpdate(AtomicUpdateView):
    def get_model(self):
        return Widget

    def get_schema(self):
        return WidgetSchema

    async def on_start(self):
        await self.obj.get_by_id(await self.get_id())

app.router.add_view("/widgets", WidgetCreate)
app.router.add_view("/widgets/{id}", WidgetUpdate)
```

View ownership wraps the complete public dispatch, including overridden `post`/`put`/`patch`/`delete` methods,
authentication decorators, validation, hooks and serialization. View construction and surrounding application
middleware are outside this guarantee. Constructors must not perform database work. A middleware error after an
atomic view returns cannot undo that view's committed writes. Its commit callbacks already ran before returning to
that middleware.

Middleware and atomic views can be combined: use the configuration above and register the atomic classes. The
middleware owns one connection and one transaction; atomic views join it without another acquisition, transaction or
savepoint. A view's narrower `atomic_methods` cannot opt out of an enclosing middleware transaction. A joined view's
returned error or exception marks the owner rollback-only even if surrounding code catches it or substitutes a
success response.

## Custom DELETE handlers and routing adapters

There is no generic DeleteView. Implement the business operation explicitly:

```python
from aiohttp import web
from aiohttp_boilerplate.views import AtomicView

class WidgetDelete(AtomicView):
    async def delete(self):
        widget = Widget(self.db_pool)
        await widget.get_by_id(int(self.request.match_info["id"]))
        await widget.delete()
        return web.Response(status=204)

app.router.add_view("/widgets/{id}/removal", WidgetDelete)
```

For a custom view base, put `AtomicViewMixin` first. It has no initializer and adds no HTTP methods.
`OptionsViewMixin` separately adds initialization-free CORS-aware OPTIONS. It preserves custom constructors and
methods such as `request_data()`. Put custom OPTIONS content in `_options()`, returning the original JSON-compatible
body.

A final routing adapter that directly calls another view's handler must itself be atomic. A direct `.post()` call
bypasses the delegated class's awaitable dispatch. Preserve metadata with `functools.wraps`:

```python
from functools import wraps
from aiohttp import web
from aiohttp_boilerplate.views import AtomicViewMixin, OptionsViewMixin

class CreateAdapter(AtomicViewMixin, OptionsViewMixin, web.View):
    @wraps(WidgetCreate.post)
    async def post(self):
        return await WidgetCreate(self.request).post()

app.router.add_view("/widget-command", CreateAdapter)
```

Alternatively, delegate through `await WidgetCreate(self.request)` when it preserves the endpoint's method contract.
Register each URL once with `add_view()`. Only expose the intended methods; inheriting UpdateView also exposes
PUT/PATCH. The mixins preserve aiohttp dispatch, 405/Allow, OpenAPI metadata and existing CORS origin/header/method
validation. Atomic selection never adds DELETE to an endpoint. Plain, Origin-only and preflight OPTIONS retain their
response bodies.

## Connections and helpers

```python
from aiohttp_boilerplate.transactions import acquire_connection, get_request_connection, on_commit

async def raw_helper(pool):
    async with acquire_connection(pool) as conn:
        return await conn.fetchval("SELECT count(*) FROM widgets")
```

Ordinary model/SQL operations automatically borrow the active connection, including models created before the
request. Borrowers detach after each use and never release the owner's connection. `SQL.transaction()` joins the
HTTP scope; remove redundant command transactions when migrating a service. Outside HTTP, existing standalone
transactions and nested asyncpg savepoints are unchanged. `acquire_connection(pool)` owns a connection only when no
HTTP scope exists.

`get_request_connection(request)` and read-only `self.conn` on framework/atomic view bases expose the active
connection. They raise `TransactionScopeError` outside the compatible owning request task. The canonical pool is
`app[DB_POOL_KEY]`; `app.db_pool` remains a fallback alias. The actual supplied pool and its ownership remain
intact; there is no pool proxy. Different pools, inherited child tasks and expired/cached borrowed SQL connections
are rejected before acquisition.

Use raw references sequentially within the request task. Do not retain them after scope exit, pass them to a
background task or use concurrent database gathers on one connection. Framework checks cannot intercept arbitrary
direct use of an already captured asyncpg connection. Independent requests retain independent context and safely
queue on a one-slot pool.

## Failures, recovery and completion

Transactions use READ COMMITTED. Raised errors, cancellation before commit and responses with status >=400 roll
back. Raised successful `HTTPException` responses (204, redirects, etc.) become ordinary buffered responses with
their status, reason, body, headers and cookies preserved. An exception escaping a joined SQL/command transaction
marks rollback-only, even a successful HTTP exception; helpers should return successful results instead of raising
HTTP control flow.

Before success commits, the owner checks rollback-only state and runs `SELECT 1` to detect swallowed database errors
that left PostgreSQL aborted. Neither failure can emit the buffered success response. Acquisition, transaction
setup, serialization and commit failures release the connection and follow normal error handling. Cancellation
during COMMIT has an uncertain outcome: use business command identity/replay handling to reconcile it.

Only an unprepared `web.Response` containing encoded in-memory bytes or no body is allowed. Empty 204 responses and
JSON DELETE responses work. FileResponse, StreamResponse, deferred Payload bodies and already prepared responses are
rejected before commit. Early `prepare()` is guarded even with middleware disabled. Catching that error, including
inside a recovery savepoint, cannot restore the right to commit. Framework setup installs an inert-outside-scope
prepare wrapper and response-prepare signal guard; view dispatch also ensures the wrapper is installed for manually
assembled apps.

Use explicit savepoints only when the algorithm deliberately recovers a database error:

```python
from asyncpg import UniqueViolationError
from aiohttp_boilerplate.transactions import recovery_savepoint

try:
    async with recovery_savepoint():
        await create_optional_record()
        on_commit(invalidate_optional_record_cache)
except UniqueViolationError:
    pass
```

Successful savepoint rollback restores the rollback-only and callback registration checkpoints. Callbacks registered
inside the rolled-back savepoint are discarded. Savepoint setup/commit/rollback failures mark the owner
rollback-only. Preparation violations remain fatal across savepoint recovery.

`on_commit(callback)` accepts sync or async callbacks and requires an active HTTP scope. The owner runs them once
after commit, release and request-accessor cleanup. Joined scopes only register. Rollback runs none. Framework
database operations are prohibited in callbacks and their child tasks, including through cached SQL connections.
Cache invalidation belongs here; transactional database/audit work belongs in the handler. Callback failures are
logged and preserve the committed success response, including callback `CancelledError` failures.

## Verification and release

Run tests serially against an explicitly configured disposable PostgreSQL database:

```sh
export ATOMIC_TEST_DSN='postgresql://test_user:test_password@127.0.0.1:5432/test_database'
python -m pytest -q
python -m ruff check .
python -m mypy aiohttp_boilerplate
python -m build
python -m twine check dist/*
```

The suite creates/truncates `atomic_widgets`; never point it at a service database. A missing DSN fails the required
integration fixture rather than silently skipping it. Test installed wheel and sdist from outside the source tree,
run `pip check`, exercise startup/imports and repeat the suite on Python 3.12–3.14. The declared aiohttp range
currently has one published version, 3.14.3. Recheck any newly supported patch before widening/updating the tested
range because dispatch uses aiohttp's internal `_iter()` and response preparation hook.

Version/tag availability must be rechecked immediately before publishing immutable `v0.11.0`; update consumers
together if it has been taken. Building packages does not publish them or upgrade/deploy any application. See [ADR
0010](adr/0010-http-transaction-ownership.md) and [the example](../examples/atomic_transactions.py).
