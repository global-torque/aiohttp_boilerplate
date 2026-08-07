# ADR 003: Bound values and explicit write scope

Status: Proposed

All values, including JSONB payloads and paths, use asyncpg arguments.
Structural identifiers follow a documented identifier grammar. Update/delete
require a scope; full-table operations use separately named methods.
