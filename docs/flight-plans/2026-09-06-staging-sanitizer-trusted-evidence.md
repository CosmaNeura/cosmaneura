# Staging sanitizer trusted-evidence join

Tier 2: this dark leaf joins the authenticated fixed-catalog source state to
the final restore-evidence authority. It does not connect, mutate, call a
provider, use credentials, execute the sanitizer, or integrate a workflow.

## Outcome and trust boundary

- `SanitizerEvidenceBinder.bind` accepts exactly two public data inputs: an
  exact sealed `AuthenticatedFixedCatalogSourceState` and an exact sealed final
  `staging_restore_safe_submit.RestoreEvidenceBinding`.
- Caller bytes, mappings, paths, URLs, DSNs, hostnames, roles, policies,
  digests, clocks, keys, status assertions, subclasses, and lookalikes cannot
  select authority. The reviewed private clock must return non-regressing,
  whole-second UTC values.
- The binding record must be canonically identical to the complete
  `evidence_binding` retained by the controller intent. Its authorization
  digest must equal the controller receipt's retained digest.
- Source/candidate SHA, source server/database, approved restore point,
  repository/ref/workflow, workflow SHA, run/attempt, and signer identity must
  equal the fixed-catalog ordered projection. Every manifest, bundle, evidence
  set, candidate, inventory, quiescence, server-scope, provider, attestation,
  and server-scope-authentication digest is rechecked transitively.
- The evidence record's expiry is historical: it was the final safe-submit
  deadline and is not extended or reused as this binder's current deadline.
  The controller target receipt must remain fresh at the current clock read.

The output is an exact, module-sealed, nonserializable
`AuthenticatedSanitizerEvidence`. It retains both original authorities and the
canonical evidence record privately. Its public surface contains only binding
digests and timestamps. It asserts authenticated fixed-catalog, inventory,
restore evidence, and server scope, while connection, provider, runtime,
sanitizer, mutation, post-projection, executable, and workflow claims remain
false.

## Dependencies and sequencing

This slice is stacked on exact fixed-catalog commit
`fde9c4ae78409adb22e0bee8dd9fd2b5f368e669`. The dependency order is controller
receipt builder, private-WORM writer, target binder, fixed-catalog source-state
binder, then this join. The final evidence object must already have been minted
in process by the disabled composition chain after server-scope HMAC,
provider-observation, and GitHub-attestation verification. No JSON
deserialization minter is introduced.

The upstream composition and source policies remain disabled or unconfigured,
so normal construction is unreachable. The public binder factory refuses with
`activation.sanitizer_executor_unimplemented`. The SQL/data-contract executor,
database adapter, provider/runtime integration, post-projection verifier, and
workflow handoff are deliberately absent.

## Activation holds

`trusted_evidence_binding_implemented=true` records only that this dark join
exists. `server_scope_authenticated=true` on its output means the accepted
final evidence authority is bound to the server-scope proof; it is not a live
provider observation, a connection credential, or permission to sanitize.

The following remain hard holds:

- the data-contract executor and database adapter do not exist;
- fixed-catalog post-projection preservation is not authenticated;
- semantic identifiers and derived values have no execution proof;
- transaction, rollback, competing-session, and post-commit behavior is not
  implemented or rehearsed;
- guarded-table truncate permission and rollback are not proven;
- upstream evidence-composition, provider, attestation, and source policies
  remain disabled or unconfigured;
- no protected workflow can construct or consume this in-process authority;
- no founder-approved live rehearsal, staging cutover, or production promotion
  is authorized by this slice.

This PR can be held indefinitely after its fixed-catalog prerequisite without
changing deployed behavior. The next receiver must consume this exact sealed
object directly and preserve every false authority claim until its own bounded
review proves the corresponding capability.

## Validation and recovery

- Validate canonical policy hash and exact closed-world fields.
- Exercise one genuine retained controller chain, including a successful join
  after historical evidence expiry but before current target expiry.
- Refuse every digest/context substitution, partial/mapping/subclass/forged
  authority, mixed chain, output tamper, bad/regressing clock, and expired
  target; prove constructor and serialization refusal.
- Run focused sanitizer, fixed-catalog, evidence-composition, safe-submit, and
  server-scope-authentication regressions plus Ruff, Pyright, raw-hash, and
  staged-diff checks.

Deployment, feature flags, schema/data migration, live rehearsal, and rollout
are N/A because this is unreachable dark code and grants no runtime authority.
Recovery is a revert of this leaf; it creates no external resource or data
change. Activation still requires a separately reviewed executor,
post-projection proof, transaction/rollback proof, workflow integration, and
founder-authorized rehearsal gates.
