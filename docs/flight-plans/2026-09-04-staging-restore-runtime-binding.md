# Flight plan: Define dormant staging restore runtime binding v1

Status: **contract and validator only; deliberately non-executable**.

## Outcome and boundaries

- Define a closed-world policy/validator for acyclic image, identity, ingress, egress, target-network, and cleanup bindings in a future private restore runtime.
- In scope: `contracts/staging-rehearsal/v1/restore-runtime-binding-policy.json`, the
  `validate_runtime_binding_policy` and `validate_external_runtime_binding` APIs in
  `scripts/validate_staging_restore_runtime_binding.py`, focused tests in
  `tests/infrastructure/test_staging_restore_runtime_binding.py`, docs, and dormant consumer pins.
- Non-goals: workflow activation, compute provisioning, Azure apply, image publication, live evidence, restore, data mutation, migration, cutover, and production/PHI use.
- No deployed runtime artifact, user, service, environment, secret, or data changes in this PR.

## Risk and current state

- **Tier 2:** this contract bounds future restore authority across supply-chain, identity, network, evidence, and cleanup boundaries. A validator pass proves document shape only.
- Base HEAD/origin `master` at `2f51b416` has the dormant executor/read-only adapters;
  `provision` remains a credentialless tombstone, and no dispatch or Azure mutation occurred.
- `infra-staging-rehearsal/` remains default-off source. Its optional `10.76.0.32/27` delegation and empty `10.76.0.64/27` subnet are not a runner, endpoints, DNS, or a route.
- This PR replaces the executor's single image/MI placeholders with this policy pin and external record/image sources; it does not populate the record or bind a runtime.
- Dependency order: sealed evidence chain → workflow tombstone → dormant executor/adapters → this contract → later hardening/runtime/integration → separately authorized rehearsal.

## Binding and authority contract

The policy pins record schema, signer/repository identity, and verification rules, not resulting
image, record, or signature URI/digests. After immutable image publication, the canonical record is
created and signed. Each sealed job-configuration pointer carries its exact job kind/resource ID;
before record validation, both pointers' four record/signature URI/SHA fields are compared field-for-field. They name
content-addressed objects in private `restore-runtime-bindings-v1`. The record binds image, policy, environment, both jobs/UAMIs,
network, and pre-binding job-configuration template digests; each template excludes its pointer to
avoid a cycle. `policy_binding_sha256` hashes canonical policy excluding activation fields/blockers;
the trusted-executor pin hashes the raw file. Missing, unsealed, or mismatched verification results fail.
Opaque seals prevent accidental/deserialized substitution but are no security boundary against
hostile same-process code; only reviewed exact-job reader/signature-verifier paths may mint input seals. This does not replace
evidence attestation. The unbound publisher is separate from dispatchers/jobs, has create-only
record/bundle authority, and has no read, overwrite, delete, source, target, secret, or start access.

| Identity | Future authority | Forbidden |
|---|---|---|
| Federated provision caller | Invoke only the exact provision broker endpoint with the pinned GitHub OIDC tuple | Azure job read/start or runtime/data authority |
| Federated cleanup caller | Invoke only the exact cleanup broker endpoint with the pinned GitHub OIDC tuple | Azure job read/start or runtime/data authority |
| Unfederated per-action broker UAMI | Consume one verified JWT, read both jobs, start one selected job with exact API version, and create/sign its immutable handoff | Job writes, retries, overrides, evidence, secret, source, target, or marker access |
| Unfederated provision job UAMI | Attach only to provision; exact evidence/secret/source/target/marker rights | GitHub federation, general subscription rights, resource deletion |
| Unfederated cleanup job UAMI | Attach only to cleanup; read its binding/root, tombstone, then delete exact owned resources | GitHub federation, broad evidence/HMAC/data/sanitize access, restore submission |

Runtime pins must name a job UAMI, never a caller or broker. Current Terraform models provision
cleanup-marker read only and three create assignments. A separate change must add the
fourth provision cleanup-tombstone create assignment and safe-submit correction for partial or
unknown outcomes; cleanup remains the only resource deleter. Start requires atomic replay consumption,
exact `/start` URL/query/body, and authenticated observations. Without reading runtime blobs or
secrets, the broker binds the equal observed pointers and signs canonical receipt bytes. The job
derives the immutable receipt path directly from `CONTAINER_APP_JOB_EXECUTION_NAME`, authenticates
those bytes, then locally validates the referenced launch/runtime records and their cross-binding.
Current dispatcher subscription `control_reader` assignments must also be removed or re-homed
before the contract's exact-job-only, `other_role_assignment_ids: []` boundary can pass.

- **Ingress:** both job UAMIs read queryless digest-in-path record/signature objects from locked,
  create-only private `restore-runtime-bindings-v1` and the pinned root from Key Vault. Only
  provision reads Azure-version-pinned evidence/attestation objects from `restore-evidence-v1`
  in a versioning-enabled account distinct from the marker account, plus the exact-version HMAC
  key. Both jobs use the same exact root version. Public/workflow/caller ingress remains forbidden.
- **Egress/containment:** exactly MI, ARM, evidence Blob, binding Blob, marker Blob, Key Vault,
  PostgreSQL, and Azure VNet DNS (`168.63.129.16`, UDP/TCP 53, A/AAAA for exact bound names) are
  allowed; everything else is denied. Provision runs two contained offline `gh` checks, cleanup one.
- **Registry:** both jobs pull only the verified record's digest from private ACR with their
  exact UAMIs before process egress applies; public/admin/anonymous, tag, password, and ambient-
  credential paths are forbidden.
- **Target isolation:** one second per-rehearsal PostgreSQL private endpoint in `10.76.0.64/27`
  plus exact private DNS reaches only the restored target. The provision UAMI uses an explicit-MI
  Entra token and receipt-bound database role; passwords/default credentials are forbidden. Keep
  public access off, zero firewalls, no peering/public route/production DNS/vendor egress.
- **Cleanup:** persistent control VNet/subnets are not deleted. Before `cleanup_binding_implemented`, evidence must cover: validate record/
  signature → authenticate receipt/ownership → observe or create and read back the dominant tombstone
  → delete only receipt-bound resources → prove all absent. Unknown ownership refuses deletion.

## Exact gates and follow-on sequence

`activation_ready` and these assertions remain false: `runtime_record_published`,
`runtime_record_signature_verified`, `private_runtime_record_ingress_bound`,
`runtime_record_publication_authority_bound`,
`private_registry_binding_implemented`, `runtime_image_publication_authenticated`,
`broker_oidc_authentication_bound`,
`broker_exact_job_observation_bound`, `broker_job_control_bound`,
`job_start_override_prevention_proven`, `job_start_no_retry_semantics_proven`,
`immutable_launch_record_mailbox_bound`, `launch_receipt_binding_implemented`,
`job_identity_attachments_bound`,
`private_evidence_ingress_bound`,
`private_secret_ingress_bound`, `private_marker_store_binding_implemented`, `explicit_egress_bound`,
`offline_verifier_containment_proven`, `target_private_endpoint_binding_implemented`,
`authenticated_controller_receipt_bound`, `dominant_tombstone_retention_bound`,
`cleanup_binding_implemented`, `workflow_integrated`,
and `restore_activation_authorized`.

1. **Contract:** `uv run python scripts/validate_staging_restore_runtime_binding.py` validates the
   dormant policy and `--executable` refuses; focused tests cover both public validator APIs.
2. **Prerequisites:** harden adapters, safe-submit, and the provision tombstone path without
   enabling a workflow or live authority.
3. **Control/private services:** prove private ACR, Key Vault/DNS, plus locked binding, versioned
   evidence, and marker Blob paths; marker differs from both, while binding may share evidence's account.
4. **Marker posture:** approve legal holds, locked 90-day WORM, and provider create-only fidelity
   before provision or cleanup receives cleanup-tombstone create authority.
5. **Artifact/runtime template:** publish the immutable image; create the environment, exact
   pointer-free job templates, network policy, job-UAMI attachments, and four marker grants.
6. **External binding/dispatch:** separately bind the create-only publisher, sign only after gate
   5 supplies exact IDs/digests, then implement four pointers, private mailbox, per-action brokers,
   replay-safe `workflow_ref`/`workflow_sha` observations/start, exact per-job RBAC scopes, bounded execution-name receipt polling, signed cross-process receipt consumption, and containment.
7. **Target/cleanup:** authenticate a v2 receipt with DB role/FQDN, target server/VNet/subnet/PE/NIC/
   DNS zone/link/group IDs, and executor PE/NIC/DNS zone/link/group IDs; bind exact provision-UAMI
   Entra DB auth, then prove isolation, ownership, TTL, late materialization, tombstone, and cleanup. Apply the marker ReadOnly management lock last.
8. **Integration:** a separate PR may wire the protected workflow only after gates 1–7. Then
   separately authorize one founder-approved non-PHI rehearsal. Sanitization, migration,
   application access, cutover, and production/PHI promotion remain later approvals.

## Completion and recovery

- Complete after dormant/executable validation, focused runtime-binding tests, budget, and final review; feature/schema/data rungs are N/A. Recover by keeping it disabled or reverting source while preserving cleanup/sweep.
