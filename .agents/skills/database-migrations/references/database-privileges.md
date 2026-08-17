# Database Privilege Changes

Treat `GRANT`, `REVOKE`, `ALTER DEFAULT PRIVILEGES`, role creation or changes,
role membership, and `ALTER ... OWNER TO` as security-sensitive.

Do not infer approval from requests such as "implement this feature", "make
deployment work", or "fix the tests". Do not automatically grant access to
shared or environment-specific runtime roles.

Never invent a principal, role naming convention, environment placeholder,
privilege scope, or migration directory. Resolve them from the current schema,
deployment configuration, and repository layout. If they cannot be resolved,
ask the user for the actual values without proposing a fabricated default.

Before requesting approval:

1. Inspect owners, ACLs, default privileges, runtime roles, and affected
   application queries using read-only checks.
2. Explain why the existing privilege model does not cover the operation.
3. Present the exact principal, object, privilege, environment, reason, and
   rollback using only discovered or user-supplied values.
4. Ask whether database privileges should change and which stable capability
   role should receive access.
5. Continue only after approval of that exact matrix and rollback.
