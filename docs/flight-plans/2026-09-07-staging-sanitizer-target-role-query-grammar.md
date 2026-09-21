# Staging sanitizer target-role query grammar

This Tier 2 slice is stacked on exact PR #336 head `800fc66f8167287afb4b7bffb6120c6ed0a445d6`. It selects a dormant, closed-world query grammar for the future least-privilege target-role database observer. It performs no database connection, credential acquisition, provider call, mutation, workflow dispatch, staging action, or production action.

Thirteen exact, SHA-256-pinned `SELECT` templates cover all 22 requirements assigned to the target-role observer: session and role posture, inbound and outbound memberships, relevant `pg_db_role_setting` source rows, schema/table/column/sequence/routine/type/language/foreign-data privileges, and user mappings. The templates have no caller parameters or identifier interpolation, execute once in a fixed order, reference only 13 named `pg_catalog` relations or views and 15 exact PostgreSQL 16 built-in function signatures, and cannot read application tables.

The selected output contract fixes every frame's column names, logical types, and nullability. Execution is bounded to 13 queries, 4,096 total rows, 16 KiB per row, 8 MiB total output, five seconds per query, and 30 seconds overall, without retry or server-side prepare. Any future adapter must use the observer envelope's single managed-identity connection and one read-only `REPEATABLE READ` transaction, verify each cursor description before consuming rows, stream-enforce all bounds, and always roll back and close.

The exact templates were exercised locally on PostgreSQL 16.15 as a disposable non-superuser inside one read-only `REPEATABLE READ` transaction. All 13 completed successfully. This was local validation only; the contract module itself imports no database, network, provider, credential, or process API and executes no SQL.

The contract remains non-executable at `activation.sanitizer_target_role_observer_unimplemented`. It grants no database connection, mutation, runtime, sanitizer, workflow, authentication, or evidence-publication authority. Separate reviewed slices must implement the one-shot observer and sealed output, implement the offline authenticator, provide privileged/global and stability authorities, select the post-provision descriptor, and join all authorities before any database-role-state claim.

Query-template set SHA-256: `a740c41e36314c9954456d3c0dadb7d496904ec8d94fdbf14c106e2d125f9e33`.

Query-grammar contract SHA-256: `8f1c4831c4766942778e5be24149a779ba0cd84081d03c11ed21463aabc3d1fe`.

Recovery is a code-only revert. No external state is created.
