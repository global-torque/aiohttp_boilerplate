# ADR 007: Transaction and hook semantics

Status: Proposed

Validation and existing `before_*` hooks precede the transaction. `perform_*`
and `after_*_in_transaction` share one connection before commit. Existing
`after_*` remains post-commit, so its failure never implies rollback.
