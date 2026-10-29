# Checkout return reflects current practice access

Risk: Tier 2 (billing journey). A manageable Stripe subscription reporting
`active` or `trialing` does not by itself establish current Core entitlement,
practice agreement acceptance, or clinician authorization. The old return notice
could claim access was ready and offer continuation while those checks denied it.

Require Core entitlement before confirming the return. Once billing is confirmed,
offer clinical continuation only when the current practice agreement and access
requirements pass, including revocation and ended access. Otherwise direct the
user to the existing practice requirements below. Polling remains bounded and
retryable; delayed confirmation continues to warn against paying twice.

This changes presentation only. Server authorization, Stripe requests, webhook
processing, prices, seat calculation, feature flags, schemas and release cohorts
are unchanged. No dependency, migration, backfill or external contract is added.
Existing billing activation controls still govern availability; no activation or
production operation is authorized by this patch.

Validate the new frontend refusal/recovery cases against the old implementation
before applying the repair. Exercise the real local API, signed webhook, durable
inbox sweep and clinical gate through expired access, idempotent checkout, broker
outage, confirmed subscription, payment failure/recovery, and cancellation. Use
committed synthetic database fixtures so independent billing transactions persist.
Only external Stripe transport and human identity verification are synthetic.
These checks do not establish live Stripe account or authenticated browser acceptance.

Independently review the final pushed commit, merge the narrow PR, and include it
in the next owner-selected immutable frontend candidate. Confirm both the ready
and remaining-requirements states in a verified synthetic practice before broader
activation. Production promotion remains founder-only and manual.

Recovery is a reviewed revert of this PR's frontend commit or selection of the
previous compatible frontend artifact. Keep server access gates and all billing
records intact; do not resend checkout or reset webhook/entitlement state as a
presentation rollback.
