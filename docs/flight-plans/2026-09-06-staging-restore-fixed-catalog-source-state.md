# Staging restore fixed-catalog source state

Tier 2: this dark leaf, rebased onto exact protected-master merge `1d2698c1ff694e3622e7031d011072654d3bd95c`, authenticates five fixed-catalog source-state HMACs. It does not connect, mutate, use credentials or keys, verify output, or run.

## Trust boundary

- `FixedCatalogSourceStateBinder.bind` accepts only an exact sealed `SanitizerTargetBinding` and exact immutable `inventory_receipt_bytes`. Caller-selected digests, policies, clocks, paths, keys, mappings, subclasses, and lookalikes cannot select authority.
- The reviewed whole-second UTC clock reauthenticates the target, writer, builder, canonical record chain, and retained observations; stale or expired target bindings refuse.
- The expected digest comes only from retained intent. Duplicate keys, noncanonical/oversized input, unknown fields, bad pins, and failed evidence refuse; canonical unsigned SHA-256 is compared to receipt and intent.
- Historical inventory time must be canonical, ordered, policy-bounded, and tied to the restore point, but its old expiry is not a new runtime deadline. Current freshness comes from the target binding.
- Source/candidate SHAs, source PostgreSQL server and database, restore point, workflow/run identity, repository/ref/path, and signer are cross-bound to the authenticated controller intent and receipt.

The private ordered projection contains exactly `baa_version`, `doctor_note_schema`, `drug_reference`, `lab_range_set`, and `note_type_customization`. Each has a nonnegative count and lowercase 64-hex HMAC. No fixed-catalog rows, row identifiers, keys, or `server_scope_hmac_key_bytes` are retained. Inputs and projection stay private; public output is only digests, versions, and times.

The policy marks this binder implemented and narrows its blocker to post-projection verification. Trusted-evidence, database/provider/mutation, sanitizer/runtime, workflow, and executable flags stay false; the public factory is hard-disabled.
The next slice must privately consume this seal and prove preserved payloads under the distinct transform-key domain. Run focused tests, static and raw-hash checks, then independently review the final commit. Recovery is a revert: this leaf creates no resource or data change. Activation still requires server-scope, executor, post-projection, transaction/rollback, workflow, and founder gates.
