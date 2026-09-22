# Staging sanitizer target-role observer envelope

This Tier 2 slice is based on protected `master` `8ce3d22c11acf33a0fc487b4fbeb3622a8f7d62e`. It adds only a dormant, closed-world contract and validator for a future least-privilege target-role database observer. It performs no database, network, provider, identity, role, grant, schema, data, workflow, deployment, staging, or production action.

The future public input is restricted to the exact sealed target-role observation plan. Caller-provided bytes, mappings, paths, URLs, DSNs, hosts, roles, identity identifiers, credentials, connectors, callbacks, SQL, clocks, policies, and runtime overrides are forbidden. The 22 target-role requirements must match the sealed observation plan's partition exactly and cannot absorb privileged/global, post-provision descriptor, or final-authenticator responsibilities.

Any later implementation must derive its target and managed-identity binding only from authenticated retained ancestry, use at most one connection and one token request, verify the exact socket peer and TLS hostname, prohibit ambient credential and libpq configuration, and execute inside one read-only `REPEATABLE READ` transaction that always rolls back and closes. It may not commit, mutate roles or privileges, run DDL/DML, read application data, retry, pool, retain credentials, or claim an advisory lock or external fence.

This slice deliberately selects no connection adapter, query text, query-template digest, catalog allowlist, function allowlist, query order, frame schema, or resource bound. The validator only fixes the future positive-allowlist shape and the closed set of observation frames. It imports no database, network, provider, credential, or process API and grants no connection, mutation, runtime, sanitizer, workflow, or evidence-publication authority.

The observation-plan and observer-contract tests move into a dedicated observer CI shard. This keeps the pre-existing sanitizer-executor shard below its enforced 20-minute worker limit while preserving explicit, disjoint, exhaustive test routing in pull-request, post-merge, and candidate workflows.

Public activation remains terminal at `activation.sanitizer_target_role_query_grammar_unselected`. Separate reviewed slices must select the exact query grammar and bounds, implement the one-shot observer and sealed output, implement the offline authenticator, supply privileged/global and stability authorities, select the post-provision descriptor, and join all authorities before any database-role-state claim can be made.

Contract SHA-256: `acf34a80c63b0b103147ccd574195c11ebde16357a24bccf935030bca0f80fad`.

Recovery is a code-only revert. No external state is created.
