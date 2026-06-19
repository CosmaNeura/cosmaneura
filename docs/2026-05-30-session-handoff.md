# Session Handoff — 2026-05-29 / 2026-05-30

Snapshot of where things stand at the end of a multi-day session that closed the full roadmap from the prior handoff (`docs/2026-05-28-session-handoff.md`).

## What was shipped

In priority order per the prior handoff (#33 → #29 → #34), every item shipped as its own PR. All eight PRs are merged into master.

### Issue #33 — Notetaking improvements (6 items)

Closed end-to-end across 5 PRs. Each touched only `app/jobs/summarize.py::build_structured_prompt` (the global structured-prompt template) plus one new live-LLM eval test in `tests/job/summarize_test.py`. Live evals are gated by `RUN_LIVE_LLM_TESTS=true` and skip in CI; manual verification scripts in `/tmp/` carried the verification load because the staging Azure OpenAI judge model returns 404 (see "Side-band items still pending" below).

- **PR #60 — items 1+2 (pronouns + Subjective-from-Patient).** `cbf0c50`. Pronouns inferred from the transcript itself; Subjective sourced only from `Patient:` lines. Strengthened the pronoun instruction once during impl because the first pass left the model defaulting to "the patient" everywhere.
- **PR #61 — item 3 (Plan sort patient-then-doctor).** `d2fa15b`. New sub-bullet under instruction #3; no renumbering. The baseline already ordered patient-then-doctor on the diabetes test transcript so the test acts as a regression check.
- **PR #62 — item 4 (Medications: tag newly-prescribed inline).** `e4c7612`. Real signal in baseline — the model omitted the newly-prescribed omeprazole from Medications entirely. Post-fix tags it `(new this visit)` verbatim.
- **PR #63 — item 5 (Plan bullets must include reason).** `7292098`. Real signal in baseline — only 1 of 4 lumbar bullets had an actual reason. First pass got 3/4; strengthened the instruction to disqualify trigger conditions as reasons, then 4/4.
- **PR #64 — item 6 (Review of Systems distinct from Subjective).** `9fdc3d7`. First test that uses an **in-memory `DoctorNoteSchema`** because the seeded "Test SOAP Note" schema has no ROS field. Baseline had ROS organised by system but Subjective duplicated the responses; post-fix splits cleanly with no overlap.

The prompt template's numbered block is now 1–16 (was 1–11 at the start of the session). Renumbering walks were always part of each plan to avoid drift.

### Issue #29 — Auto-delete transcripts after 14 days

- **PR #65 — `8f707e5`**. Single PR containing:
  - New `AIVisitTranscriptNonPHI` model + alembic migration (no FKs, no timestamps — both would let timing/relationship metadata reattach to the original PHI row).
  - `TranscriptDeletionJob` implemented at `app/jobs/anonymize.py::perform` — was a stub before. Deidentifies each `TranscriptEntry.content` via existing `deidentify()`, persists into NonPHI, clears `transcript_data` + sets `deleted_at` on the original. Idempotent on the `deleted_at` guard.
  - New `app/jobs/transcript_cleanup.py` daily sweeper. Beat schedule entry at 03:00 UTC (outside US clinic hours).
  - Removed the read-time hack at `app/routes/internal.py:170-175` and updated the two tests in `tests/routes/notes_test.py` that asserted on it (`test_note_detail_deleted_transcript` updated to clear `transcript_data` alongside setting `deleted_at` like the real job does; `test_note_detail_old_transcript` deleted because it tested the now-retired clear-on-read behaviour).

Live-Azure DeID test (`tests/job/anonymize_test.py::test_anonymize`) remains gated by `RUN_LIVE_DEIDENTIFICATION_TESTS=true`. The new unit tests in `tests/job/transcript_deletion_test.py` mock `deidentify` so they run unconditionally.

### Issue #34 — Usage dashboard

- **PR #66 — `fe6c152`**. Admin-only `GET /internal/v1/admin/usage` endpoint serving the five platform-usage queries the boss has been running by hand. Raw SQL via `session.execute(text(...))` (precedent: `app/configuration/versions.py:49`). New `app/services/usage.py` for the helpers + Pydantic row types; route + response model in `app/routes/admin.py`. Test in `tests/routes/admin_usage_test.py` covers 401 for non-admin and 200 + structural shape for admin.

Differences vs. the queries in the issue body:
- `LIMIT 50` and `LIMIT 10` extracted to module constants.
- `WHERE vaf.created_at > '2025-04-10'` parameterised as bound `:since` with the same default.
- Commented-out debug `WHERE d.email = '...'` in pending_uploads dropped.
- Interval-typed AVG/MAX/MIN columns converted to float seconds for JSON-serialisability.

A late CI failure caught during this PR: a snapshot test (`tests/unit/openapi_schema_test.py::test_openapi_schema_matches_generated_file`) requires the FE `web/openapi.json` to match the FastAPI-emitted schema. Adding a new endpoint shifts the schema, so you must run `just js_generate-openapi` before pushing. Captured in PR #66 as a follow-up commit.

### Side-band #1 — `test_audio_file_upload` stale toast (open in flight)

- **PR #67 — open, CI in flight on the fix-of-fix.** First commit replaced the stale `"Notification Upload"` text with `"Upload complete"` (matching the actual toast emitted by `web/app/components/notes/UploadRecordingButton.tsx:215`). That fix tripped Playwright strict mode because radix-ui's toast aria-live region also contains "Upload complete" as a substring (`"Notification Upload completeYour recording has been..."`). Second commit (`25ab325`) switched to `get_by_text("Upload complete", exact=True)` which hits only the visible toast title `<div>` and not the aria-live region.

Status at end of session: CI run `26673145158` was in progress on the rebased fix. Should be green; if not, the same locator can be expressed via role: `page.get_by_role("status").filter(has_text="Upload complete")`.

## Open PRs / branches

```
master (origin)
  └── fix/upload-test-stale-toast  (PR #67)  ← only one open; CI in flight
```

The local working tree at session end is on `fix/upload-test-stale-toast`. One untracked file: `docs/2026-05-28-session-handoff.md` — the prior session's handoff, left intentionally uncommitted by its author.

All branches associated with merged PRs were squash-merged remote-side and deleted locally during their corresponding rebase/cherry-pick cleanups. No stale local feature branches.

## Side-band items still pending (not in any PR yet)

These came up during the session and were noted in PR bodies / commit messages but never opened as their own issues. Each is one or two PRs of work.

### 1. Settings + notes N+1 in our own backend

Sentry event `8b200f10f4554da6b8116f3aae72236c` (the same one PR #57 already addressed for the Clerk side) also showed `/internal/v1/settings` called 3× and `/internal/v1/notes` called 2× in the same `GET /` transaction. PR #57 only fixed the Clerk N+1. Suspect: component-level React Query duplication — multiple `useQuery` calls with different keys against the same endpoint, or a missing shared QueryClient. Worth a separate issue.

### 2. Judge model `gpt-4.1-mini-2025-04-14` not deployed on staging Azure OpenAI

Flagged in the body of every PR from #60 through #64. `tests/eval.py:8` sets `OPENAI_MODEL = "gpt-4.1-mini-2025-04-14"`. The note-generation model (`gpt-4.1-2025-04-14` per `app/configuration/openai.py:20`) IS deployed; only the **judge** model is missing. Every live-LLM eval call therefore succeeds at note generation but fails at the judge step with `404 DeploymentNotFound`. Workarounds used throughout the session were one-off `/tmp/manual_verify_*.py` scripts that called `generate_clinical_note` directly and eyeballed the output.

Cheapest fixes:
- Deploy `gpt-4.1-mini-2025-04-14` on the staging Azure OpenAI resource, OR
- Gate the judge model name behind an env var so dev shells can substitute a deployed model name without changing source, OR
- Wire the eval judge to OpenAI direct (not Azure) under a `RUN_LIVE_LLM_TESTS_VIA_OPENAI=true` flag for local-only use.

Worth a tracked issue. Without it, the live evals committed in PRs #60-64 can't actually run end-to-end against staging.

### 3. Dave/Mike Clerk-side DeID config

Open question carried from the prior handoff and into PR #65's body: "is there anything Dave or Mike configured on the Clerk-side that we need to preserve when wiring up the transcript flow (specific PHI categories, locale, etc.)?" PR #65 shipped using the existing `deidentify()` defaults (Surrogate operation, API version 2024-11-15) which already power `extract.py:271`. If Dave/Mike has specific overrides, the deletion job in PR #65 won't be picking them up. Worth a ping; if they confirm there's something to preserve, it's a follow-up PR to `app/jobs/anonymize.py::deidentify`.

## Useful context for picking back up

A few non-obvious things that cost time during the session:

### Azure OpenAI creds via 1Password — sequential `op read`

The `.envrc` checks `op whoami` before populating secrets via `op_read_if_available`. With 1Password desktop-app integration enabled, `op whoami` returns "account is not signed in" (the desktop bridge doesn't expose a session to the CLI) even though `op vault list` and `op read` work fine. Result: `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT` are exported as empty strings.

Workaround used throughout this session:

```bash
VAULT="a747na7sgxngfa7gczsqqwekfq"
export AZURE_OPENAI_API_KEY=$(op read "op://$VAULT/azure-openai-staging-runtime/password")
export AZURE_OPENAI_ENDPOINT=$(op read "op://$VAULT/azure-openai-staging-runtime/website")
echo "key=${#AZURE_OPENAI_API_KEY} ep=${#AZURE_OPENAI_ENDPOINT}"
```

Run the two `op read` calls **sequentially**. Running them in parallel via `$(...) $(...)` on a single line causes one of them to time out — both hit the biometric prompt simultaneously and only one wins.

Worth fixing in `.env.shared:81` long-term: change the gate from `op whoami` to `op vault list >/dev/null 2>&1` (or similar) so direnv populates secrets correctly under desktop-app integration. Tracked-but-undone.

### Conftest `load_ci_environment` timeout when running pytest manually

`tests/conftest.py:21` calls `load_ci_environment()` when `PYTHON_ENV` is not `test`. That helper spawns `just direnv_export_ci` which runs `env -i ... direnv export json` and times out at 30s if `OP_SERVICE_ACCOUNT_TOKEN` is unset (the CI auth path). To run tests locally, **set `PYTHON_ENV=test` explicitly before pytest** so the helper is skipped:

```bash
PYTHON_ENV=test uv run pytest tests/...
```

This is in the prior handoff's spec advice but worth surfacing again.

### CLI Clerk-private-key needed for admin tests

Tests under `tests/routes/admin_usage_test.py` and `tests/routes/login_as_test.py` call `get_valid_token` which calls real Clerk APIs. Without `CLERK_PRIVATE_KEY`, these tests `pytest.skip`. To run them locally, fetch the same way as Azure:

```bash
export CLERK_PRIVATE_KEY=$(op read "op://a747na7sgxngfa7gczsqqwekfq/clerk-staging-api/secret_key")
```

### OpenAPI snapshot test

`tests/unit/openapi_schema_test.py::test_openapi_schema_matches_generated_file` is a snapshot — adding/removing/changing any route changes the snapshot. Run `just js_generate-openapi` before committing route changes so `web/openapi.json` is in sync. Failed PR #66's CI on first push because of this. Worth surfacing in `CONTRIBUTING.md` or similar.

### Stacking PRs against an in-flight merge

When you stack a branch on an unmerged PR and the parent PR squash-merges to master while you're still iterating, your stacked branch will conflict with master (squash creates a new commit hash; your branch still has the originals). The clean recovery is **reset + cherry-pick** rather than rebase:

```bash
git fetch origin master
git reset --hard origin/master
git cherry-pick <your-actual-commits-only>
git push --force-with-lease
```

This happened to PRs #63 and #64 during the session. Cleaner if the parent is held until the child is also approved, but holding both gates progress, so live with the cherry-pick cost.

## Files to look at when resuming

If you're picking up the **judge-model 404 follow-up**:
- `tests/eval.py:8` — the `OPENAI_MODEL` constant to gate behind env var or replace.
- `app/configuration/openai.py` — the live-Azure client config.

If you're picking up the **N+1 backend cleanup**:
- `app/routes/internal.py` — `note_detail` and the `/settings` route. Look for repeat `useQuery` calls in `web/app/` against `/internal/v1/settings` and `/internal/v1/notes`.

If you're picking up the **Dave/Mike DeID config** question:
- `app/jobs/anonymize.py:24-57` — the existing `deidentify()` whose defaults #65 inherited.
- `infra/variables.tf:35` (line referenced in prior handoff) — credential wiring.

## Repo state at end of session

- master at `fe6c152` (PR #66) plus whatever lands from PR #67.
- One open PR: #67 (one-line integration-test toast fix, CI in flight).
- No stale branches, no uncommitted file changes (untracked prior handoff aside).
- No DB migrations pending beyond what shipped in PR #65.
- Issue #33 closed (via PR #64), #29 closed (via PR #65), #34 closed (via PR #66).
- Issue #58 still open in GitHub even though PR #59 covered it last session — worth closing manually if not auto-closed.
