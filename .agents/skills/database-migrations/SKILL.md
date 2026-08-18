---
name: database-migrations
description: Work with WebDevelop/Torque database migrations stored in the sibling i-migration-service repository. Use when adding or reviewing SQL schema/data migrations, choosing and naming PostgreSQL enum types, inserting MIGRATION_* environment values into SQL, choosing migration file names and ordering, updating evm-api local test bootstrap SQL, validating migration hashes/application, or coordinating DB changes with Go services, i-models, admin views, and deployed migration jobs.
---

# Database Migrations

## Core Rule

Use `../i-migration-service/migrations` as the canonical home for shared database schema and seed changes. Do not create repo-local migrations in the current repository.

## Database Privilege Approval Gate

Treat SQL statements that change database privileges, roles, memberships, or
ownership as security-sensitive. This includes:

- `GRANT` and `REVOKE`.
- `ALTER DEFAULT PRIVILEGES`.
- `CREATE ROLE` and `ALTER ROLE`.
- Role-membership grants.
- `ALTER ... OWNER TO`.

Unless the user explicitly requested the exact privilege change, do not write
or modify SQL containing these statements.

Before requesting approval:

1. Inspect existing owners, ACLs, default privileges, runtime roles, and the
   affected application queries using read-only checks.
2. Explain why the existing ownership or privilege model does not already
   cover the required operation.
3. Present the exact proposed privilege matrix: principal, object, privilege,
   environment, reason, and rollback.
4. Ask whether the change should modify database privileges at all, which
   stable capability role should receive access, and whether the user approves
   the exact matrix and rollback.
5. Continue only after explicit approval.

Never infer privilege-change approval from a general request such as
"implement this feature", "make deployment work", or "fix the tests". Do not
automatically grant privileges to shared roles such as `invest_user` or
`prod_invest_user`, use environment-specific login roles, or add optional-role
catalog loops without explicit approval.

## Workflow

1. Locate the owning directory in `migrations/` folder

2. Pick the next filename by sorting the directory:

```sh
find ../i-migration-service/migrations/10_evm_wallets -maxdepth 1 -type f | sort
```

Use a zero-padded or existing-style numeric prefix that sorts after the current files, then a concise snake-case title, for example `13_add_session_policy_fields.sql`.

3. Write canonical migration SQL to fail on unexpected schema state:
   - Use plain `ADD COLUMN`, `CREATE TABLE`, `CREATE INDEX`, `ALTER TYPE`, and `ADD CONSTRAINT` in normal migration files.
   - Do not add `IF NOT EXISTS`, `IF EXISTS`, or catalog guards just to make a migration rerunnable; an already-existing table, column, index, enum value, or constraint usually means migration ordering or DB drift needs attention.
   - Do not introspect `pg_index`, `pg_attribute`, `pg_constraint`, or similar catalogs to decide between multiple DDL paths in a normal migration.
   - If a historical repair is truly required, make it a small explicit repair with comments and exact preconditions; fail when the DB is not in the expected legacy shape.
   - Keep idempotent guards for local Docker bootstrap over old Postgres snapshots, not for canonical migrations.
   - Backfill old rows explicitly before adding stricter code expectations.
   - Prefer PostgreSQL enums for small, controlled, stable vocabularies such as workflow statuses and supported chains; follow the enum guidance below.
   - Prefer exact numeric types for money/token amounts, usually `numeric(78,18)` for token-scale values and `numeric(38,18)` for USD snapshots.

4. Prefer expand-and-contract for breaking changes:
   - Do not drop, rename, or change the type/meaning of an in-use column in the same migration/release that introduces the replacement.
   - First add the new nullable/defaulted column, enum, table, or view shape beside the old one.
   - Update application code to tolerate both shapes; for replacements, dual-write old and new fields while reads still use the old source.
   - Backfill old data into the new structure, then switch reads to the new structure and stop writing the old field.
   - Drop old columns/tables/views only in a later cleanup migration after production has run without readers/writers for the old shape.
   - For generated model changes, update the generated/model repo that owns those structs rather than hiding schema assumptions in application code.
   - For views depending on a changing table, keep old view contracts until all consumers move, or add a compatible new view/columns first; remove old view fields only in the later cleanup step.

5. Check whether local Docker tests need a bootstrap patch:
   - `docker-compose.yml` mounts `../i-migration-service/migrations` and applies selected newer files on top of a seeded Postgres snapshot.
   - If tests start from a snapshot older than the new migration and the app/tests need the schema immediately, add an idempotent step to the `migrate` service there.
   - Keep that bootstrap as a local-test bridge only; the canonical migration still belongs in `i-migration-service`.

## Enum Types

Use a PostgreSQL enum when a column represents a closed application contract
with a small, controlled, and reasonably stable set of values. Good candidates
include workflow statuses, supported chains, operation kinds, and transaction
legs. Do not use enums for free-form errors, provider identifiers, hashes,
event names, source labels, or other externally extensible values.

Search existing migrations before creating a type. Reuse an enum only when it
represents the same semantic domain, not merely because some labels overlap:

```sql
-- Reuse the canonical chain domain instead of declaring chain as text.
chain evm_wallet_chain_t NOT NULL DEFAULT 'ethereum'
```

Create a dedicated enum for a distinct state machine:

```sql
CREATE TYPE evm_contract_deployment_status_t AS ENUM (
    'prepared',
    'token_submitted',
    'token_mined',
    'treasury_submitted',
    'succeeded',
    'failed',
    'ambiguous'
);

CREATE TABLE evm_contract_deployment_operations (
    status evm_contract_deployment_status_t NOT NULL DEFAULT 'prepared'
);
```

Name enum types in singular `snake_case` with a `_t` suffix. Prefer the
smallest name that remains unambiguous across the shared database:

- Shared domain: `evm_wallet_chain_t`, `tokenization_engine_t`.
- Entity-specific attribute: `evm_wallet_operation_status_t`,
  `payment_provider_operation_status_t`.
- Keep the column name concise (`chain`, `status`); `_t` belongs only on the
  type name.

Use canonical labels: lowercase `snake_case` for internal states, existing
lowercase or kebab-case labels for chains, and established protocol spelling
when it is part of the domain contract, such as `ERC-20`. Prefer a `CHECK`
constraint instead when the vocabulary is deliberately open, changes often,
or contains only a temporary single allowed value.

When converting existing text to an enum, verify and normalize every stored
value first, then use an explicit cast such as
`USING status::text::evm_contract_deployment_status_t`. Drop and restore
dependent defaults or checks as required, and coordinate the type change with
application models and views.

## Runner Commands

Use the current `i-migration-service` binary/container contract. Set DB env vars from a known local/test environment before running; do not blindly source `.local.env` if it points at shared or live DBs.

```sh
cd ../i-migration-service

# Initialize migration tracking tables.
./app --init

# Apply all unapplied migrations from MIGRATION_DIR.
MIGRATION_DIR=./migrations/ ./app --apply-only=true
```

## Dynamic Values In Migration SQL

Use migration variables when a migration needs a secret or another value that
must be supplied at execution time. The migration-service process expands
environment variables matching `MIGRATION_[A-Z0-9_]+` when it applies an
eligible migration:

```sql
INSERT INTO service_credentials (service_name, api_key, timeout_seconds)
VALUES ('payments', ${MIGRATION_PAYMENTS_API_KEY}, ${MIGRATION_TIMEOUT_SECONDS}::integer);
```

Follow these rules:

- Write `${MIGRATION_NAME}` unquoted as a standalone SQL value. Never write
  `'${MIGRATION_NAME}'`; the runner supplies the PostgreSQL string literal.
- Use variables only for values. Do not use them as table/column names,
  identifiers, keywords, operators, or raw SQL fragments.
- Supply each referenced variable to the migration-service process through its
  shell, container, or deployment secret configuration. An unset variable is a
  fatal error before SQL execution; an explicitly set empty value is valid.
- Add an explicit PostgreSQL cast where useful, such as `::integer`, `::uuid`,
  or `::jsonb`. PostgreSQL performs the conversion from the rendered string
  literal.
- Keep placeholders out of comments, quoted strings, quoted identifiers,
  dollar-quoted blocks, and function/`DO` bodies. Put the dynamic value in a
  top-level DML statement. The runner also rejects a placeholder that touches
  an identifier character.
- If a variable-bearing migration contains a backslash in an ordinary SQL
  string, change that string to an explicit `E'...'` escape string so parsing
  does not depend on `standard_conforming_strings`.
- A migration skipped by `required_env` does not resolve its variables.
  Variable-resolution failures remain fatal even with `allow_error: true`.

The migration hash, migration-service logs, `migration_service_logs`, and
`--final-sql` retain the placeholder rather than the resolved value. This keeps
hashes stable across environments and avoids persisting the value in runner
logs. `--final-sql` output containing placeholders is an audit/debug view, not
SQL that can be executed directly.

The resolved SQL must still reach PostgreSQL. Database statement, error,
duration, audit, or activity logging can expose a value outside the migration
runner. Before using this feature for secrets, restrict database roles and
review server-side logging and `pg_stat_activity` access. Do not use textual
migration variables when database-side query-text secrecy is mandatory.

## In-File Options

The migration runner supports first-line configuration comments:

```sql
--- allow_error: false, required_env: !master
```

Use these sparingly. `required_env` is for branch/environment-specific seed or temporary data, not normal schema. Production schema should usually be unconditional. `allow_error: true` is almost never appropriate for structural migrations; prefer deterministic SQL that fails loudly on unexpected state.

### Add An Enum Value

Postgres can reject `ALTER TYPE ... ADD VALUE` inside a transaction block. Keep enum-value additions isolated when possible and document that requirement in the file:

```sql
-- IMPORTANT: ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
ALTER TYPE tokenization_engine_t ADD VALUE 'ERC-7943';
```

### Avoid Defensive Catalog Probing

Do use the style with a long `DO $$` block queries `pg_index`/`pg_attribute`/`pg_constraint`. That pattern is bad for canonical migrations because it is hard to read, hides DB drift, accepts multiple incompatible histories, and adds code that should not exist. A migration should state the expected transition:

```sql
ALTER TABLE evm_wallet_sessions
    ADD CONSTRAINT evm_wallet_sessions_pkey PRIMARY KEY (id);
```

If that fails, the failure is useful information. Fix the underlying ordering/state problem or write a deliberately scoped repair migration after understanding the actual legacy state.

### Change A View Around A Table Rewrite

When a table rewrite would break an admin view, split the work:

1. Add a pre-migration dependency file.
2. Apply the table migration in the owning directory.
3. Recreate the view later.
This ordering works because the runner sorts by directory and file names.

### Cleanup Or Cross-Service Migration

For the final cleanup phase of drops, renames, enum slimming, or data shape changes, write the migration as a coordinated release note in SQL comments. Call out the prior migration/code steps that made the cleanup safe:

```sql
-- CLEANUP: drops evm_wallet_operations.wallet_id after:
-- 1. wallet_address was added and backfilled,
-- 2. evm-api/i-models/admin consumers read wallet_address,
-- 3. no deployed writer still writes wallet_id.
```

Do not bundle the first replacement migration and the final drop in the same feature migration. Do not leave live code writing dropped columns or old enum values.

## Validation

Prefer the repo wrapper for service tests:

```sh
./make.sh test unit
./make.sh test docker
```

Use `./make.sh test docker` when the change affects local container bootstrap or integration-test schema. From `../i-migration-service`, use `./app --check` against a safe local/test DB to catch hash drift. If you cannot run a full apply, at least validate ordering, fail-loud schema assumptions, affected application code, and any local bootstrap changes.
