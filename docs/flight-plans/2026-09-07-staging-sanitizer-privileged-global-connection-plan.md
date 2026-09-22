# Staging sanitizer privileged/global connection plan

This Tier 2 slice is stacked on exact observation-frames commit `4480a4b971c514274df9af881a01298d8a8454b1`. It selects only the dormant database sweep topology for the future privileged/global observer. It opens no connection, requests no credential, executes no SQL, changes no provider or database state, and grants no staging or production authority.

The exact inventory is closed to `azure_maintenance`, `azure_sys`, `postgres`, `staging`, `template0`, and `template1`. The server-once phase uses `postgres`; the same connection is reused for its per-database frames, and every other connectable database is visited once in increasing PostgreSQL OID order. Managed-database connection or catalog failures remain blockers. At most one connection is active, with no pooling or retries and a maximum of 64 connections and token requests.

Non-connectable entries are never silently omitted. Only `template0`, `azure_maintenance`, or `azure_sys` may be disabled, and `template0` must be present, disabled, and a template. Their inaccessibility must be authenticated from the exact server-once inventory, dependency, ACL, effective-privilege, and session frames while a future external server-wide stability authority is held. This strategy deliberately makes no claim that disabled databases contain no application data or have no object-level ACL deltas.

The resulting plan is immutable, non-copyable, non-serializable public metadata that retains the identical sealed target-role observation plan in process. It selects neither the separate bootstrap identity nor any live adapter. Public activation remains terminal at `activation.sanitizer_privileged_global_database_observer_unimplemented`.

Connection-plan contract SHA-256: `d7bd6ef34b63a4dd281101ea23b8855b101bb4154e172199c3bf90f0e592dde4`.

Recovery is a code-only revert. No cloud, staging, production, or database state is created.
