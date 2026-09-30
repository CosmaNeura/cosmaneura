# Scribe note readiness in staging

Risk: Tier 2 under the repository's patient-data-path classification. This
change adds existing authorized route reads only; it changes no authorization,
schema, note generation, clinical review, save operation or service contract.
Owner: John Clemente. Cohort: existing staging test users. Review by 2026-10-07
before expanding distribution. Production is excluded.

The owner reported needing refreshes in Scribe and staging after a successful
Zoom-to-SOAP test. Core publishes the transcript before the draft can finish;
the browser's original loader did not revalidate that transcript-only view.
The Scribe client recovery change is separately deployable and not required for
this browser improvement.

## Validation and release

1. Run the synthetic router/page tests covering delayed draft publication,
   single in-flight checks, hidden tabs, the fixed 90-second window, manual
   recovery, route changes and existing access/error boundaries. Confirm no
   preparation/save calls and no refresh of editable drafts.
2. Record the exact tested/reviewed SHA, results and implementer review in the
   PR. Merge passing work under the owner's local-validation authority.
3. Reconcile current staging source, digest, schema and configuration. Preserve
   newer Session Prep/CosmaBot work. Build the exact merged candidate with the
   existing staging configuration and packaging path; validate its source and
   artifact provenance before publication or traffic changes.
4. Verify synthetic note readiness on the delivered revision. Report deployment
   separately from local tests; do not claim the owner's original encounter or
   recording fidelity was independently inspected.

## Recovery

Retain the previously active immutable image/revision and its traffic snapshot.
If readiness or existing access checks regress, restore that prior revision's
traffic. No database rollback, recording processing, patient/account change,
saved-note overwrite or migration is part of this release.
