# ADR 0010: Shared HTTP transaction ownership

Status: accepted for 0.11.0. Extends ADR 0007's local CRUD transactions without changing disabled-mode behavior.

## Context

CRUD-local transactions leave authentication, application middleware, later hooks, response loading and encoding
outside the commit boundary. Explicit connection parameters in every model/helper would spread ownership through
applications. Services also need an additive view alternative when surrounding middleware does not require atomic
database work.

## Decision

One engine owns the complete buffered HTTP result. Opt-in middleware wraps application middleware and all handlers
for configured POST/PUT/PATCH/DELETE methods. Initialization-free atomic view mixins wrap aiohttp's awaitable
`_iter()` dispatch and delegate to `super()._iter()`, preserving public-handler overrides, decorators, method checks
and response assertions. AtomicCreateView and AtomicUpdateView reuse existing lifecycles. AtomicView supplies an
explicit custom-handler base; there is no generic DELETE lifecycle. Final direct-forwarding routing adapters must
also participate in atomic dispatch.

A ContextVar records pool identity, connection, request, owning task, scope kind and lifecycle. Middleware, views
and SQL transactions join a compatible active HTTP scope without acquisition or implicit savepoints. Inherited,
incompatible and expired context is rejected without replacement or a second pool acquisition. Scope references
remain visibly expired in copied contexts. The canonical application pool remains unchanged and retains its
independent resource ownership.

Model SQL instances resolve ownership at use time. Borrowers detach after use; the owner alone returns the
connection. A guarded SQL.conn property also validates cached/explicitly assigned connections. An
initialization-free shared accessor supplies read-only self.conn to ordinary framework views and custom atomic
bases. Raw helper acquisition uses the same context validation. Existing standalone SQL transactions and nested
savepoints remain unchanged outside HTTP scopes.

The owner uses READ COMMITTED and commits only after dispatch produces an unprepared web.Response with an encoded
in-memory body or no body. HTTP success exceptions are normalized with metadata preserved. Returned errors, escaping
exceptions and cancellation roll back. Joined transaction failures mark rollback-only even when caught; successful
HTTP normalization never clears it. Before commit, rollback-only validation and SELECT 1 detect swallowed
transaction failures.

A context-aware StreamResponse.prepare wrapper rejects early preparation before bytes leave the process. Framework
setup installs it even for view-only applications; an on_response_prepare guard covers subclasses that use aiohttp's
preparation signal directly. The wrapper is inert outside an active HTTP scope. Preparing cannot be recovered
through a savepoint: a separate sticky response-violation flag survives recovery. Buffered-response checks reject
files, streams and deferred payloads even if the handler never prepares them. The current tested aiohttp 3.14.3
internal hooks must be rechecked when the supported dependency range changes.

Explicit recovery_savepoint supports intentional database-error recovery. Rollback restores rollback-only and
callback checkpoints; setup/commit/rollback failures mark the owner rollback-only. Only the owner executes on_commit
callbacks, once after commit, release and request cleanup, with framework database access prohibited. Callback
failures are logged and cannot undo committed success. View-owned callbacks finish before surrounding middleware
resumes.

## Consequences

Middleware is required to include surrounding application work. View construction and middleware around a view-owned
transaction are outside its guarantee; later middleware errors cannot undo an earlier view commit. Outer framework
logging/error/header handling must remain database-free and cannot defer successful response serialization.

Atomic handlers must buffer responses and use one task's connection sequentially. Child-task gathers require
restructuring when they perform database work in an atomic request. Already captured raw asyncpg references cannot
be intercepted; applications must not retain or share them. Cancellation during COMMIT may have an uncertain
outcome, so durable business command identity and retry handling remain necessary. Remote commands are outside a
service's local database rollback.

The preparation hook is deliberately isolated in one engine. No transparent pool proxy, model constructor changes,
new routing verbs, implicit recovery savepoints, or default enablement are introduced. Tests cover real
one-connection PostgreSQL pools, all four ownership configurations, CRUD/decorator/serialization boundaries,
callback order, nested ownership, response control flow, CORS, OpenAPI and standalone compatibility.
