# Migration Variables

Use `${MIGRATION_[A-Z0-9_]+}` only when migration SQL needs a value supplied at
execution time:

```sql
INSERT INTO service_credentials (service_name, api_key, timeout_seconds)
VALUES ('payments', ${MIGRATION_PAYMENTS_API_KEY}, ${MIGRATION_TIMEOUT_SECONDS}::integer);
```

Follow these rules:

- Write the placeholder unquoted as a standalone SQL value. The runner supplies
  the PostgreSQL string literal.
- Use placeholders only for values, never identifiers, keywords, operators, or
  SQL fragments.
- Supply every referenced variable to the migration job. Unset is fatal;
  explicitly empty is valid.
- Add an explicit PostgreSQL cast when useful.
- Keep placeholders out of comments, quoted strings/identifiers,
  dollar-quoted blocks, and functions or `DO` bodies.
- Use an `E'...'` escape string when variable-bearing SQL also contains a
  backslash.
- A migration skipped by `required_env` does not resolve its variables.
  `allow_error: true` does not suppress resolution failures.

Hashes, migration logs, and `--final-sql` retain the placeholder, not its
resolved value. Database query, statement, audit, or activity logging can still
expose resolved values. Review database logging and `pg_stat_activity` access;
do not use textual variables when database-side query-text secrecy is required.
