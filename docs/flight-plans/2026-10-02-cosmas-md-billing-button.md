# Cosmas MD billing confirmation button

Tier 2: provider note and billing-context UI. Depends on the additive Core
billing source/operation API; this change introduces no schema or service auth.

## Outcome

Each current note may expose a Billing header action only when Core supplies an
admitted source for that exact note, appointment and schema. Opening it makes no
request. Providers explicitly enter visit context and confirm the selected note.
The browser sends its source hashes and one operation UUID, never note text or
actor/practice overrides. Recording-derived care-time omissions are explained.

This first UI consumes durable preparation. It accurately reports that the
billing connection is not configured and no header has been generated. The
subsequent authenticated Core-to-CAB transport will consume that saved source
and provide generation/read/edit in this same provider workspace.

## Failure and recovery

A pending or uncertain save retains the exact command for retry and locks the
form and dialog close. A confirmed source/access conflict requires note reload.
Changing visit fields clears human confirmation. The existing notes page,
clinical-note copy and Session Prep action remain independent.

No default clinical values, patient data in URLs, browser storage, external
navigation, workload credentials or automatic model requests are added.

## Release sequence

1. Merge the default-off Core source-preparation receiver and API snapshot.
2. Merge this UI. With the server admission empty, the button stays absent.
3. Complete and accept the separate service identity/operation-authority bridge
   and header continuation before activation. Use only synthetic/deidentified
   source admissions until the real-data gates are separately satisfied.
4. Keep staging and production unchanged during local development.

Recovery is to remove the button/map consumption or leave source admissions
empty. Preserve any prepared operations; do not roll back the additive schema.

## Local validation

Sixteen focused component/contract checks passed, including exact-command retry,
wrong-receipt rejection, source conflict, explicit context confirmation,
unchanged clinical-note copy, and the existing Session Prep review controls.
The frontend type-checks against the generated actual internal API schema.
Desktop and 390px mobile layouts were inspected using the actual components
and a synthetic display harness with non-local requests blocked.
