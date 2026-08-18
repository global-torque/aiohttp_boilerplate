# ADR 005: Request context and logging isolation

Status: Proposed

Each request receives a frozen context under a typed key and an immutable
logger adapter. Identity discovery replaces both atomically. Shared loggers
never hold request, response, identity, or component state.
