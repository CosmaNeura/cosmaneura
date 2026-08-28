# Public capability entitlement contract v1

`capability-entitlement.schema.json` is Core's canonical, server-authoritative
preflight contract for public-provider capability access. Clients render the
returned decision; they never reconstruct, weaken, or cache an allow decision
across an execution request.

## Policy frozen by v1

- Capability keys are exactly `soap_note`, `intake_prep`, `session_prep`,
  `scribe`, and `cab`. Unknown versions, keys, enum values, or dependencies fail
  closed.
- Authorization uses verified identity, an active and authorized practice
  membership and role, owner authority for owner-only mutations, current Terms,
  Privacy and BAA acceptance, an authoritative subscription entitlement, tenant
  isolation, feature-specific consent, and capability rollout state.
- Professional credential or licensure review is not an authorization gate.
  NPI is absent and cannot prove identity, licensure, owner authority, tenant,
  payment, entitlement, or capability access. CosmaNeura does not claim to
  verify professional licensure.
- An `allowed` response is a preflight result. Every PHI execution path must
  re-resolve identity, membership, tenant, entitlement, rollout, and the
  feature-specific consent context at execution time.
- Billing verification states `pending_verification` and `invalid` are preserved
  in the wire contract and fail closed. They are never coerced to `trialing` or
  `active` during an incident.
- A legacy pilot record can satisfy only the entitlement gate, only when its
  stored practice snapshot matches the actor's active authorized membership.
  It never replaces identity, membership, role, BAA, tenant, consent, or
  rollout checks.
- An active clinician seat is an active, non-revoked membership. The owner
  counts. Pending invitations and inactive or revoked memberships do not count.
  The Core database is authoritative and the maximum is 20.
- Intake Prep has exactly five versioned questions, modifiable only by an
  authorized practice user. Session Prep is read-only. Scribe is web/PWA first
  with supported clients added later.
- CAB is advisory billing intelligence only. Its contract fixes EHR write,
  claim submission, and clearinghouse submission to `false`; it does not
  authorize 837P generation/submission, payer enrollment, or patient contact.

The paid-pilot price is $89 per active clinician per month. Card collection is
part of app-owned onboarding Checkout, and the fixed 14-day clock begins only
when an authoritative hosted Checkout Session is created. Checkout creation is
not entitlement. The commercial action after trial expiry remains undecided and
is deliberately absent from this contract.

## Seat-policy API

Core's in-process seat-policy API is:

- `app.services.public_entitlements.MAX_ACTIVE_CLINICIAN_SEATS`
- `app.services.public_entitlements.active_clinician_seat_count(session, practice_id)`

Callers must not count rows independently. Stripe consumes the canonical count
and durable quantity-sync obligation; pending invitations and client state are
not billing authority.

## Compatibility and safe defaults

This v1 artifact is being re-frozen before its first merge under the 2026-08-28
owner policy. All earlier credential-gated v1 bytes and digests are superseded.
The evidence boundary does not expose `GET /internal/v1/capabilities`; that
runtime producer lands receiver-first in a later independently reviewed slice.
Until it does, capability rollout remains disabled and consumers must fail
closed rather than synthesize a response.
