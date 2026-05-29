# Design — Auto-delete transcripts after 2 weeks

**Issue:** #29
**Date:** 2026-05-29
**Author:** Oscar (with Claude)

## Background

The boss confirmed priority #33 → #29 → #34. With #33 closed (PRs #60–64), #29 is next. The goal: stop holding raw, PHI-containing transcript text on disk indefinitely. After 14 days the raw `AIVisitTranscript.transcript_data` is replaced with a de-identified copy in a new table that has no foreign keys back to the patient/doctor/appointment. The de-identified data remains available for training/eval.

A read-time hack at `app/routes/internal.py:170-175` currently zeroes out `transcript_data` on read when the transcript is older than 14 days, with a comment pointing at this issue. The hack must be removed once auto-deletion is in place — otherwise we'd be both hiding and clearing data, and the hack runs every read.

Per the boss: not HIPAA compliance per se, but worth retaining the de-identified dataset for output improvement work.

## What already exists

Verified during session prep:
- `app/jobs/anonymize.py::deidentify(phi_text: str) -> str` is wired against Azure DeID's `"Surrogate"` operation and already used by `extract.py:271` for example notes.
- Azure DeID credentials present (`infra/variables.tf`).
- A live integration test at `tests/job/anonymize_test.py` gated by `RUN_LIVE_DEIDENTIFICATION_TESTS=true`.
- `app/jobs/anonymize.py:18` has `def perform(): ...` stub with `# TODO implement!` — the placeholder for `TranscriptDeletionJob`.
- `app/celery.py:185-207` has the `beat_schedule` config with existing entries (sync_clerk, heartbeat, synthetic_zoom_check) — natural place to add a daily entry.

## What's missing (the work)

1. **Model `AIVisitTranscriptNonPHI`** — does not exist.
2. **Job implementation** — `anonymize.py:perform()` is a stub.
3. **Beat schedule entry** — no daily transcript-cleanup task.
4. **Hack removal** at `internal.py:170-175`.

## Constraints

- **Open question pending:** the handoff flagged "is there anything Dave/Mike configured on the Clerk-side that we need to preserve when wiring up the transcript flow (specific PHI categories, locale, etc.)?" The boss has decided to **proceed with the existing `deidentify()` defaults** (Surrogate operation, API version 2024-11-15) which already power `extract.py` for example notes. If a Dave/Mike config surfaces later, adjust in follow-up.
- **No timestamps on NonPHI.** Per the issue body, the NonPHI model "probably should not have `created_at` timestamps, as they will closely match the `deleted_at` timestamps on the AIVisitTranscript". A close-by `created_at` would let an attacker correlate a NonPHI row with the deleted PHI row by timing — defeating de-identification. Skip `TimestampsMixin`.
- **No foreign keys on NonPHI.** Per the issue body, the NonPHI model "should NOT have any attributes that can tie it to the patient/doctor/appointment". Just an `id` and `data`.
- **De-identification is per-line.** `deidentify()` takes a single string and returns a string. We de-identify each `TranscriptEntry.content` (keeping `speaker`, `startTime`, `endTime` — none of those are PHI). If a `content` is empty, `deidentify()` short-circuits returning the input.
- **Live Azure DeID calls in the deletion job.** The unit test mocks `deidentify` so it doesn't hit Azure; a live integration test (gated by `RUN_LIVE_DEIDENTIFICATION_TESTS=true`) can exercise the full path against real Azure if needed — out of scope here, but the existing `tests/job/anonymize_test.py` precedent is there.

## Approach — single PR

One PR with five logical pieces:

### 1. `AIVisitTranscriptNonPHI` model + migration

New file `app/models/ai_visit_transcript_non_phi.py`:

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
    and no timestamp columns — both would let timing/relationship
    metadata reattach this row to the original PHI row it was derived
    from. This dataset exists to improve model outputs without exposing
    individual visits.
    """

    data: list[TranscriptEntry] = Field(sa_type=JSONB)
```

Alembic migration creates `ai_visit_transcript_non_phi` table with just `id` (TypeID PK) and `data` (JSONB). No FKs, no timestamps. Imported in `app/models/__init__.py`.

### 2. Re-export through `app/models/__init__.py`

So the celery task can `from app.models.ai_visit_transcript_non_phi import AIVisitTranscriptNonPHI` and so the model is registered with SQLModel metadata at app boot.

### 3. `TranscriptDeletionJob` — implement `anonymize.py:perform()`

```python
def perform(transcript_id: str | TypeIDType) -> None:
    """De-identify and tombstone an AIVisitTranscript.

    1. Load the AIVisitTranscript (returns if already soft-deleted).
    2. De-identify each TranscriptEntry's content via Azure DeID.
    3. Persist the de-identified entries as a new
       AIVisitTranscriptNonPHI row (no FK, no timestamps).
    4. Clear AIVisitTranscript.transcript_data and set deleted_at,
       leaving the row in place so /internal endpoints continue to
       reference it by appointment_id.

    Idempotent: if deleted_at is already set, the function returns
    without making any DB or Azure calls.
    """

    transcript = AIVisitTranscript.get(id=transcript_id)
    if not transcript or transcript.deleted_at:
        log.info("transcript already deleted, skipping", transcript_id=str(transcript_id))
        return

    log.info("deidentifying transcript for deletion", transcript_id=str(transcript_id))

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


def queue(transcript_id):
    log.info("queuing transcript deletion", transcript_id=str(transcript_id))
    perform_celery.delay(transcript_id)
```

### 4. Daily beat schedule

New job file `app/jobs/transcript_cleanup.py` that finds candidates and queues per-transcript deletion:

```python
from datetime import datetime, timedelta, timezone

from app import log
from app.celery import celery_app
from app.jobs.anonymize import queue as queue_deletion
from app.models.ai_visit_transcript import AIVisitTranscript


CLEANUP_AGE_DAYS = 14


def perform() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=CLEANUP_AGE_DAYS)
    candidates = AIVisitTranscript.where(
        (AIVisitTranscript.created_at < cutoff)
        & (AIVisitTranscript.deleted_at == None)  # noqa: E711
    ).all()

    log.info("queuing transcript deletion job", count=len(list(candidates)))

    for transcript in candidates:
        queue_deletion(transcript.id)


perform_celery = celery_app.task(name="app.jobs.transcript_cleanup.perform")(perform)
```

Add to `celery_app.conf.beat_schedule`:

```python
"transcript_cleanup": {
    "task": "app.jobs.transcript_cleanup.perform",
    "schedule": crontab(minute="0", hour="3"),  # daily at 03:00 UTC
},
```

03:00 UTC = 23:00 EDT / 22:00 EST — clear of business hours for clinic ops.

### 5. Remove the read-time hack

Strip `internal.py:170-175`. Once the deletion job has run, `transcript_data` is already empty on rows older than 14 days, so the read endpoint observes the same empty list it would have observed via the hack — without writing to the in-memory row on every read.

The transcript stays joinable to the appointment for UI purposes; the only visible change is that the in-memory clobber goes away and stale-but-not-yet-deleted transcripts (created 14+ days ago but not processed yet because the daily job hasn't run) momentarily show their raw data on the read endpoint. The window is at most 24 hours and is acceptable per boss's "not HIPAA per se" framing.

## Tests

- **Unit test for `anonymize.perform`** (`tests/job/anonymize_test.py`): patch `deidentify` to return `"REDACTED({content})"`. Build an in-memory transcript, save it, call `perform(transcript_id)`. Assert (a) a new `AIVisitTranscriptNonPHI` exists with the redacted data, (b) original transcript's `transcript_data == []`, (c) original transcript's `deleted_at` is set, (d) calling `perform` again is a no-op (idempotency).
- **Unit test for `transcript_cleanup.perform`**: create two transcripts — one with `created_at` 15 days ago, one with `created_at` 1 day ago. Patch `queue_deletion`. Call `perform()`. Assert `queue_deletion` is called exactly once with the older transcript's id.
- **Live integration test (optional, gated)**: extend `tests/job/anonymize_test.py` to exercise the full `perform` path against real Azure DeID under the existing `RUN_LIVE_DEIDENTIFICATION_TESTS=true` gate. Defer if it complicates this PR; the existing `test_anonymize` already covers DeID itself.
- **No test for the hack removal**: it's a delete of dead code, covered indirectly by the deletion-job tests since their post-state matches what the hack used to produce.

## Out of scope

- Per-doctor opt-out of auto-deletion. Not requested.
- A retention window other than 14 days. The boss confirmed 14.
- Backfilling the new dataset from already-existing >14-day transcripts. The daily job will pick them up on first run.
- Surfacing the NonPHI dataset anywhere in the UI or API. It exists for offline output-improvement work.
- Anonymising other tables that might carry PHI (e.g. transcript-derived notes in `AIVisitNote.note`). Separate issue if needed.

## Risks and rollback

- **Risk: Azure DeID rate limits or errors during a large daily batch.** First run will process all existing >14-day transcripts at once. Mitigation: each transcript is queued as a separate celery task, so failures retry independently and the rate is governed by the worker prefork pool. If a batch saturates Azure, the failed tasks bubble through Sentry and are retried.
- **Risk: deletion job runs while a clinical user is reading the transcript page.** The job soft-deletes (sets `deleted_at` + clears `transcript_data`) — subsequent reads see empty data, which is the same outcome as the hack today. UI handles empty `transcript_data` correctly (it's been doing so via the hack).
- **Risk: incorrect cutoff bypasses recent transcripts.** The test verifies exactly that boundary.
- **Risk: model has wrong typeid prefix that collides.** `ai_visit_transcript_non_phi` is new; verified no collision in existing models.
- **Rollback:** revert the PR. The NonPHI table sits unused; no FK constraints elsewhere reference it. Bring the hack back if necessary as part of the revert (it's a small chunk).

## Affected files

- `app/models/ai_visit_transcript_non_phi.py` — NEW.
- `app/models/__init__.py` — export new model.
- `migrations/versions/<new>.py` — NEW migration creating the table.
- `app/jobs/anonymize.py` — implement `perform`, add `queue`, wire celery task.
- `app/jobs/transcript_cleanup.py` — NEW (or add to existing module — see plan).
- `app/celery.py` — add `transcript_cleanup` beat entry.
- `app/routes/internal.py` — remove the hack at lines 170-175.
- `tests/job/anonymize_test.py` — add unit tests; keep existing live test.
- `tests/job/transcript_cleanup_test.py` — NEW unit test for the scheduler/query.
