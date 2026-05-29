# Auto-delete transcripts after 2 weeks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land issue #29 — after 14 days, raw transcript text gets de-identified into a new `AIVisitTranscriptNonPHI` table (no FK / no timestamps) and the original `AIVisitTranscript` row is soft-deleted with an empty `transcript_data`. A daily celery beat task drives the cleanup. The read-time hack in `internal.py` that currently zeroes `transcript_data` on read goes away.

**Architecture:** Single-PR change with five logical pieces:
1. New `AIVisitTranscriptNonPHI` model + alembic migration.
2. `TranscriptDeletionJob` (`app/jobs/anonymize.py::perform`) implemented to deidentify-per-line and persist into the new table.
3. New `app/jobs/transcript_cleanup.py` daily sweeper that queues per-transcript deletions for rows older than 14 days with `deleted_at IS NULL`.
4. Beat schedule entry in `app/celery.py` firing the sweeper at 03:00 UTC daily.
5. Remove the read-time hack at `internal.py:170-175`.

**Tech Stack:** Python 3, Alembic, SQLModel + ActiveModel, Celery + redis, Azure DeID via existing `deidentify()`. No FE changes.

**Spec:** `docs/superpowers/specs/2026-05-29-auto-delete-transcripts-design.md`

**Branch:** `feat/auto-delete-transcripts` (already created from master).

---

## Local prerequisites

- Postgres + redis dev containers up: `just db_up && just redis_up` (or whatever the project conventions are — should already be running if you've been working).
- `PYTHON_ENV=test` for tests (we use the same pattern as items 1-6).
- **Live Azure DeID is NOT required** for the unit tests in this plan; the existing live integration test `tests/job/anonymize_test.py::test_anonymize` stays gated by `RUN_LIVE_DEIDENTIFICATION_TESTS=true`.

---

## Task 1: Add `AIVisitTranscriptNonPHI` model + migration

**Files:**
- Create: `app/models/ai_visit_transcript_non_phi.py`
- Create: `migrations/versions/<auto>_add_ai_visit_transcript_non_phi.py`

- [ ] **Step 1: Create the model**

Create `app/models/ai_visit_transcript_non_phi.py`:

```python
from activemodel import BaseModel
from activemodel.mixins import PydanticJSONMixin, TypeIDMixin
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from .ai_visit_transcript import TranscriptEntry


class AIVisitTranscriptNonPHI(
    BaseModel,
    PydanticJSONMixin,
    TypeIDMixin("ai_visit_transcript_non_phi"),
    table=True,
):
    """De-identified copy of an AIVisitTranscript's transcript_data.

    Intentionally carries no foreign keys to patient/doctor/appointment
    and no timestamp columns. Both would let timing or relationship
    metadata reattach this row to the original PHI row it was derived
    from. This dataset exists to improve model outputs without
    exposing individual visits.
    """

    data: list[TranscriptEntry] = Field(sa_type=JSONB)
```

No `TimestampsMixin`, no `SoftDeletionMixin`, no FKs. Only `id` (from TypeIDMixin) and `data` (a JSONB column).

The model is picked up automatically by `app/models/__init__.py` (which scans the directory) — no manual export needed.

- [ ] **Step 2: Generate the alembic migration**

Run from the repo root:

```bash
just db_generate_migration "add ai_visit_transcript_non_phi"
```

This invokes alembic's autogenerate against the dev database. It should produce a new file in `migrations/versions/` containing a `create_table` for `ai_visit_transcript_non_phi` with two columns (`id`, `data`).

- [ ] **Step 3: Inspect the generated migration**

Open the new file under `migrations/versions/`. The `upgrade()` body should look approximately like:

```python
op.create_table(
    "ai_visit_transcript_non_phi",
    sa.Column("id", ..., primary_key=True),
    sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
)
```

If `squawk` (the migration linter) flags risky DDL on the new table — it shouldn't, since this is a CREATE TABLE on a new empty table — fix per squawk's suggestion before continuing.

- [ ] **Step 4: Apply migrations against dev + test DBs**

```bash
just db_migrate
```

Expected: alembic upgrades both dev and test, ending at the new revision. No errors.

- [ ] **Step 5: Spot-check in psql**

```bash
psql $TEST_DATABASE_URL -c "\d ai_visit_transcript_non_phi"
```

Expected: two columns (`id`, `data`), no FKs, no `created_at` / `updated_at` / `deleted_at`.

- [ ] **Step 6: Commit**

```bash
git add app/models/ai_visit_transcript_non_phi.py migrations/versions/*ai_visit_transcript_non_phi*
git commit -m "feat(model): add AIVisitTranscriptNonPHI for de-identified transcript dataset (issue #29)"
```

---

## Task 2: Implement `TranscriptDeletionJob` in `anonymize.py`

**Files:**
- Modify: `app/jobs/anonymize.py`

- [ ] **Step 1: Replace the stub `perform()` with the real implementation**

Open `app/jobs/anonymize.py`. The stub at line 17 reads:

```python
# TODO implement!
def perform(): ...
```

Replace with:

```python
from datetime import datetime, timezone

from activemodel.types import TypeIDType
from app.celery import celery_app
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.ai_visit_transcript_non_phi import AIVisitTranscriptNonPHI


def perform(transcript_id: str | TypeIDType) -> None:
    """De-identify and tombstone an AIVisitTranscript.

    Idempotent: if the transcript is already soft-deleted, returns
    without making any DB or Azure calls. We rely on this for celery
    retries and any rare double-queueing from the daily sweeper.
    """
    transcript = AIVisitTranscript.get(id=transcript_id)
    if not transcript or transcript.deleted_at:
        log.info(
            "transcript already deleted, skipping",
            transcript_id=str(transcript_id),
        )
        return

    log.info(
        "deidentifying transcript for deletion",
        transcript_id=str(transcript_id),
        entries=len(transcript.transcript_data),
    )

    deidentified_entries = [
        TranscriptEntry(
            speaker=entry.speaker,
            content=deidentify(entry.content),
            startTime=entry.startTime,
            endTime=entry.endTime,
        )
        for entry in transcript.transcript_data
    ]

    AIVisitTranscriptNonPHI(data=deidentified_entries).save()

    transcript.transcript_data = []
    transcript.deleted_at = datetime.now(timezone.utc)
    transcript.save()


perform_celery = celery_app.task(name="app.jobs.anonymize.perform")(perform)


def queue(transcript_id: str | TypeIDType) -> None:
    log.info("queuing transcript deletion", transcript_id=str(transcript_id))
    perform_celery.delay(transcript_id)
```

Keep the existing `deidentify()` function unchanged at the bottom of the file. Add the new imports at the top (do not duplicate the existing `from app import log`).

- [ ] **Step 2: Write the unit test**

Create or extend `tests/job/anonymize_test.py`. Add (alongside the existing `test_anonymize`):

```python
from datetime import datetime, timezone
from unittest.mock import patch

from app.jobs.anonymize import perform
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.ai_visit_transcript_non_phi import AIVisitTranscriptNonPHI
from app.models.appointment import Appointment
from app.models.patient import Patient


# Note: this test does NOT use the live-DeID skipif at the top of the
# file because it patches deidentify(). It runs unconditionally.
def test_perform_deidentifies_and_tombstones_transcript():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    appt = Appointment.where(Appointment.patient_id == patient.id).one()

    transcript = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=60,
        transcript_data=[
            TranscriptEntry(
                speaker="Patient",
                content="My name is John Smith and I live in Chicago.",
                startTime=0,
                endTime=5000,
            ),
            TranscriptEntry(
                speaker="Doctor",
                content="Have you been to Dr. Mozal recently?",
                startTime=5000,
                endTime=8000,
            ),
        ],
    ).save()

    initial_non_phi_count = AIVisitTranscriptNonPHI.count()

    with patch("app.jobs.anonymize.deidentify", side_effect=lambda s: f"DEID({s})"):
        perform(transcript.id)

    # New NonPHI row exists with deidentified entries.
    assert AIVisitTranscriptNonPHI.count() == initial_non_phi_count + 1
    new_non_phi = (
        AIVisitTranscriptNonPHI.select()
        .order_by(AIVisitTranscriptNonPHI.id.desc())
        .limit(1)
        .one()
    )
    assert [e.content for e in new_non_phi.data] == [
        "DEID(My name is John Smith and I live in Chicago.)",
        "DEID(Have you been to Dr. Mozal recently?)",
    ]
    # speaker / timing preserved
    assert [e.speaker for e in new_non_phi.data] == ["Patient", "Doctor"]

    # Original transcript: data cleared, deleted_at set.
    refreshed = AIVisitTranscript.get(id=transcript.id)
    assert refreshed is not None
    assert refreshed.transcript_data == []
    assert refreshed.deleted_at is not None


def test_perform_is_idempotent_when_already_deleted():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    appt = Appointment.where(Appointment.patient_id == patient.id).one()

    transcript = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=60,
        transcript_data=[
            TranscriptEntry(speaker="Patient", content="hi", startTime=0, endTime=1000),
        ],
        deleted_at=datetime.now(timezone.utc),
    ).save()

    initial_non_phi_count = AIVisitTranscriptNonPHI.count()

    with patch("app.jobs.anonymize.deidentify") as mock_deid:
        perform(transcript.id)
        mock_deid.assert_not_called()

    # No new NonPHI row.
    assert AIVisitTranscriptNonPHI.count() == initial_non_phi_count
```

The exact import path for `Patient` / `Appointment` matches existing tests (`tests/job/summarize_test.py`).

If `AIVisitTranscriptNonPHI.count()` isn't a method on the activemodel base class, use `AIVisitTranscriptNonPHI.select().count()` or `len(AIVisitTranscriptNonPHI.select().all())` instead. Check existing tests for the pattern (`tests/job/summarize_test.py:count_visit_notes` uses `AIVisitNote.where(...).count()`).

- [ ] **Step 3: Run the unit tests**

```bash
PYTHON_ENV=test uv run pytest tests/job/anonymize_test.py -v
```

Expected: 2 new tests pass (`test_perform_deidentifies_and_tombstones_transcript`, `test_perform_is_idempotent_when_already_deleted`). The existing `test_anonymize` remains SKIPPED unless `RUN_LIVE_DEIDENTIFICATION_TESTS=true` and Azure creds are set.

- [ ] **Step 4: Commit**

```bash
git add app/jobs/anonymize.py tests/job/anonymize_test.py
git commit -m "feat(jobs): implement TranscriptDeletionJob — deidentify, persist NonPHI, tombstone (issue #29)"
```

---

## Task 3: Daily sweeper + beat schedule

**Files:**
- Create: `app/jobs/transcript_cleanup.py`
- Modify: `app/celery.py` (beat_schedule)
- Create: `tests/job/transcript_cleanup_test.py`

- [ ] **Step 1: Create the sweeper**

Create `app/jobs/transcript_cleanup.py`:

```python
"""Daily sweep that queues TranscriptDeletionJob for any
AIVisitTranscript older than CLEANUP_AGE_DAYS with deleted_at IS NULL.

Owned by issue #29. The 14-day cutoff is the boss's decision; not
HIPAA-mandated. The cutoff lives here as a module-level constant
rather than in settings because changing it requires reasoning about
the de-identified dataset's coverage (see spec).
"""

from datetime import datetime, timedelta, timezone

from app import log
from app.celery import celery_app
from app.jobs.anonymize import queue as queue_deletion
from app.models.ai_visit_transcript import AIVisitTranscript


CLEANUP_AGE_DAYS = 14


def perform() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=CLEANUP_AGE_DAYS)
    candidates = list(
        AIVisitTranscript.where(
            (AIVisitTranscript.created_at < cutoff)
            & (AIVisitTranscript.deleted_at == None)  # noqa: E711
        ).all()
    )

    log.info("queuing transcript deletion job", count=len(candidates), cutoff=cutoff.isoformat())

    for transcript in candidates:
        queue_deletion(transcript.id)


perform_celery = celery_app.task(name="app.jobs.transcript_cleanup.perform")(perform)
```

- [ ] **Step 2: Add to the beat schedule**

Open `app/celery.py`. Find `celery_app.conf.beat_schedule = { ... }` around line 185. Add a new entry alongside the existing ones:

```python
    # Daily sweep that finds AIVisitTranscripts older than 14 days
    # with no soft-delete marker and queues per-transcript
    # de-identification + tombstoning (issue #29). 03:00 UTC sits
    # outside US clinic hours.
    "transcript_cleanup": {
        "task": "app.jobs.transcript_cleanup.perform",
        "schedule": crontab(minute="0", hour="3"),
    },
```

Make sure `crontab` is already imported at the top of the file (it is — used by `sync_clerk` and `synthetic_zoom_check`).

- [ ] **Step 3: Write the unit test**

Create `tests/job/transcript_cleanup_test.py`:

```python
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.jobs.transcript_cleanup import CLEANUP_AGE_DAYS, perform
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.patient import Patient


def _make_transcript(appt, created_offset_days: int) -> AIVisitTranscript:
    t = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=appt.patient_id,
        duration=60,
        transcript_data=[
            TranscriptEntry(speaker="Patient", content="hi", startTime=0, endTime=1000)
        ],
    ).save()
    # Overwrite created_at; TimestampsMixin sets it to now() on save.
    cutoff_offset = datetime.now(timezone.utc) - timedelta(days=created_offset_days)
    t.created_at = cutoff_offset
    t.save()
    return t


def test_sweeper_queues_only_transcripts_older_than_cutoff():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    appt = Appointment.where(Appointment.patient_id == patient.id).one()

    old = _make_transcript(appt, created_offset_days=CLEANUP_AGE_DAYS + 1)
    young = _make_transcript(appt, created_offset_days=1)

    with patch("app.jobs.transcript_cleanup.queue_deletion") as q:
        perform()

    queued_ids = {call.args[0] for call in q.call_args_list}
    assert old.id in queued_ids
    assert young.id not in queued_ids


def test_sweeper_skips_already_deleted_transcripts():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    appt = Appointment.where(Appointment.patient_id == patient.id).one()

    already_deleted = _make_transcript(appt, created_offset_days=CLEANUP_AGE_DAYS + 1)
    already_deleted.deleted_at = datetime.now(timezone.utc)
    already_deleted.save()

    with patch("app.jobs.transcript_cleanup.queue_deletion") as q:
        perform()

    queued_ids = {call.args[0] for call in q.call_args_list}
    assert already_deleted.id not in queued_ids
```

Caveat: assigning `created_at` directly relies on `TimestampsMixin` not overwriting it on `save()` after the initial insert. If activemodel auto-updates `updated_at` but leaves `created_at` alone (the common case for `created_at` mixins), this works. If it overwrites, use a raw SQL update via `Session.execute(text(...))` to set `created_at`. Verify with one test run.

- [ ] **Step 4: Run the unit tests**

```bash
PYTHON_ENV=test uv run pytest tests/job/transcript_cleanup_test.py -v
```

Expected: 2 tests pass.

If they fail because seeded transcripts for Samwise/Ocie/etc already match the >14d cutoff (because seed data was loaded long ago), the assertions on "young" vs "old" presence may be off. Add explicit filtering or pick a different patient seed that the existing test database doesn't already populate with transcripts.

- [ ] **Step 5: Commit**

```bash
git add app/jobs/transcript_cleanup.py app/celery.py tests/job/transcript_cleanup_test.py
git commit -m "feat(jobs): daily transcript-cleanup sweeper at 03:00 UTC (issue #29)"
```

---

## Task 4: Remove the read-time hack

**Files:**
- Modify: `app/routes/internal.py`

- [ ] **Step 1: Remove the hack**

Open `app/routes/internal.py`. Find lines 167-176 (the block that starts with `if transcript and transcript.created_at:`):

```python
    if transcript and transcript.created_at:
        tz_now = datetime.now(transcript.created_at.tzinfo)
        created_gte_two_weeks_ago = transcript.created_at < tz_now - timedelta(days=14)
        if transcript.deleted_at or created_gte_two_weeks_ago:
            # Clear out transcript entries if it was "deleted" or more than 2 weeks old.
            # TODO This is a quick hack until we set up a job to auto-delete after 2 weeks.
            # https://github.com/CosmaNeura/cosmaneura/issues/29
            transcript.deleted_at = transcript.created_at + timedelta(days=14)
            transcript.transcript_data = []
```

Delete the block. The `NoteDetail` constructor that follows it receives the transcript as-is. For transcripts that have been processed by the deletion job, `transcript_data` is already `[]` and `deleted_at` is set — same observable state.

Check whether `datetime`/`timedelta` are still imported elsewhere in `internal.py` after the removal. If they were only used by the hack, drop the now-unused imports.

- [ ] **Step 2: Run the existing tests for the internal routes**

```bash
PYTHON_ENV=test uv run pytest tests/routes/ -v -k internal 2>&1 | tail -30
```

Expected: any internal-routes tests that touched the deleted block still pass (they should — the hack was an internal mutation that the tests didn't directly assert on). If a test fails, look at what it expected and decide whether the test needs updating or the removal needs adjusting.

- [ ] **Step 3: Commit**

```bash
git add app/routes/internal.py
git commit -m "refactor(internal): drop read-time transcript-clear hack now that auto-delete is wired (issue #29)"
```

---

## Task 5: Lint, push, open PR

**Files:**
- No source changes.

- [ ] **Step 1: Run the linters**

```bash
just py_lint
```

Expected: clean (ruff, pyright, j2lint, djlint all green). If ruff flags formatting, run `just py_lint_fix` and follow up with a `chore: format` commit.

```bash
just db_lint
```

Expected: clean. squawk runs against new migrations (scoped to migrations added vs `origin/master` per PR #57). It should be quiet because our migration is a CREATE TABLE with no FKs / NOT NULL backfill problems.

- [ ] **Step 2: Run the relevant pytest suite to confirm no regression**

```bash
PYTHON_ENV=test uv run pytest tests/job/ -v 2>&1 | tail -20
```

Expected: all job tests pass (existing + new). The summarize live-LLM tests stay SKIPPED unless `RUN_LIVE_LLM_TESTS=true`.

- [ ] **Step 3: Push**

```bash
git push -u origin feat/auto-delete-transcripts
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat: auto-delete transcripts after 14 days with de-identified dataset (issue #29)" --body "$(cat <<'EOF'
## Summary
- Closes #29. After 14 days, raw \`AIVisitTranscript.transcript_data\` is de-identified into a new \`AIVisitTranscriptNonPHI\` table (no FK, no timestamps) and the original row is soft-deleted with an empty \`transcript_data\`.
- A daily celery beat task at 03:00 UTC (\`app/jobs/transcript_cleanup.perform\`) queries transcripts older than 14 days with \`deleted_at IS NULL\` and enqueues \`TranscriptDeletionJob\` (\`app/jobs/anonymize.perform\`) for each.
- Read-time hack at \`app/routes/internal.py:170-175\` removed -- the deletion job already clears \`transcript_data\` on the eligible rows, so the hack's runtime mutation is obsolete.
- Uses the existing \`deidentify()\` (Azure DeID, Surrogate operation, API version 2024-11-15) -- same code path that powers \`extract.py\`'s example-note de-identification today.

## Files
- New: \`app/models/ai_visit_transcript_non_phi.py\`, \`app/jobs/transcript_cleanup.py\`, migration.
- Modified: \`app/jobs/anonymize.py\` (real \`perform\`), \`app/celery.py\` (beat entry), \`app/routes/internal.py\` (hack removal).
- Tests: \`tests/job/anonymize_test.py\` (2 new unit tests with mocked DeID), \`tests/job/transcript_cleanup_test.py\` (2 new unit tests).

## Test plan
- [ ] CI green on Backend (unit tests run without live Azure).
- [ ] On staging: run \`celery beat\` for ~24 hours, check that the daily sweep fires and per-transcript jobs complete via Sentry / celery logs.
- [ ] Spot-check: after one daily run on staging, query \`select count(*) from ai_visit_transcript_non_phi\` and verify it's non-zero, and that \`ai_visit_transcript where deleted_at is null and created_at < now() - interval '14 days'\` returns zero.
- [ ] Verify the read endpoint at \`/internal/notes/<appointment_id>\` returns the same \`NoteDetail\` shape for old transcripts as before (transcript present, \`transcript_data\` empty for old ones).

## Open question carried from #29 prep
Per session handoff: "is there anything Dave or Mike configured on the Clerk-side that we need to preserve when wiring up the transcript flow (specific PHI categories, locale, etc.)?" — proceeding with the existing \`deidentify()\` defaults (Surrogate, API 2024-11-15) which already power \`extract.py\` for example notes. If Dave/Mike has specific PHI category overrides, we wire them in a follow-up.

## Spec & plan
- Spec: \`docs/superpowers/specs/2026-05-29-auto-delete-transcripts-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-29-auto-delete-transcripts.md\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Surface URL and CI status**

Watch CI; report URL + conclusion.

---

## Self-review notes

- **Spec coverage:** model + migration (Task 1), job (Task 2), sweeper + scheduler (Task 3), hack removal (Task 4), lint + push + PR (Task 5). All four spec deliverables covered.
- **TimestampsMixin is NOT applied to the NonPHI model.** Verified in Task 1 model file.
- **Idempotency:** the job's early-return on `deleted_at` was tested explicitly in Task 2. The sweeper's `deleted_at IS NULL` filter avoids double-queueing during a retry.
- **Beat 03:00 UTC** picked to avoid US clinic hours (matches existing `sync_clerk` 00:00 UTC and `synthetic_zoom_check` 15min cadence pattern of doing infra work off-hours).
- **No backfill needed.** First run of the sweeper picks up all already-stale transcripts.
- **squawk migration check:** the new migration is a CREATE TABLE on a brand-new table — no NOT NULL backfill, no index on a hot column, no concurrent index. Should sail through.
