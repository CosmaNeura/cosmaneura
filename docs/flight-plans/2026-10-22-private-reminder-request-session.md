# Restore private reminders in the provider dashboard

Risk: Tier 2, because the fix touches the existing provider request-session and
private-note paths. One deployable outcome: an authorized provider can load and
save private reminders on an owned Scribe visit, including an unmatched visit.

## Failure and change

The staging private-note GET returned HTTP 500. A synthetic local HTTP request
with the installed ActiveModel session manager reproduced a nested global-session
error: the internal router already owns a request session, while the encounter
workflow tries to create a second global session.

The browser bridge will pass its existing server-side session to the encounter
workflow. The workflow must verify object identity with the current inherited
session. Native/service callers retain their existing fresh-session path. The
browser cannot supply this internal argument.

The dashboard will distinguish sign-in, access and invalid-request failures from
retryable or unknown failures. It will preserve unsaved input and the exact
unresolved mutation key/body, and will stop autosaving after access is denied.

## Required invariants and verification

- Current issuer, subject and session, actor, tenant, practice, ownership,
  creation/provenance and final provider reauthorization remain required.
- Read-only view-as remains denied. Foreign, missing and revoked access stays
  closed; no fallback ownership or fabricated provider link is introduced.
- A successful save follows the existing flush and durable commit. Commit
  failure or revoked final authorization cannot return success or retain a
  private-note change/receipt. Deadline, locks and rollback behavior remain.
- Actual request-scope regression uses the installed session manager. Test owned
  provisional read/write, exact-key retries, foreign/view-as denial, incompatible
  session identity, commit failure and final revocation against synthetic data.
- DOM regression covers classified errors, retained input, exact-key manual
  retry, cross-visit stale responses and navigation protection. Run focused
  backend checks, frontend typecheck, formatting and lint.

There is no schema/data migration, new dependency, configuration or cohort change.
Local fixture setup does not authorize a staging database operation.

## Review, release and recovery

Commit the final changes and obtain independent review of that exact commit,
including Cursor review. Reconcile the selected merge with current staging and
master. Build a fresh frontend for the changed component and an immutable Core
image; retain the existing public assets needed by active clients.

Prepare an exact staging-only image proposal before requesting any additional
Azure exception. The earlier Core04/Sovereign86 rollout approval does not identify
the new image. Production promotion remains a separate founder decision.

After rollout, verify the exact web/worker/scheduler image identities and health,
then repeat owned synthetic private-note read/save/reload and account denial.
Do not infer native Scribe sync solely from a dashboard save.

Recovery uses the previously accepted Core04 image, subject to the appropriate
deployment authority. No data structures are dropped. A reverted frontend may
show the earlier private-note failure; confirmed saved reminders remain stored.
