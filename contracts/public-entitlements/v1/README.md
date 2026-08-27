# Public capability entitlement contract v1

`capability-entitlement.schema.json` is Core's canonical, server-authoritative
read contract for public-provider capability access. Clients render the returned
decision; they never reproduce or relax the policy.

## Policy frozen by v1

- Capability keys are exactly `soap_note`, `intake_prep`, `session_prep`,
  `scribe`, and `cab`. Unknown keys and unknown enum values fail closed.
- Every public self-service clinician begins credential review in `pending`.
  `pending`, `needs_review`, and `rejected` deny real-PHI access. Only
  `approved`, backed by reviewer evidence outside the applicant boundary, may
  pass this gate. No public route may mutate credential state or self-approve.
- A pending clinician may use only a separately enabled, explicitly synthetic
  non-PHI sandbox. `data_mode: synthetic_only` is not permission to process
  real PHI, and `decision: denied` remains the protected-route result.
- The trial begins when Checkout starts, before credential approval. Credential
  state does not pause or reset the clock. The still-undecided behavior when the
  trial expires before approval is not represented as an entitlement.
- Billing verification states `pending_verification` and `invalid` are preserved
  in the wire contract and always fail the subscription gate. They are not
  coerced to `trialing`, `active`, or another entitled state during an incident.
- An active clinician seat is an accepted membership whose seat state is active
  and whose access has not been revoked. The owner counts. Pending invitations,
  inactive memberships, revoked memberships, and expired/revoked invitations do
  not count. The Core database is authoritative; Stripe quantity is a durable,
  asynchronous mirror.
- Activation, reactivation, and invitation acceptance serialize on the practice
  and atomically reject an active-seat count above 20. A Stripe failure cannot
  turn a rejected 21st seat into an active membership or silently clear the
  durable reconciliation obligation.
- `session_prep` is read-only. `scribe` is web/PWA first and retains one
  client-neutral authority contract for later clients. `cab` is advisory-only;
  it never authorizes an EHR write or claim submission.

## Read and seat-policy APIs

Core exposes the schema from `GET /internal/v1/capabilities`. The authenticated
principal and practice membership determine actor and tenant context; neither
is accepted from query parameters or request bodies.

Core's in-process seat-policy API is the constant
`app.services.public_entitlements.MAX_ACTIVE_CLINICIAN_SEATS` and
`app.services.public_entitlements.active_clinician_seat_count(session, practice_id)`.
Callers must not count rows independently. Stripe consumes the resulting count and
`stripe_quantity_sync_required` obligation, not derive quantity from pending
invitations or client state.

## Compatibility and safe defaults

V1 additions are backward compatible. Core retains v1 throughout the paid
pilot; removal or semantic change requires a new version and a receiver-first
consumer migration. Missing policy data, dependency failures, unknown states,
and disabled rollout controls return a denied decision. `intake_prep`,
`session_prep`, `scribe`, and `cab` default disabled until their own activation
gates pass.
