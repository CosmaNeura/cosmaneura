# Staging sanitizer target-role database observation plan

This Tier 2 slice is based on protected `master` `a2f30f179d2a54ebdaea83a7b71c4fbb0ace56f8`. It adds a dormant, process-local plan for a future least-privilege target-role database observer. It performs no database, network, provider, role, grant, schema, data, workflow, deployment, staging, or production action.

The planner accepts only an exact sealed `SanitizerTargetBinding` and the exact `ControllerReceiptV2DatabaseObservationAuthority` retained inside that binding's receipt ancestry. Object identity is mandatory: equivalent bytes, digests, copies, subclasses, lookalikes, maps, and mixed chains are rejected. A reviewed whole-second UTC clock reauthenticates the complete retained chain at both the start and end of planning, rejects regression and expiry, and never extends the input lifetime.

The sealed output contains only bounded digests, timestamps, claims, and private references to the two authenticated ancestors. It retains no token, credential, DSN, connector, callback, connection, cursor, SQL, catalog row, or expected post-provision descriptor. It is nonserializable, copy-resistant, tamper-evident, and grants no connection, mutation, runtime, sanitizer, provider, workflow, or evidence-publication authority.

The contract partitions every database-capability authentication requirement among the future target-role observer, a separate privileged/global observer, external stability authorities, an independently reviewed post-provision descriptor authority, and the final adapter/authenticator join. The historical controller database observation proves only its narrow TLS/private-route/role snapshot and is never promoted into full database-role-state authentication.

Public activation remains terminal at `activation.sanitizer_target_role_database_observer_unimplemented`. Later changes must separately implement the one-shot observer, sealed raw observation, offline authenticator, privileged/global evidence, descriptor selection, stability fences, and final join before any live capability can be claimed.

Contract SHA-256: `d341d653195b9b3d3d941304205f801d952e3195cd0eefafd0e9d6a9131a0dbb`.

Recovery is a code-only revert. No external state is created.
