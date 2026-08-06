# aiohttp-boilerplate

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
- [ ] during 500 error should return proper cors
- [ ] eliminate JSONError exception
- [ ] Add more examples
- [ ] Add integration with prometeus
- [ ] Move to ujson instead of json library
