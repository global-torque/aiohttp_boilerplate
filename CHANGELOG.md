# Changelog

## 0.9.0 - 2026-08-07

### Added

- Add immutable startup configuration, typed aiohttp keys, `create_app()`, and
  independently owned database/auth cleanup contexts.
- Add a public `Environment` reader for required and optional string, boolean,
  integer, float, and comma-separated list settings. Missing and empty values
  share the same fail-fast/default behavior.
- Add exact-origin `aiohttp-cors` integration and a routed healthcheck.
- Add centralized JSON errors, identity-aware TTL caching, transaction-local
  hooks, and regression coverage for the 0.8.1/0.9 contracts.

### Changed

- Bind JSONB payloads and paths, reject implicit full-table writes, preserve
  caller mappings, and log SQL templates without values.
- Reuse one auth session and map upstream rejection/malformed/unavailable
  responses to 403/502/503.
- Isolate request context and logging, remove import-time deployment reads,
  correct schema field/join handling, and make fixture sequence resets
  deterministic for empty tables.
- Require CPython 3.12+ and publish from `pyproject.toml` as version 0.9.0.

### Removed

- Remove the custom CORS and middleware healthcheck implementations, the
  `DOMAIN` fallback, implicit event-loop policy mutation, and process-wide
  logging hook installation.

### Upgrade and rollback

- Follow `docs/migration-0.8.1.md` and `docs/migration-0.9.md`. Rollback must
  retain an equivalent exact-origin CORS control and scoped/bound SQL writes.
- Replace the removed import-time `aiohttp_boilerplate.config.conf` global with
  the injected `APP_CONFIG_KEY`; custom application settings remain present in
  the immutable mapping.

## 0.8.0 - 2026-08-06

### Changed

- Serialize model-backed response data through the configured Marshmallow
  schema, including custom fields, nested schemas, aliases, `load_only`, and
  dump hooks.
- Serialize fallback Python `Decimal` values as lossless canonical base-10 JSON
  strings instead of binary floating-point numbers, removing insignificant
  fractional zeroes. This is an intentional response contract change for
  endpoints that previously emitted decimal JSON numbers.
- Raise runtime dependency minimums to the versions validated by the 0.8.0
  integration suite.

### Security

- Keep the obsolete `aiohttp_boilerplate/logging/logging-gcp.json`
  service-account file out of releases and ignore it to prevent accidental
  recommits.

### Upgrade notes

- Clients must treat decimal response values as strings.
- Services pinned to a Git commit must update their dependency pin and rebuild
  before receiving this behavior.
