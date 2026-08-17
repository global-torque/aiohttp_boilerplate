---
name: database-migrations
description: Work with WebDevelop/Torque PostgreSQL migrations in i-migration-service. Use when adding or reviewing schema/data migrations, deciding whether a database constraint is required, choosing enum and column types, using MIGRATION_* values, ordering migration files, validating hashes/application, or coordinating changes with services, i-models, views, and deployed migration jobs.
---

# Database Migrations

## Core Rules

- Use `../i-migration-service/migrations` as the canonical home for shared
  schema and seed changes. Do not create repo-local copies.
- Never edit an applied migration. Add a forward migration so stored hashes
  remain valid across deployed databases.
- Keep each migration focused on the requested schema transition.

## Golden Rule: Application Owns Logic

Keep business rules and validation in application code. Use PostgreSQL for the
minimum storage shape and relational integrity the application requires.

Do not add a constraint unless it is necessary. A request to "ensure",
"validate", or "reject" a value does not imply database enforcement.

Keep these in application code by default:

- formats, regexes, casing, normalization, ranges, and positivity;
- conditional column combinations, calculations, and rounding;
- workflow transitions, timestamps, counters, and retry state;
- blockchain data validation;
- security evidence, approvals, allowlists, compliance, provider, and policy
  rules.

Limit normal database constraints to required structural integrity: primary
keys, real foreign keys, database-wide uniqueness/idempotency, universally
required `NOT NULL`, and small stable enums. Existing checks are not precedent.
Do not move application logic into triggers, functions, generated columns, or
domains to bypass this rule.

Before adding any constraint:

1. Inspect every known writer and its application validation.
2. Identify the concrete corruption possible without the constraint.
3. Confirm the invariant applies to every row regardless of workflow,
   provider, policy, or application version.
4. Use the smallest structural mechanism that solves the problem.

Treat every new `CHECK` as an exception. Unless the user explicitly requested
the exact check, pause and ask for approval before writing it. Show the exact
expression, why application validation is insufficient, affected writers,
backfill/locking impact, and rollback.

## Privilege Changes

Do not add or change roles, ownership, memberships, `GRANT`, `REVOKE`, or
default privileges without explicit approval for the exact change. For such a
task, read [references/database-privileges.md](references/database-privileges.md)
completely before acting.

## Workflow

1. Locate the owning directory and sort its files:

   ```sh
   find ../i-migration-service/migrations/10_evm_wallets \
     -maxdepth 1 -type f | sort
   ```

2. Pick the next existing-style numeric prefix. It must sort after current
   files and exceed the version recorded in target databases when that state is
   available.
3. Inspect the affected schema, every writer, i-models, views, and deployment
   path before writing SQL.
4. Write the expected transition plainly. Use `ADD COLUMN`, `CREATE TABLE`,
   `CREATE INDEX`, or `ALTER TYPE` without rerun guards. Use `ADD CONSTRAINT`
   only after the constraint gate above.
5. Do not use `IF EXISTS`, `IF NOT EXISTS`, or catalog-driven `DO` blocks to
   hide ordering errors or schema drift. For a proven legacy repair, read
   [references/advanced-migrations.md](references/advanced-migrations.md).
6. Backfill or remove incompatible rows before adding `NOT NULL`, changing a
   type, or introducing another stricter storage contract.
7. For a breaking replacement, expand first, migrate code and data second, and
   contract only in a later migration after old readers/writers are gone.
8. Update the repository that owns generated models. Keep dependent views
   compatible until their consumers move.

## Types and Enums

- Inspect existing schema and model semantics before choosing a numeric type;
  do not apply a global precision/scale default. Raw units, display amounts,
  and fiat snapshots have different contracts.
- Use an enum only for a small, controlled, stable, closed vocabulary. Keep
  open, provider-defined, or temporary values as text and validate them in the
  application. Do not use a `CHECK` as a lightweight enum.
- Name enum types with the smallest unambiguous singular `snake_case_t` name.
  Reuse a type only for the same semantic domain.
- When converting text to an enum, verify stored values, drop the old default,
  cast explicitly with `USING column::text::enum_type`, then restore the
  default. Remove superseded checks.

## Conditional References

- For `${MIGRATION_*}` placeholders, read
  [references/migration-variables.md](references/migration-variables.md)
  completely before writing SQL or deployment configuration.
- For in-file options, enum-value additions, historical repairs, dependent
  views, or cleanup migrations, read
  [references/advanced-migrations.md](references/advanced-migrations.md)
  completely before acting.

## Apply and Validate

Use only a known local or disposable test database. Do not blindly source an
environment file that may point to shared or live infrastructure.

```sh
# Initialize tracking tables only on a fresh database.
./app --init

# Apply unapplied migrations.
MIGRATION_DIR=./migrations/ ./app --apply-only=true

# Check stored hashes; optional paths narrow the check.
./app --check [migration paths...]
```

`./make.sh test` runs the repository hash check when `DB_*` variables and
`./app` are available; it has no `unit` or `docker` modes.

Before finishing:

- run `git diff --check` and confirm migration ordering;
- apply the migration to a disposable canonical database when practical;
- verify the resulting columns, types, constraints, and data postconditions;
- run focused tests in affected services/models;
- report any unrelated pre-existing hash drift separately.
