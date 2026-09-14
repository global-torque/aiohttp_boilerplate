# Advanced Migrations

## In-File Options

Use first-line options sparingly:

```sql
--- allow_error: false, required_env: !master
```

Use `required_env` only for environment-specific seed or temporary data, not
normal schema. Avoid `allow_error: true` for structural migrations.

## Add an Enum Value

Keep an enum-value addition isolated because PostgreSQL versions can reject it
inside a transaction:

```sql
-- IMPORTANT: run outside a transaction block when required by PostgreSQL.
ALTER TYPE tokenization_engine_t ADD VALUE 'ERC-7943';
```

## Historical Repairs

Use plain fail-loud SQL for canonical migrations. Use catalog inspection or a
conditional `DO` block only after proving a specific deployed legacy state.
Document the accepted precondition, keep the repair small, and fail on any
unexpected shape.

Never edit a historical migration to repair an applied database. Add a new
forward migration with a version greater than both checked-in and deployed
versions.

## Dependent Views

When a table rewrite would break a view contract:

1. Add an earlier migration that removes or makes the dependency compatible.
2. Apply the table change in the owning directory.
3. Recreate the compatible view in a later-sorting migration.

## Cleanup and Cross-Service Changes

Use expand-and-contract. In cleanup SQL comments, name the deployed code and
backfill steps that made the removal safe. Do not remove the old shape while a
live reader or writer still uses it.
