# Held production configuration exporter — Tier 2

This separate source outcome supplies the existing held-write release control
through the normal production configuration authority. It changes no patient
policy, application code, schema, identity, Scribe setting, or billing setting.
The original held-capable source and all production control proposals retain
their own review and admission requirements.

The outcome contains seven files: three configuration controls, focused tests,
this plan, and the two matching fixed-107 canonical audit metadata files. The
audit uses the unchanged path inventory and verifier logic with actual Git
blob/mode hashes and a genuine committed source ancestor; it grants no release
or runtime authority.

`just direnv_export .env.production.backend` explicitly forwards
`HELD_MEDICATION_WRITES_ENABLED` as an input-only variable, defaulting to literal
`false`. The production template accepts only literal `true` or `false` and
exports the public runtime key. The input-only key is removed from the result.
The existing production `python_public_vars` declaration projects that value
to API, worker and scheduler; the independent Session Prep and startup-auto
controls remain unchanged. Arbitrary ambient variables are still cleared.

Keep the value false while the admitted held-capable image and additive schema
are deployed. Only after every server reader and the served browser asset are
qualified may a separately approved production configuration operation render
the explicit true input, retain it for each later plan, and review/apply the
normal saved plan. Require the exact key/value on all three ready workloads.
An ordinary source merge, health response or successful exporter is not
activation authority. No vault value or secret reference is changed here.

Focused validation executes the actual just clear-environment recipe and the
tracked production template using the existing data-only vault/helper-double
pattern at a synthetic direnv boundary. It covers false defaults, true/false,
malformed input refusal, helper-input isolation and shared role projection.
It does not establish real direnv trust, 1Password access, a live Terraform
plan, production readiness or source admission. Actual local checks remain
pending until the separately reviewed recipe executes.

Recovery stops new holds by retaining a separately approved explicit false
configuration on all three workloads, preserving held-capable readers and all
patient data. Before deployment this outcome can be reverted at its exact seven
paths. Do not roll back to a closed enum reader or erase held history after any
held row has been written. No production command is authorized by this plan.
