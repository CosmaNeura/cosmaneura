# Session Handoff — 2026-05-28

Snapshot of where things stand at the end of this session, in priority order for picking back up.

## Open PRs

### PR #57 — CI annotation cleanup + Clerk N+1 fix
**Status:** Open, all checks green, ready to merge.
**Why merge first:** PR #59 sits on top of master and will inherit these fixes once #57 lands.

Commits (squashed):
- `f22ea41` `fix(ci): silence Clerk SDK warning + eliminate residual cartesian product`
- `3125452` `ci: drop Node 20 apt-tools dep + scope squawk to new migrations`
- `92fd962` `ci: drop apt cache step (root-owned /var/cache/apt/archives broke cache@v5)`
- `4c5398f` `fix(clerk): memoize getClient() promise to close concurrent-load race`

What it does:
- Drops Backend CI annotation count from 23 to 0.
- Test bug fix: `tests/job/extract_test.py::test_no_duplicates_on_failure` was asserting `DoctorNoteExample.where(DoctorNoteSchema.doctor_id == ...)` (wrong model in the filter); switched to `DoctorNoteExample.doctor_id`.
- Suppresses a noisy Clerk SDK UserWarning via `pyproject.toml` `filterwarnings`.
- Inlines apt install to drop the Node-20 transitive dep (`iloveitaly/actions-use-apt-tools` pulled in `actions/cache@v4`).
- Scopes squawk to migrations added in the PR (vs `origin/master`), so historical risky-DDL patterns stop polluting CI forever.
- Memoizes `getClient()` in `web/app/configuration/clerk.tsx` to collapse the 5× `/v1/environment` + 5× `/v1/client` N+1 documented in Sentry event `8b200f10f4554da6b8116f3aae72236c`.

Action: review and merge.

### PR #59 — Clerk test-auth bypass for integration tests
**Status:** Open, 5 consecutive CI runs verified the bypass works, ready to merge after #57.
**Depends on:** PR #57 (so the absorbed CI improvements line up).

Commits:
- `87bad85` `docs: design for programmatic Clerk session in integration tests`
- `b13c796` `docs: implementation plan for programmatic Clerk session in tests`
- `855c41c` `test(integration): explored Path A — programmatic Clerk session via sign-in tokens` (squashed failed exploration)
- `7da43bd` `feat(test): replace Clerk programmatic auth with env-gated bypass` (final working Path B)

What it does:
- Replaces the Clerk UI flow in `login_as_dev_user` with an env-gated bypass. Backend (`AuthenticateClerkRequest`) honors `Bearer test:user_test_<id>` tokens only when `PYTHON_ENV=test`. Frontend (`getClient`) returns a stub Clerk client when `window.__TEST_AUTH__` is set, gated by `VITE_ENABLE_TEST_AUTH=true` at build time.
- Integration tests are now 30–60% faster (3–5s vs 9–11s previously) because there's no Clerk redirect chain.
- `test_onboarding_flow` is untouched and still exercises the real Clerk UI flow end-to-end.

Why Path A failed (documented in commit `855c41c` and in issue #58 comment): Clerk's dev instance refused to redirect a `sign_in_tokens` ticket flow to arbitrary destination domains even after adding `api-test.cosma.localhost` as a satellite domain. Path A would need either a paid plan tier with redirect-allowlist controls or a real multi-domain SDK setup. Path B is self-contained and unblocks CI today.

Action: review and merge after #57.

## Open issues + ordering

Boss confirmed priority (Slack, this session): **#33 → #29 → #34**.

### Issue #33 — Notetaking improvements (doctor feedback)
**Priority:** Highest. Part of the provider-customization roadmap.

Six prompt/output tweaks from Pearson (4/4/25) and Jensen (5/5/25). Boss said: still valid, do them one at a time, start with these two — **highest visible impact**:

1. **Pronouns:** notes should use the patient's actual pronouns instead of defaulting to "they/them".
2. **Subjective from patient only:** explicitly instruct the model that the Subjective section pulls only from `Patient:` lines in the transcript, not from `Doctor:` interpretation.

Important context I confirmed during the session: prompts in this codebase are **partly per-doctor already** (the `DoctorNoteSchema`, `DoctorNoteExample`, and `Doctor.biography` are doctor-scoped — see `app/jobs/summarize.py::build_structured_prompt`), but the prompt **template itself** (the `dedent(f""")` block starting at `summarize.py:197`) is a single global string. So any tweak to the template lands for every provider at once. Flag this to the boss before merging the first change so it's an informed decision; mention that "per-provider prompt customization" is itself the bigger roadmap piece.

Remaining four items (do later, one PR each):
3. Sort plan bullets by patient todos then doctor todos.
4. Separate current medications from prescribed medications.
5. Plan bullets should include the reason ("do X for Y").
6. Review of Systems should add the doctor's insights, not repeat Subjective.

Eval set: I clarified to the boss that I meant a curated `(transcript, expected output)` regression set we run against the model before merging prompt changes — not anything that auto-migrates between users. If we have nothing today, even a small fixed set of 5–10 transcripts in `tests/eval/` with snapshot-style assertions on key fields would catch the worst regressions.

### Issue #29 — Auto-delete transcripts after 2 weeks
**Priority:** Second. Tech debt (not HIPAA compliance per the boss), but he wants the de-identified dataset retained for improving outputs.

What's already there (I verified):
- `app/jobs/anonymize.py::deidentify()` is wired up against Azure DeID's `"Surrogate"` operation.
- Credentials are deployed (`infra/variables.tf:35`).
- A live integration test exists (`tests/job/anonymize_test.py`, gated by `RUN_LIVE_DEIDENTIFICATION_TESTS=true`).
- `app/jobs/extract.py:271` already calls `deidentify()` when persisting example notes.

What's missing:
- `AIVisitTranscriptNonPHI` model — does not exist.
- `TranscriptDeletionJob` Celery command — there's a stub `def perform(): ...` with `# TODO implement!` at `anonymize.py:18`, nothing else.
- A daily scheduler entry to enqueue the deletion job for transcripts older than 14 days with `deleted_at IS NULL`.
- Removal of the existing read-time hack at `app/routes/internal.py:170–175` (currently clears `transcript_data` on read for transcripts >14 days old). The comment there already points at this issue.

Open question I raised with the boss (still pending an answer): is there anything Dave or Mike configured on the Clerk-side that we need to preserve when wiring up the transcript flow (specific PHI categories, locale, etc.)?

### Issue #34 — Usage dashboard
**Priority:** Third. Boss is the sole consumer (platform perf + business). Internal endpoint + simple page, daily snapshot. No BI tool needed.

Source SQL queries are in the issue body (5 of them: recent appts, doctor rankings, daily volume, pending uploads, avg time-to-note). Cheapest first pass: a single authenticated route under `app/routes/admin.py` that runs the five queries on each request (or a daily cron that snapshots into a small JSON file and a page that reads it).

## Side-band cleanup discovered this session

### `test_audio_file_upload` brittleness (separate issue worth opening)
- `tests/integration/upload_visit_test.py:40` asserts `expect(page.get_by_text("Notification Upload")).to_be_visible()`.
- The app's actual toast text is now "Upload complete" (see screenshot from CI run `26600206964`).
- This is independent of Clerk auth — it'd flake even without our work.
- Suggested issue title: "test(integration): test_audio_file_upload asserts on stale toast text"
- Fix is one-line: update the locator to match the current text, or use a more stable selector.

### Settings + notes N+1 in our own backend (Sentry event same as Clerk)
The Sentry trace that flagged the Clerk N+1 (`8b200f10f4554da6b8116f3aae72236c`) also showed `/internal/v1/settings` called 3× and `/internal/v1/notes` called 2× in the same `GET /` transaction. PR #57 only addresses the Clerk side. Worth a separate issue: probably component-level React Query duplication (multiple `useQuery` calls with different keys against the same endpoint, or a missing QueryClient share).

## Repo state at end of session

```
master (origin)
  └── ci/clean-up-annotations  (PR #57)   ← merge first
  └── test/clerk-programmatic-auth  (PR #59)   ← merge after #57
```

Both branches are clean; no local-only commits, no stale worktrees, no uncommitted changes.

## Files to look at when resuming

If you're picking up #33 first:
- `app/jobs/summarize.py:197` — the structured prompt template.
- `app/jobs/extract.py:140`, `:181`, `:221` — three other prompts (note-extraction pipeline).
- `app/models/doctor_note_schema.py` — per-doctor schema rows that the prompt template pulls from.
- `app/models/doctor.py` — `biography` field is already injected into prompts.

If you're picking up #29 next:
- `app/jobs/anonymize.py` — the DeID wiring + the `TODO` stub.
- `app/routes/internal.py:170–175` — the read-time hack to remove.
- `app/celery.py:186` — the existing scheduler config (where the daily job entry would go).
- `migrations/versions/` — new model migration goes here.

If you're picking up #34 last:
- `app/routes/admin.py` — natural home for the endpoint.
- The 5 SQL queries are in the issue body of #34; copy-paste them as-is into a Python module that exposes them via FastAPI.
