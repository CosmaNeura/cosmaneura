# Staging sanitizer privileged/global observer envelope

This Tier 2 slice is stacked on exact offline-authenticator commit `39c1699438fe860ca6b516c55294a33597d295cd`. It defines only the dormant boundary for the 11 privileged/global requirements in the sealed observation plan. It selects no bootstrap identity, maintenance database, query, catalog or function allowlist, frame schema, connection count, token count, disabled-database strategy, runtime adapter, or live authority.

The future observer's only public input is the exact sealed target-role observation plan. Its separate bootstrap authority must be internally derived from authenticated runtime ancestry, cannot be supplied by a caller, must be distinct from the provision runtime identity, may perform no observer mutation, and must be torn down before target-role runtime execution. The observer cannot use passwords, ambient credentials, provider calls, retries, pooling, application-data reads, commits, DDL, DML, role changes, privilege changes, or raw password-verifier selection.

The future connection sweep must authenticate the target server and every database entry, use at most one active verify-full connection, run only read-only repeatable-read transactions, and always roll back and close. It must cover all connectable databases plus a separately reviewed strategy for disabled database surfaces. A final adapter—not this observer—must already hold the external server-wide stability authority across the complete sweep and later sanitizer transaction.

The future exact grammar must prove the provision-job UAMI mapping, password-null predicate, ownership and shared dependencies, closed-world database inventory and ACL sources, effective database privileges and sessions, tablespace and parameter privileges, publication/subscription/extension/event-trigger authority, and PostgreSQL 16 system-object ACL deltas. Raw role, database, object, ACL, or identity material remains process-local in a sealed output and may not be logged, persisted, or published.

Public activation remains terminal at `activation.sanitizer_privileged_global_query_grammar_unselected`. Recovery is deletion or revert of this additive contract slice; it creates no database, cloud, staging, or production state.

Contract SHA-256: `040874bb93f30c17168a49284b9255be982f2530e1cf144325ad89ae174a63d0`.
