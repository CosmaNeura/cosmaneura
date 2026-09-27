# Session Prep prior-visit review checkpoint

Tier 2: provider record reads and an additive dashboard response. The owner
requested a comparison against updated staging/main, followed by a bounded,
committed feature checkpoint for the next combined Scribe release. No staging
or production mutation is part of this checkpoint.

## Reconciliation on September 24, 2026

Core master is `82e8ca268cff7bfd3329c6193808bc129a78912e`. Staging web, worker
and scheduler all serve image
`sha256:c7cdae9307ba085931544ec77322d41d939604cf864b40e4cc37c70aeab5bf09`,
whose registry tag is that exact commit. The previous saved integration at
`593237f4` has been imported through the release stack and followed by further
capture/recovery fixes. It is not a remaining uncommitted patch.

Sovereign main is `c39ffc3`; its provider staging revision is
`cosmaneura-sovereign-staging-app--spc39ffc318da`, image
`sha256:ae84e0b974d94c82bf0e8b32efc35f891c8f517106de7eb6249ae23ec85572ae`,
tagged `c39ffc3efaec-3a5da0f04a24`. The prior operational-beta checkout is clean
and its changes are already merged. Unrelated edits in the original Sovereign
workspace are preserved. Scribe service images, checkouts and builds are untouched.

## Provider outcome

The current visit page shows recent prior notes, which need review, and the
actual current selection for Session Prep. A link opens the exact note in a
separate tab with that note expanded. After explicit review, the provider can
return and refresh the original visit before requesting preparation. The read
does not review notes, queue a build, or return note content.

The existing selected-provider cohort, authenticated doctor/practice access and
view-as restrictions remain in force. Selection reuses the existing provider's
patient, prior-visit cutoff, 548-day window, current content digest and validated
review evidence. The response includes 20 recent notes plus any selected older
notes, at most 25. Review lookup uses unique note/digest pairs rather than reading
unbounded revision history. Included status describes source eligibility at the
time of the read; it does not claim that a brief has finished generating.

## Verification and release

Use the owned `beta_prep_prior_visits_test` synthetic database for source/review
and HTTP tests. Cover explicit review and correction, excluded record scopes,
future/old/superseded sources, invalid content, older selected notes outside the
preview, and the five-note selection limit. Verify browser links, exact-note
expansion, empty states, refresh and existing review/transcript behavior. Run
Python/browser types, changed-file lint and a production-bundled local build.
Obtain independent Cursor review after the final substantive push.

Completed locally: 37 source/review cases, 30 notes/review/runtime cases
(overlapping groups, not an additive total), and 15 browser cases. Python and
browser type checks, changed-file Ruff/ESLint/format checks, and the production-
bundled test build passed. All records and build configuration were synthetic.
These results do not represent a deployed UI or native-device acceptance test.

Cursor found no blocking issue at `05499580`. Its three low-severity UX findings
were addressed: preview dates now follow the existing visit-header wire format;
an explicitly opened review tab hides its own prior-visit panel; and only the
requested note opens, with explicit scrolling after the client-rendered mount.
The URL focus is accepted only for a current note returned by the authorized
loader. Fifteen backend and fifteen browser follow-up cases and browser types
passed. This preserves the existing date-display convention rather than changing
the application's global timezone behavior. Renew review on the final push.

This is an additive Core-only update: no new migration, dependency, Scribe API
contract or Sovereign release is required. Merge the reviewed PR with the next
stable Scribe Core changes, run the existing combined-release checks, then deploy
one Core image to web/worker/scheduler. Reconcile intervening commits before that
release. Validate the review-to-later-Prep flow on staging with synthetic records.
Recovery restores the previous Core application image; all existing notes and
review evidence remain intact. The user controls the next staging rollout.
