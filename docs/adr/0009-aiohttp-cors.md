# ADR 009: Adopt aiohttp-cors

Status: Proposed

The framework delegates CORS headers and preflight behavior to aiohttp-cors.
It registers a snapshot of every managed route against canonical exact origins
and rejects incompatible OPTIONS, wildcard, and class-view route forms. No
homemade fallback or origin matching remains.
