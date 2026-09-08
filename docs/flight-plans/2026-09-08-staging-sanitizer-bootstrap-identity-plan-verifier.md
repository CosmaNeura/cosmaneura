# Staging sanitizer bootstrap identity plan verifier

This Tier 2 slice validates an already-created saved OpenTofu plan for the isolated sanitizer bootstrap identity root. It cannot create or apply a plan. Explicit digests, source commit, state identity, owner-only plan bytes, immutable Git objects, descriptor-safe checkout reads, OpenTofu 1.12.5, the complete AzureRM 4.73.0 package H1, and both executable SHA-256 values are bound exactly.

## Isolation and policy

Only one exact UAMI and one built-in activation guard may be created; updates, deletion, replacement, drift, deferred work, federation, RBAC, jobs, network, storage, database, production, and PHI resources are refused. The cleanup deadline is derived from the plan timestamp and bounded lifetime, checked before and after validation, and every source, provider, state, configuration, output, tag, and lifecycle shape is closed. For the first plan against this empty remote backend, the archived `tfstate-prev` must be OpenTofu's exact empty sentinel (empty lineage, serial zero). Archived `tfstate` must carry the expected remote lineage and serial, and both it and decoded `prior_state` must carry the exact state-boundary output and sole resource-group readback. The verifier never runs `tofu init`; only the pinned executables may run inside default-deny, network/IPC-blocked sandboxes, so the Azure backend is never contacted, initialized, locked, read, or changed.

The receipt binds exact source, provider, plan, decoded JSON, and embedded-state digests while always reporting all live-readback fields and `apply_authorized` as false. Failure emits no partial receipt. Recovery is deletion of this code-only validator; backend capture, live readback, review, and apply remain separate founder-controlled steps.
