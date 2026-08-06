# Changelog

## 0.8.0 - 2026-08-06

### Changed

- Serialize model-backed response data through the configured Marshmallow
  schema, including custom fields, nested schemas, aliases, `load_only`, and
  dump hooks.
- Serialize fallback Python `Decimal` values as lossless base-10 JSON strings
  instead of binary floating-point numbers. This is an intentional response
  contract change for endpoints that previously emitted decimal JSON numbers.

### Security

- Keep the obsolete `aiohttp_boilerplate/logging/logging-gcp.json`
  service-account file out of releases and ignore it to prevent accidental
  recommits.

### Upgrade notes

- Clients must treat decimal response values as strings.
- Services pinned to a Git commit must update their dependency pin and rebuild
  before receiving this behavior.
