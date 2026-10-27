# Capture medication verification and compact source notes

Risk tier: **2** (clinical note generation). This changes newly generated capture
drafts only. No schema, provider, dependency, deployment, or saved-preference change.

A known-role SOAP capture repeatedly omitted the current medication-list verification
limit and a separate prospective decision that no medication changes were planned.
Past unchanged medication history and a narrower no-new-medicine decision do not
replace that broader prospective decision.

The response schema requests Plan source references before narrative fields. A
server-rendered verification limit is then retained in an existing nonexcluded
Medication or Objective field. It says the list is unverified; it does not say a
list was absent or the person takes no medication. Every generated/source statement
remains intact. No field is created for unsupported schemas.

The separate rendering commit changes only the framing of selected history entries:
compact attributed bullets replace repeated transcript headings and paragraph gaps.
Complete source text, role qualification, entry order, selection, prompt, schema,
and Plan output remain unchanged. Known-role narrative notes do not use this
source-history renderer and remain byte-identical in the replay.

## Validation and limits

- 217 focused tests pass; Ruff passes. The owned PostgreSQL schema was dropped.
  Coverage includes save/reopen/replay, malformed references, medication scope,
  source instructions, person/role boundaries, and complete trailing qualifiers.
- The related baseline suite retained 79 passes and two stale pre-848 Plan-frame
  assertion failures. Those assertions now match the already existing Plan bullets.
- Seventeen actual configured-model calls used fixed synthetic sources, timeout
  30 seconds, zero retries, and at least 65 seconds between calls. There were no
  database connections, saved-note regeneration, or live capture actions.
- The sixteen-case matrix passed the unchanged accepted calibration and required
  Plan checks in all cases. Raw/strengthened checks passed 15/16: their original
  first-context-header parsing failure in the person fixture remains recorded.
  Actual complete source entries and the preselected calibration retain each person.
- All seventeen retained responses were replayed offline through exact old/new
  production resolvers. Every old field was reproduced exactly; every selected
  complete entry, role and order survived the compact renderer; other fields and
  Plan were unchanged. No further model call or historical regrading occurred.

[Validation and source pins](../../tools/capture-retention/note-quality-validation.json)
record per-case results and the remaining limits. The
[actual decoded responses](../../tools/capture-retention/note-quality-model-receipts.json)
are retained with hashes and explicit source fixtures. Canonical R2 has its own
fixture because it differs from the older repository transcription; the older
fixture and historical failures remain unchanged.

Mixed clinical/editorial source entries and duplicate follow-up still appear.
One known-role response expanded the unstated conventional unit `mmHg`; that strict
source-fidelity finding remains separate from clinically material omission or
changed medication scope. Literal unanswered-question copying is optional; no
invented answer is permitted. These samples and formatting proofs do not establish
universal clinical completeness, UI usability, live workflow or deployment readiness.

## Recovery and review

Revert this change to restore prior behavior for future drafts. Existing saved
notes and deliberate clinician edits keep their normal early-return path and are
not regenerated. No migration or data rollback is needed.

Independent agent review is required after the final substantive push. Model
qualification, author review and structural replay are evidence, not approval.
