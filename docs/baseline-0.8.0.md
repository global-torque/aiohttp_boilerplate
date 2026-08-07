# Untouched 0.8.0 baseline

Captured from parent commit `86b10f6` on CPython 3.14.6 with aiohttp 3.14.3.

- Importing runtime modules without deployment variables failed at missing
  `HOST`; configured import succeeded.
- Default collection found three tests before two collection errors. With all
  required variables, nine tests passed; twelve functions in legacy
  `tests.py` files were not collected and used removed `asyncio.coroutine`.
- Branch coverage was 23%; Ruff reported 149 findings, Black would reformat 38
  files, and strict mypy reported 516 legacy typing findings.
- A direct wheel passed `twine check` but had no `Requires-Python`. Building a
  wheel from the sdist failed because `setup.py` read an omitted requirements
  file.
- Reproductions confirmed interpolated JSONB paths/payloads, unscoped writes,
  permissive credentialed CORS, cross-request logger/context state, per-call
  auth sessions, double pool cleanup, cache identity collisions, and schema
  join/field errors.
- The pinned `investment-api` consumer imports the old module-level `conf` and
  reads custom `app.config` service URLs from it. Version 0.9 preserves custom
  values in injected `AppConfig`, but requires the documented move away from
  import-time `conf`.

The 0.9 regression suite records those behaviors directly instead of treating
tooling/discovery changes as baseline improvement.
