# Staging sanitizer bootstrap binding plan

This Tier 2 slice is stacked on exact privileged-global connection-plan commit `1174af79c27bf9a5fadbef30efd03e611ffb2cb9`. It authenticates and binds the already selected database role and provision-job managed identity into redacted metadata, and selects the future bootstrap mechanism. It creates no identity, grants no provider or database authority, acquires no credential, opens no connection, executes no SQL, and changes no staging or production state.

The selected future mechanism is a distinct, unfederated, ephemeral user-assigned managed identity named `cosmaneura-staging-restore-bootstrap-job`, attached only to a reviewed single-use bootstrap job. The bootstrap identity must be different from the provision, cleanup, dispatcher, GitHub Actions, and Scribe identities. Passwords, connection strings, default credential chains, public networking, persistent compute attachment, retries, pooling, and ambiguous outcomes remain forbidden.

The future lifecycle is fail-closed and ordered: create/assign the bootstrap identity, read back its exact identifiers, attach it only to the single-use bootstrap job, temporarily establish PostgreSQL Entra administrator authority, connect with the exact identity, provision the already-bound runtime role, authenticate the result, close the database session, remove the temporary administrator, and read back its absence. External stability authority must span the first provider mutation through the final absence readback, and runtime-role execution cannot begin until bootstrap authority is proven absent.

The planner consumes only the exact sealed privileged-global connection plan. It reauthenticates the retained target-role plan and full controller/database ancestry, then derives a domain-separated SHA-256 binding over the exact role and provision identity identifiers. Raw role and identity values remain private; public output exposes only digests and non-authoritative status. The output is sealed, immutable, non-copyable, non-pickleable, non-JSON-serializable, and tamper-evident.

The only positive claims are connection-plan authentication, exact database-role selection, provision-job identity binding, and bootstrap-mechanism selection. Bootstrap identity binding, provider calls, database calls, mutation authority, workflow integration, activation readiness, and execution all remain false. Public construction remains terminal at `activation.sanitizer_bootstrap_identity_unavailable`.

Bootstrap-binding contract SHA-256: `sha256:88b3ea9e5ca800ddfc50f10a8d83b4372c9239953877d1b52d2865d561dbdcff`.

Recovery is a code-only revert. No external state is created.
