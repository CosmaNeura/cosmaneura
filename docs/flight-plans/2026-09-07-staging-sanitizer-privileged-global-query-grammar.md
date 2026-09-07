# Staging sanitizer privileged/global query grammar

This Tier 2 slice is stacked on exact privileged/global observer-envelope commit `aaf86e20dfd5dab25df88f8168d65ab28712e274`. It selects only the dormant PostgreSQL 16 query grammar and output frames for the future privileged/global observer. It performs no database connection, credential acquisition, provider call, mutation, workflow dispatch, staging action, or production action.

Thirteen exact, SHA-256-pinned `SELECT` templates cover the 11 privileged/global requirements assigned by the observer envelope. Eleven run once on a future selected maintenance database; two run once per future authenticated database, up to 64 entries. They read only an exact `pg_catalog` allowlist, use five exact built-in function signatures, accept no caller SQL or parameters, and may not read application data.

Microsoft Entra principal mappings come only from `pg_shseclabel` labels owned by provider `pgaadauth`; the vendor principal-listing function is not executed. Password posture is a boolean `pg_authid.rolpassword IS NULL` projection and never exposes password verifiers. A future separately reviewed bootstrap authority must be selected before either privileged catalog can be read.

The selected frames cover Entra mappings, password-null state, shared dependencies, closed-world database inventory and ACLs, effective database privileges and active sessions, tablespace and parameter privileges, publication/subscription/extension/event-trigger authority, and PostgreSQL 16 system-object ACL deltas. Raw catalog values remain sealed and process-local and cannot be logged, persisted, or published.

The exact templates were exercised locally against disposable PostgreSQL 16.15. All 13 parsed and executed; the system-object baseline returned no delta on a fresh cluster, then detected one isolated test grant. This was local validation only.

Public activation remains terminal at `activation.sanitizer_privileged_global_database_observer_unimplemented`. Bootstrap identity, maintenance database, disabled-database strategy, connection adapter, observer, offline authenticator, stability authorities, post-provision descriptor, and final adapter join remain unselected or unimplemented.

Template-set SHA-256: `bd6a985e8101363332de22a753ffde90837c9d8fdd8532a03e1a87066992d3fa`.

Query-grammar contract SHA-256: `d6d751a71f87936768f153986f087a89ca5edf4cf1a3dc9f5193a225dbe23bb8`.

Recovery is a code-only revert. No cloud, staging, or production state is created.
