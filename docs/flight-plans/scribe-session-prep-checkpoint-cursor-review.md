# Independent Cursor source checkpoint review

Recorded after the final substantive branch push on 2026-09-23. Cursor ran in read-only Ask mode with its sandbox enabled. This review concerns the source checkpoint, not a staging deployment. The implementer independently verified all 1,070 exported source-file hashes against the clean Git worktree before this report; the reviewer did not recompute them.

---

I found no blocker introduced by the fixes. All six earlier findings now have a disposition. Four are resolved (1, 2, 5, 6), one is retained and documented (3), and one is resolved with stated limits (4). The source checkpoint is acceptable as a stable checkpoint. This is a source assessment only, not staging or release signoff.

- **Reviewed commit:** `028a224ffa5cdd89bb4465382a39251e9cdeadbc`
- **Delta base:** `6a51a0f4d0130af040473b7f2685da079a4a5679`
- **Diff SHA256, as recorded in the manifest:** `252cec7428f2470bf32af123c4593ccb2fe88b28282a87ec70a5ffe57125220c`

## Dispositions of the original findings

**1. Workload separation between Sovereign and Scribe (was High): Resolved.**
- In `app/configuration/service_auth.py` (lines 188–221), the capture allowlist is empty unless the receiver is configured. When the receiver is set, capture allows only the Scribe ID and the Sovereign-tool policy allows only the Sovereign IDs, each with a required count of 1.
- If the receiver is unset, capture routes fail closed with 503. If it is malformed or overlaps a Sovereign ID, the app fails at import.
- Route wiring is correct:
  - `/tools/invoke` (`app/routes/agent.py:561`) uses the Sovereign-tool authenticator.
  - All five routes in `app/routes/scribe_capture.py` (lines 214, 272, 364, 393, 497) use the capture authenticator.
- The shared protocol endpoints (bootstrap, resolve, conversation turns, audit and usage events) still accept both workloads. That is intentional, and interaction lineage is bound to the calling workload.
- Minor residuals, not blocking:
  - `tests/authority_kernel/workload_feature_boundaries_test.py` checks 3 of the 5 capture paths for the wrong-caller 403. I confirmed the other two (capture-operations and encounter-context) by reading the wiring.
  - All four authenticators share one log namespace, so their log lines can't be told apart.

**2. Background summarize publishing a note for a capture-owned appointment (was Medium): Resolved.**
- `app/jobs/summarize.py` checks for a capture binding before generating anything (lines 74–85). It checks again after taking the appointment `FOR UPDATE` lock (lines 207–217), which covers a job already in flight.
- The only caller that passes `authorize_finalization` is `app/services/scribe_capture_notes.py:965–971`.
- Bindings are created only in `app/services/scribe_capture_scope.py`. That happens under the same appointment `FOR UPDATE` lock (lines 154–156), inside the same transaction (created at 289, added at 294, committed at 312). So the recheck under the lock does protect publication.
  - If the background job wins the lock, its note publishes first and capture then refuses.
  - If binding creation wins, the job returns `False`.
- The lock order is consistent, so I see no deadlock path.

**3. Historical multi-segment Recall source membership: Retained and documented.** The flight plan (lines 77–80) records it as a known boundary and does not claim a fix. Current capture paths use an explicit version source.

**4. Test gaps: Resolved, with limits.**
- The handoff test now uses distinct identities: `222…` for Scribe and `prep.WORKLOAD` for Sovereign.
- `test_two_current_notes_keep_distinct_sources_and_reviews` checks that the legacy `transcript` field is empty, that each note maps to its own source, and that review status is tracked per note.
- Limits:
  - The database-backed capture and handoff tests are opt-in and skip without `W8_CORE_CAPTURE_TEST_URL`.
  - CI is disabled.
  - The handoff test runs in a shared session.
  - Live Entra identity and the native client are not exercised.

**5. Patient context cutoff and ordering: Resolved.**
- The cutoff and `before` are now normalized to naive UTC, and the ordering adds `AIVisitNote.id desc` as a tie-breaker.
- The `America/New_York` session test would actually catch a regression. Under the old code, the note just inside the cutoff would be dropped, because an aware `before` compared against a `timestamp` column gets interpreted in the session time zone.

**6. Invalid Scribe origins: Resolved.** `valid_scribe_origin()` is checked in `is_complete()`, and `build_scribe_workflow_client` raises before any credential is created. Bad ports and unparseable hosts raise `ValueError`, which is caught inside the validator.

## Non-blocking notes

- **An abandoned capture binding silently stops background notes for that appointment.** Binding resolution alone creates a binding (`scribe_capture_scope.py:288–295`), before any admission or transcript. Scenario: a clinician opens Scribe for an appointment, then uses an uploaded or Recall recording instead.
  - Summarize skips the appointment.
  - The capture note path also refuses, with a `CaptureNoteConflict`, because the only transcript doesn't match a capture source.
  - The result is a transcript with no note. There's no wrong data and no data loss, and no signal to the user beyond a log line.

  This matches the stated "retained binding owns preparation" design. The flight plan (lines 60–62) states the refusal but not this consequence. Before release, record it as a known operational limit with a recovery path. It is not a checkpoint correctness fix.
- **The flight plan attributes two "release-blocking" gaps to my first review (line 57).** My first report rated workload separation High and background summarize Medium. The substance is right; only the severity wording differs.
- **Out of scope:** the pre-existing `VisitTranscriptViewer` hook-after-early-return.

## Checkpoint vs. later staging and release

For this checkpoint I consider the source stable, with no pending correctness fix.

Still required before staging integration or release:
- live Entra app-only identities for both workloads, with the receiver configured;
- behavior of the native and external Scribe client;
- cross-repo contract verification;
- a test run by someone other than the implementer, with CI enabled or equivalent;
- a decision on the abandoned-binding recovery path;
- the retained Recall multi-segment limit.

## Review limits

This was a static review only. I executed no tests, builds or migrations. I did not recompute the diff hash or file hashes; they are reported as recorded in the manifests.

I reviewed the changed files plus the call sites and lock paths they depend on, not all of the source tree. For four unchanged core files, the recorded per-file hashes in `source-sha256.json` match the earlier bundle's recorded hashes. That is consistent with the change since the earlier bundle being limited to this diff, but I did not re-hash anything.

The implementer's test counts (93, 42, 61, 29, the type check, Ruff, and the earlier 1542/329/136) are implementer evidence, not results I reproduced.
