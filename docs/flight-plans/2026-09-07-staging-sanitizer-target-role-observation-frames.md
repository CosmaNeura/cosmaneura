# Staging sanitizer target-role observation frames

This Tier 2 slice is stacked on exact local query-grammar commit `8dfb775f0ffb1653b7edf7eb66828e3339054626`. It adds the dormant structural boundary between the future one-shot PostgreSQL observer and its offline authenticator. It opens no connection, acquires no credential, executes no SQL, calls no provider, and changes no staging or production state.

The decoder accepts only an exact tuple of the 13 named frame tuples in the query grammar's fixed order. Every row must be an exact tuple matching the selected column count, logical type, nullability, and integer range. Boolean values cannot pass as integers; text must be valid UTF-8 without NUL; unknown, missing, reordered, or duplicate frames and duplicate rows fail closed. Per-frame, aggregate-row, per-row-byte, and total-output-byte limits are enforced before an opaque result is minted.

The decoded frame set is immutable, non-copyable, non-pickleable, non-JSON-serializable, tamper-evident, and content-free in `repr` and public failures. Raw frames remain process-local. Its only positive claims are structural schema validation, exact input-order preservation, and resource-bound enforcement. It explicitly does not claim a database connection, transaction, live observation, role-state authentication, sanitizer authority, workflow integration, or evidence-publication authority.

The future reviewed one-shot observer must execute only the pinned query templates over its internally derived managed-identity connection and pass the exact materialized tuple directly to this decoder. The future offline authenticator must consume the identical sealed object and separately prove the 22 target-role requirements. Public construction remains terminal at `activation.sanitizer_target_role_database_observer_unimplemented`.

Observation-frames contract SHA-256: `sha256:7326d189125f1b01646a0de221eee59ddace31ff353573e8f7186bf77009162b`.

Upstream query-grammar contract SHA-256: `sha256:8f1c4831c4766942778e5be24149a779ba0cd84081d03c11ed21463aabc3d1fe`.

Query-template set SHA-256: `sha256:a740c41e36314c9954456d3c0dadb7d496904ec8d94fdbf14c106e2d125f9e33`.

Recovery is a code-only revert. No external state is created.
