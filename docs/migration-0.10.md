# Migrating to 0.10

Version 0.10 keeps the framework's HTTP and schema behavior stable while moving
its runtime to Marshmallow 4 and replacing two legacy dependency paths.

## Marshmallow 4

The framework now requires `marshmallow>=4.3,<5`. Before upgrading a service,
remove Marshmallow 3-only usage such as schema `context=`, `pass_many=`, implicit
fields declared only through `Meta.fields`, field `missing=`/`default=`
arguments, and direct subclasses of removed field types.

Schemas should declare fields explicitly. Custom fields should subclass a
concrete Marshmallow 4 field and accept the current field serialization and
deserialization signatures. Schema hooks should accept keyword arguments.

The validated framework paths cover schema load and dump, validators, custom
fields, nested and joined schemas, generated JSON Schema, and generated OpenAPI.
Each consuming service must still run its startup and complete test suite before
upgrading from a 0.9.x release.

## JSON cache responses

`CacheMixin` now uses Python's standard `json` module with compact separators.
Cache misses, hits, expiry, and bypass return the same parsed JSON values as the
0.9 implementation. No alternate JSON serializer was added.

## aiohttp-apispec delivery

The internal `aiohttp-apispec` fork is installed from its immutable 3.1.0 wheel
with a SHA-256 fragment. The import path remains `aiohttp_apispec`.

## Rollback

Services that cannot yet run on Marshmallow 4 must remain pinned to
`aiohttp-boilerplate==0.9.1` (or its immutable source revision) and Marshmallow
3.26.x until their schema compatibility work is complete.
