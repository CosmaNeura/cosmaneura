# Session Prep saved patient identity selection

Risk tier: **Tier 2**, because this changes selection of historical patient notes.

The web app enables Scribe encounter creation; the worker leaves that creation
gate disabled. Session Prep currently uses the same gate to decide whether it
honors stored patient matches. A reviewed native visit still associated with its
original provisional patient can therefore appear in the web app's four-source
selection while the worker prepares only three sources.

This change honors existing `matched` and `confirmed` identity decisions when
selecting history, independently of whether new encounters may be created. It
preserves the provider, original source patient, canonical patient ownership and
explicit different-identity checks. Unmapped provisional visits stay separate;
existing appointment and review provenance remain unchanged.

## Reviewed scope and evidence

- One selector change and focused synthetic tests; no new dependency or schema.
- The regression runs the actual selector and source-digest functions against
  four synthetic reviewed notes with both creation-gate values. Matched and
  confirmed visits produce the same sources and digest; provisional, unmapped,
  foreign-provider and conflicting patient decisions remain excluded.
- The existing integrated matching test no longer needs to enable the creation
  gate on the source selector. Its capture/private-content checks remain.
- No flags, configuration, patient data, Scribe services/jobs or Sovereign change.

## Staging sequence

1. Review and merge the exact source commit. Record the live pre-update web
   image (`5a1e7f…`, Core 68) and worker image (`dacd9…`, Core 872) separately,
   including full immutable digests and configuration hashes, in the release
   manifest for recovery.
2. Record separate child build parents: the locally accepted Saved-answer web
   image (`52ddb8…`) and the retained worker image (`dacd9…`). Prepare each child
   with only the reviewed selector-file replacement. Compare the resulting file
   and inherited inputs so unrelated web/worker differences remain intact. The
   web update also delivers the already-reviewed Saved-answer static change;
   its build parent is distinct from the current live web recovery image.
3. Update the worker, then the web app, to their respective reviewed child images.
   The scheduler only schedules work and is outside this selector update.
4. Reuse the authorized fictional provider-owned visit to verify a four-source
   build, matching source digest and ready Session Prep consumption of the
   corrected, freshly reviewed native note. Check retained workload/configuration
   identities and availability. Local regressions alone do not prove this live
   handoff.

## Recovery

Restore each affected workload to its own exact pre-update image recorded in the
release manifest. Preserve all data, patient identities, explicit reviews, queued
work and immutable artifacts; perform no migration rollback or source cleanup.
The prior web/worker selection mismatch will return, so the affected live handoff
remains unqualified until the reviewed fix is restored and verified.
