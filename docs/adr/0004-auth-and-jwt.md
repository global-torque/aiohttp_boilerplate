# ADR 004: Authentication upstream and JWT behavior

Status: Proposed

The application owns one finite-timeout auth session. Rejection maps to 403,
malformed success to 502, and transport/timeout failure to 503. Unverified JWT
parsing has an explicitly unsafe name and is never authentication.
