# Migration to the 0.8.1 safety contract

- Replace `DOMAIN=example.com` with exact serialized origins, for example
  `CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com`.
  Add `aiohttp-cors>=0.8.1,<0.9`; remove explicit `OPTIONS` routes and make
  class views inherit `CorsViewMixin` (framework views already do).
- Empty update/delete scopes now fail before acquiring a connection. Use
  explicitly named `update_all()` or `delete_all()` only when a full-table
  operation is intentional.
- JSONB identifiers must be simple PostgreSQL identifiers; payloads and paths
  are bound without apostrophe stripping.
- Auth 204 returns `{}`; rejected credentials remain 403, malformed successful
  responses are 502, and transport/timeouts are 503.
- Logs no longer contain credentials, bodies, validation values, or SQL values.

Rollback: do not restore the former CORS reflection or unbound/unscoped SQL.
Any rollback release must provide equivalent exact-origin enforcement, bound
JSONB values, explicit write scopes, and request-local logging.
