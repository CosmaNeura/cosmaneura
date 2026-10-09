# Avoid ORM compilation inside recording timeout inspection

Tier 2. Current Core 733 virtual CPU profiles identify repeated
`Select.get_final_froms()` ORM compile-state construction in the per-statement
timeout guard. Use the public, column-derived `columns_clause_froms` subset to
recognize relational reads cheaply. This is deliberately conservative: a
generated read with explicit FROM but no relational selected columns takes the
original setter. Keep the existing tree rejection of text, literal SQL,
functions, writes and mutating CTEs.

No authorization decision, identity, consent, credential, lock, timeout limit,
cancellation fence or transaction evidence is changed or cached. Preserve fresh
budget calculations, original setter fallback, exact scope/connection/thread/
transaction/savepoint correspondence and invalidation on every uncertainty.

Validate with actual task-owned fictional PostgreSQL: relational reads must
execute without calling the ORM FROM compiler, conservative columnless reads
must retain every setter, and the full current desktop/phone preparation
profiles must retain identical actual SQL and lock shapes. Run the existing
deadline, cancellation, slow-SQL/lock, invalidation, nested-budget, native,
admission, recording-window and metadata contracts plus scoped Ruff/types.
Record the exact tested commit and implementer review before merge under the
owner's local-validation policy. Instrumented lab times do not establish device
consent-to-Ready seconds.

No schema, configuration, dependency, release cohort or production changes.
Any staging delivery uses immutable merged source and the existing image,
configuration and database qualification methodology. Retain Core 733 staging
digest `sha256:4ce7d0e3475846339dbca33cab24a4cd61aff04751d615ae74bacff8d7ef291a`
and receiver 39 for recovery; reverting this change also restores classification.
Virtual audio only. Desktop 65 installation awaits normal app closure and Mac
unlock; iPhone 62 upload awaits the already pending explicit user approval.
All twelve repairs and consistent 5–7 second acceptance remain open.
