# ADR 006: Cache identity and TTL-only behavior

Status: Proposed

Caching is opt-in through an identity/tenant callback. Keys include namespace,
method, path, canonical repeated query values, declared vary headers, and
identity. Mutations/random/skip bypass storage. Version 0.9 promises TTL only.
