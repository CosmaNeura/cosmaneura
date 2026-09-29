# Native capture continuation through Core

When a configured CosmaBot recording renews its capture lease, its retained
native microphone owner sends `source_extend`. Core previously rejected that
operation before it reached Scribe. Core also lacked the read-only
`capture_lease_state` projection already used by CosmaBot recovery.

This additive receiver change carries the existing native owner proof and exact
extension intent to Scribe's `/v1/captured-sessions/{id}/source-extension` route.
It validates the returned session, creator, consent, owner, lease, reservation
range and bounded capability lifetime, then withholds the result if local
admission changed during I/O. A lost reply remains retryable with the same intent;
Core does not automatically perform a second mutation. Existing source-stop
ownership remains unchanged. No capture authority is inferred from a lease.

The new operation stays behind the existing `SCRIBE_CAPTURE_LONG_VISITS_ENABLED`
flag, which defaults off. The lease-state read uses the normal admitted-capture
checks and validates its nested session/lease. Existing provider discovery,
native context reads, 2,048-position protocol support, consent handling and
capture profiles remain unchanged.

## Flight plan and recovery

Tier 2: native microphone authority and patient-workflow contract. No schema,
identity, role, secret, dependency, or live environment change is included.

1. Merge the additive Core receiver after local checks and implementer review.
2. Select its exact merge commit for the existing separately approved staging
   rollout. Record current image/configuration drift before activation.
3. Pair it with CosmaBot API commit
   `5d33c5fe31d56e293503a9de1734afd19f65d428` or a validated descendant, and
   a Scribe receiver implementing the prepared native continuation contract.
   Scribe main `ec511ff1c3b979901e80f5033c1f02a05f65c4b3` was inspected and
   does not yet expose source-extension or capture-lease-state. The prepared
   implementation exists in the local W97 draft. Reconcile and validate that
   receiver first; do not claim that current Scribe main supports these routes.
4. Confirm the deployed Scribe image actually provides both routes and the
   expected contract before enabling continuation. A Git commit or SDK build
   alone does not prove this.
5. Explicit staging activation must include the existing Core long-visits flag
   and CosmaBot's matching long-visits flag as well as the separately approved
   companion caller/role settings. This source change activates none of them.
6. Qualify a synthetic recording across renewal, stop/upload and reviewed SOAP,
   including an uncertain extension reply and revocation. Keep desktop and
   iPhone runtime acceptance separate.

For rollback, retain the prior Core workload revision. Withdraw new recording
admission before disabling continuation so an active recording is not stranded
by a mid-visit configuration change. Existing captures still require their
normal physical stop, upload and reconciliation; rollback does not delete or
reassign saved audio.

The selectively reused local draft was
`w97-provider-workflow-r1/core-source`. Its older unrelated consent, profile and
provider-context code was not imported. Local synthetic tests exercise real Core
routing/HTTP adapters with explicit admitted-scope and Scribe-response doubles;
they do not prove deployed service or device acceptance.
