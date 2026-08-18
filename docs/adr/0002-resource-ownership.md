# ADR 002: Resource ownership and cleanup contexts

Status: Proposed

`create_app()` gives each database pool and auth session an independent
`cleanup_ctx`. Factory-created resources are owned; injected resources are not
owned unless explicitly requested. Every owned successful creation closes once.
