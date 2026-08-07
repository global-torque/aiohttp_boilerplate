# ADR 008: Compatibility and release policy

Status: Proposed

Safety fixes ship before broad cleanup. Version 0.9 retains deprecated
`start_web_app`, `app.conf`, `app.db_pool`, `request.context`, and `request.log`.
Removal requires a documented later release and downstream migration window.
