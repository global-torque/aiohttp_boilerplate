# Migration to 0.9

- Prefer `create_app(config, db_pool=..., auth_session=...)`. Injected resources
  remain caller-owned; pass `take_ownership=True` only when the app should close
  them. `start_web_app()` retains legacy ownership and is deprecated.
- Read configuration once with `load_config()` and access it through
  `APP_CONFIG_KEY`. `app.conf`, `app.db_pool`, `request.context`, and
  `request.log` remain deprecated aliases through 0.9.
- Application-specific configuration may use `Environment`. Calls without a
  default require a non-empty value; missing and empty values use an explicit
  default when one is supplied. `Environment` never reads `.env` files.
  It exposes `str`, `bool`, `int`, `float`, and comma-separated `list` readers.
  Booleans accept `1/true/yes/on` and `0/false/no/off`; floats must be finite;
  list items are trimmed and empty items omitted. Invalid typed values raise
  `ConfigurationError`, and `default=None` is preserved for unset values.
- The import-time module global `from aiohttp_boilerplate.config import conf`
  is removed. Replace it with `request.app[APP_CONFIG_KEY]` in request code;
  `request.app.conf` is the temporary 0.9 alias. Values merged from
  `<app_dir>.config` remain available through that immutable mapping, including
  application-specific keys such as service URLs.
- Errors now use one JSON envelope and never expose internal exception text.
- Caching is off unless an endpoint declares an identity/tenant callback. The
  first contract is TTL-only and never caches mutations, random, or skip
  requests.
- Computed Marshmallow fields are not selected from the DB. `db_field=False`
  opts out, a string maps an expression, `load_only` is excluded, and DB-backed
  `dump_only` remains selected.
- DB-only `after_*_in_transaction` hooks run before commit on the same
  connection. Existing `after_*` hooks remain post-commit; their failure does
  not roll back committed data.

Rollback: callers may temporarily return to `start_web_app()` and compatibility
aliases, but must preserve one-close resource ownership, auth session reuse,
JSON error status preservation, cache identity isolation, and transaction
semantics.
