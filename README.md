# aiohttp-boilerplate

Version 0.9 supports CPython 3.12–3.14. Applications are created explicitly:

```python
from aiohttp_boilerplate.bootstrap import create_app
from aiohttp_boilerplate.config import AppConfig

config = AppConfig.from_mapping({"app_dir": "app"})
app = create_app(config)
```

Deployment entry points may call `await load_config()` to read and validate the
environment once. Library imports never read `.env` or require deployment
settings.

Services can use the same public reader for application-specific settings:

```python
from aiohttp_boilerplate.config import Environment

env = Environment()
wallet_api = env.str("WALLET_API")
debug = env.bool("DEBUG", default=False)
allowed_hosts = env.list("ALLOWED_HOSTS", default=[])
```

Missing values and empty strings are treated as unset. They raise
`ConfigurationError` when no default is provided and otherwise return the
explicit default. The reader never loads `.env` files implicitly.

Available readers are `str`, `bool`, `int`, `float`, and `list`. Boolean values
accept `1/true/yes/on` and `0/false/no/off`, case-insensitively. Floats must be
finite. Lists are comma-separated, trim surrounding whitespace, and omit empty
items. Invalid typed values raise `ConfigurationError` naming the setting.
Passing `default=None` preserves `None` for missing or empty values. String
values, including whitespace-only strings, are otherwise returned unchanged.

## CORS

Set `CORS_ALLOWED_ORIGINS` to comma-separated exact HTTP(S) origins. An empty
value disables CORS and creates no preflight routes. `DOMAIN` is rejected; list
each allowed subdomain explicitly. See `docs/migration-0.8.1.md`.

## Errors, ownership, and compatibility

Errors use `{"error": {"status": ..., "message": ..., "details": ...}}`.
Factory-created resources are application-owned. Injected resources remain
caller-owned unless `take_ownership=True`. The `app.conf`, `app.db_pool`,
`request.context`, `request.log`, and `start_web_app()` aliases remain with
deprecation warnings for the 0.9 compatibility period.

## Response serialization

Views with a Marshmallow schema serialize model data through `Schema.dump()`.
This applies field serializers, nested schemas, `load_only`, `data_key`, and
dump hooks before creating the JSON response.

Python `Decimal` values that reach the generic JSON fallback are encoded as
canonical base-10 strings rather than binary floating-point numbers. Fractional
trailing zeroes are removed, so `Decimal("5001.0")` becomes `"5001"`. Define an
explicit Marshmallow decimal field when an endpoint needs different formatting.
This behavior avoids precision loss but changes the JSON type from number to
string compared with releases before 0.8.0.

# ToDo
- [ ] Create real simple ToDo example and create example with using different profiles and jsonb fields
- [ ] for some reason logs goes to stderr in podman
- [ ] add service info (name, version, githash) to log to service context
- [ ] eliminate JSONError exception
- [ ] Add more examples
- [ ] Add integration with prometeus
- [x] Use the standard-library JSON implementation throughout the framework
