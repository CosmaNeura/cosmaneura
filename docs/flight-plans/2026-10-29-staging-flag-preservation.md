# Preserve active staging workflows in the Session Prep release

Risk tier: **Tier 2**, staging deployment control. No database, credentials,
runtime feature implementation, production configuration or identity changes.

## Problem and resulting behavior

The live Core staging API and worker use five flags absent from the local
deployer's ownership contract. Strict configuration preservation refuses the
unknown names. A release without that strict comparison could remove the flags
and interrupt the existing Scribe, Zoom or Session Prep monitoring workflows.

The release contract admits one optional, complete group of these five Boolean
values, while retaining both historical release formats. Values come from the
reviewed live-state observation; this change supplies no enabled defaults.
The deployer's actual owned-name arrays preserve each flag on its existing
workloads, and the complete effective-configuration fingerprint covers them.
Unknown keys, partial groups, ambiguous values, changed live values and flags
appearing on another workload remain refusals.

| Existing flag | Existing workload scope |
| --- | --- |
| `SCRIBE_COMBINED_SESSION_PREPARATION_ENABLED` | API only |
| `SCRIBE_NATIVE_REQUEST_TIMING` | API only |
| `SCRIBE_WORKFLOW_IDENTITY_REUSE_ENABLED` | API only |
| `ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED` | API and worker |
| `SESSION_PREP_HEALTH_ALERTS_ENABLED` | Worker only |

## Sequence and evidence

1. Independently review this source after its final substantive push and merge
   normally. It is separate from the automatic provider-access change.
2. Reconcile it with the final merged Session Prep/Scribe-compatible candidate.
   Bind the exact merged candidate, current protected controller, full-tree
   review, source archive, current schema and per-workload recovery revisions.
3. Re-read the five current flags and preserve their exact values in the
   complete ReleaseV2 `runtime_public` group. Revalidate all public frontend
   inputs and the secret-inclusive effective-configuration fingerprint without
   printing or persisting secrets. Use `STAGING_PRESERVE_EFFECTIVE_CONFIGURATION=true`.
4. Prepare and independently review the complete founder-authorized staging
   release package. This source does not grant invocation authority.
5. Use the existing bounded controller, publisher and deployer to release one
   immutable Core image, worker then scheduler then API. Verify the new images,
   flags, healthy revisions, Session Prep and Scribe-compatible encounter flow.
   Scribe's own applications/jobs and public Sovereign are outside the targets.

Executed tests must run the deployer's real Bash arrays/contract builder and
the real pure comparison CLI, showing correct workload scope, changed-flag
fingerprint changes, live drift/foreign-workload refusal, exact group/value
validation, historical acceptance and value-free failures.

## Recovery

Keep the existing per-workload image/revision recovery bindings and their
complete configuration. Do not replace them with a fabricated common digest.
On a failed release, preserve the observed evidence and use the previously
reviewed retained-revision recovery or a reviewed fix-forward. No schema change
or downgrade belongs to this patch. Reverting this source restores the earlier
deployer's omission and therefore cannot be used to deploy over the current
flagged runtime without a separate explicit reconciliation decision.
