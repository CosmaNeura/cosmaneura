# Zoom Meeting Schedule Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Schedule page to the Cosma web app that syncs and displays a doctor's Zoom meetings with join, cancel, and post-meeting notes access.

**Architecture:** A new `ZoomMeeting` DB model stores meetings synced from the Zoom API. A Celery task (`sync_zoom_meetings`) is queued on page load to refresh the DB. Backend endpoints at `/internal/v1/schedule` return the meeting list and handle cancellations. The frontend reuses the existing sidebar+detail layout pattern from the Notes page.

**Tech Stack:** Python/FastAPI, SQLModel/activemodel, Celery, Alembic; React Router v7, TypeScript, Tailwind, shadcn/ui, `@hey-api/openapi-ts` for the generated API client.

**Spec:** `docs/superpowers/specs/2026-04-06-zoom-schedule-design.md`

---

## File Map

**Create:**
- `app/models/zoom_meeting.py` — ZoomMeeting SQLModel table + ZoomMeetingStatus enum
- `app/jobs/sync_zoom_meetings.py` — Celery task: fetch Zoom API + upsert ZoomMeeting rows
- `migrations/versions/YYYYMMDD_<hash>_add_zoom_meeting_table.py` — Alembic migration
- `tests/routes/schedule_test.py` — route tests for GET/DELETE /schedule
- `tests/job/sync_zoom_meetings_test.py` — unit tests for sync job
- `web/app/layouts/schedule.tsx` — sidebar shell for schedule routes
- `web/app/routes/schedule/index.tsx` — meeting list with Today/Upcoming/Past sections
- `web/app/routes/schedule/detail.tsx` — meeting detail panel

**Modify:**
- `app/services/zoom.py` — add `get_user_meetings()` and `delete_meeting()`
- `app/routes/internal.py` — add `GET /schedule` and `DELETE /schedule/{zoom_meeting_id}`
- `app/jobs/zoom_webhooks.py` — update `handle_meeting_started` and `handle_meeting_ended` to update ZoomMeeting
- `web/app/routes.ts` — add schedule prefix + layout + routes
- `web/app/components/app-sidebar.tsx` — add Schedule nav item

---

## Task 1: ZoomMeeting model

**Files:**
- Create: `app/models/zoom_meeting.py`

- [ ] **Step 1: Write the model**

```python
# app/models/zoom_meeting.py
from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

from app.models.doctor import Doctor


class ZoomMeetingStatus(str, Enum):
    scheduled = "scheduled"
    started = "started"
    ended = "ended"
    cancelled = "cancelled"


class ZoomMeeting(BaseModel, TimestampsMixin, TypeIDMixin("zmtg"), table=True):
    doctor_id: TypeIDType = Doctor.foreign_key()
    "FK to Doctor"

    zoom_meeting_id: str = Field(index=True)
    "Zoom's numeric meeting ID, stored as string"

    topic: str
    "Meeting topic/title from Zoom"

    start_time: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    "Scheduled start time, timezone-aware"

    duration: int
    "Meeting duration in minutes"

    join_url: str
    "Zoom join URL for attendees"

    status: ZoomMeetingStatus = Field(default=ZoomMeetingStatus.scheduled)
    "Lifecycle status of the meeting"

    appointment_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("appointment"), nullable=True),
    )
    "Set by webhook when meeting starts and a Cosma Appointment is created"
```

- [ ] **Step 2: Verify the model is auto-imported**

`app/models/__init__.py` auto-imports all modules in the `app/models/` folder, so no changes are needed. Confirm by checking:

```bash
grep -r "modules_in_folder" app/models/__init__.py
```

Expected output: shows the auto-import loop.

- [ ] **Step 3: Commit**

```bash
git add app/models/zoom_meeting.py
git commit -m "feat: add ZoomMeeting model"
```

---

## Task 2: DB migration

**Files:**
- Create: `migrations/versions/<hash>_add_zoom_meeting_table.py`

- [ ] **Step 1: Generate the migration**

```bash
alembic revision --autogenerate -m "add_zoom_meeting_table"
```

Expected: creates a new file in `migrations/versions/` with `upgrade()` that creates the `zoommeeting` table.

- [ ] **Step 2: Verify the generated migration**

Open the new file and confirm `upgrade()` contains:
- `op.create_table('zoommeeting', ...)` with columns: `id`, `doctor_id`, `zoom_meeting_id`, `topic`, `start_time`, `duration`, `join_url`, `status`, `appointment_id`, `created_at`, `updated_at`

- [ ] **Step 3: Run the migration**

```bash
alembic upgrade head
```

Expected: `Running upgrade ... -> <hash>, add_zoom_meeting_table`

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/
git commit -m "feat: migration for zoom_meeting table"
```

---

## Task 3: zoom_service additions

**Files:**
- Modify: `app/services/zoom.py`

- [ ] **Step 1: Add `get_user_meetings` and `delete_meeting` to zoom_service**

Append to `app/services/zoom.py`:

```python
def get_user_meetings(doctor: Doctor) -> list[dict]:
    """Fetch upcoming scheduled meetings for the authenticated doctor from Zoom API."""
    assert doctor.zoom_user_id, f"doctor {doctor.id} has no zoom_user_id"

    data = _zoom_get(
        doctor,
        f"/users/{doctor.zoom_user_id}/meetings",
        params={"type": "upcoming", "page_size": "100"},
    )

    log.info("zoom meetings fetched", doctor_id=str(doctor.id), count=len(data.get("meetings", [])))
    return data.get("meetings", [])


def delete_meeting(doctor: Doctor, zoom_meeting_id: str) -> None:
    """Cancel a Zoom meeting by ID."""
    if _is_token_expired(doctor):
        doctor = refresh_access_token(doctor)

    with httpx.Client() as client:
        response = client.delete(
            f"{ZOOM_API_BASE}/meetings/{zoom_meeting_id}",
            headers={"Authorization": f"Bearer {doctor.zoom_access_token}"},
        )

    response.raise_for_status()
    log.info("zoom meeting deleted", doctor_id=str(doctor.id), zoom_meeting_id=zoom_meeting_id)
```

- [ ] **Step 2: Commit**

```bash
git add app/services/zoom.py
git commit -m "feat: add get_user_meetings and delete_meeting to zoom_service"
```

---

## Task 4: sync_zoom_meetings Celery job

**Files:**
- Create: `app/jobs/sync_zoom_meetings.py`
- Create: `tests/job/sync_zoom_meetings_test.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/job/sync_zoom_meetings_test.py
from datetime import datetime, timezone
from unittest.mock import patch

from tests.factories import DoctorFactory
from app.jobs.sync_zoom_meetings import perform
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus


FAKE_MEETINGS = [
    {
        "id": "123456789",
        "topic": "Patient Follow-up",
        "start_time": "2026-04-07T14:00:00Z",
        "duration": 30,
        "join_url": "https://zoom.us/j/123456789",
    },
    {
        "id": "987654321",
        "topic": "New Patient Intake",
        "start_time": "2026-04-07T16:30:00Z",
        "duration": 60,
        "join_url": "https://zoom.us/j/987654321",
    },
]


def test_sync_creates_zoom_meetings(global_session):
    doctor = DoctorFactory.build(zoom_user_id="zoom_user_abc", zoom_access_token="tok").save()

    with patch("app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings", return_value=FAKE_MEETINGS):
        perform(str(doctor.id))

    meetings = list(ZoomMeeting.where(ZoomMeeting.doctor_id == doctor.id).all())
    assert len(meetings) == 2
    topics = {m.topic for m in meetings}
    assert topics == {"Patient Follow-up", "New Patient Intake"}


def test_sync_upserts_existing_meetings(global_session):
    doctor = DoctorFactory.build(zoom_user_id="zoom_user_abc", zoom_access_token="tok").save()

    # pre-create one meeting
    ZoomMeeting(
        doctor_id=doctor.id,
        zoom_meeting_id="123456789",
        topic="Old Topic",
        start_time=datetime(2026, 4, 7, 14, 0, tzinfo=timezone.utc),
        duration=30,
        join_url="https://zoom.us/j/123456789",
    ).save()

    with patch("app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings", return_value=FAKE_MEETINGS):
        perform(str(doctor.id))

    meetings = list(ZoomMeeting.where(ZoomMeeting.doctor_id == doctor.id).all())
    assert len(meetings) == 2  # not duplicated

    updated = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id="123456789")
    assert updated.topic == "Patient Follow-up"  # updated from Zoom


def test_sync_preserves_started_status(global_session):
    doctor = DoctorFactory.build(zoom_user_id="zoom_user_abc", zoom_access_token="tok").save()

    ZoomMeeting(
        doctor_id=doctor.id,
        zoom_meeting_id="123456789",
        topic="Patient Follow-up",
        start_time=datetime(2026, 4, 7, 14, 0, tzinfo=timezone.utc),
        duration=30,
        join_url="https://zoom.us/j/123456789",
        status=ZoomMeetingStatus.started,
    ).save()

    with patch("app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings", return_value=FAKE_MEETINGS):
        perform(str(doctor.id))

    meeting = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id="123456789")
    assert meeting.status == ZoomMeetingStatus.started  # not reset to scheduled


def test_sync_skips_doctor_without_zoom(global_session):
    doctor = DoctorFactory.build(zoom_user_id=None).save()

    with patch("app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings") as mock_get:
        perform(str(doctor.id))
        mock_get.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/job/sync_zoom_meetings_test.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `sync_zoom_meetings` doesn't exist yet.

- [ ] **Step 3: Implement the job**

```python
# app/jobs/sync_zoom_meetings.py
from datetime import datetime, timezone

from app import log
from app.celery import celery_app
from app.models.doctor import Doctor
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus
from app.services import zoom as zoom_service


def perform(doctor_id: str) -> None:
    """Fetch upcoming Zoom meetings for a doctor and upsert into the DB."""
    doctor = Doctor.get(id=doctor_id)

    if not doctor or not doctor.zoom_user_id:
        log.info("skipping zoom sync: no doctor or zoom_user_id", doctor_id=doctor_id)
        return

    meetings = zoom_service.get_user_meetings(doctor)

    for m in meetings:
        zoom_meeting_id = str(m["id"])
        start_time = m["start_time"]
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))

        existing = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=zoom_meeting_id)

        if existing:
            if existing.status in (ZoomMeetingStatus.started, ZoomMeetingStatus.ended):
                # Don't reset status for active/completed meetings; only refresh mutable fields
                existing.topic = m["topic"]
                existing.join_url = m["join_url"]
            else:
                existing.topic = m["topic"]
                existing.start_time = start_time
                existing.duration = m["duration"]
                existing.join_url = m["join_url"]
            existing.save()
        else:
            ZoomMeeting(
                doctor_id=doctor.id,
                zoom_meeting_id=zoom_meeting_id,
                topic=m["topic"],
                start_time=start_time,
                duration=m["duration"],
                join_url=m["join_url"],
            ).save()

    log.info("zoom meetings synced", doctor_id=doctor_id, count=len(meetings))


perform_celery = celery_app.task(perform)


def queue(doctor_id: str) -> None:
    "always use this method to queue the sync job"
    perform_celery.delay(doctor_id)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/job/sync_zoom_meetings_test.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/jobs/sync_zoom_meetings.py tests/job/sync_zoom_meetings_test.py
git commit -m "feat: add sync_zoom_meetings Celery job"
```

---

## Task 5: Backend API endpoints

**Files:**
- Modify: `app/routes/internal.py`
- Create: `tests/routes/schedule_test.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/routes/schedule_test.py
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app
from app.models.doctor import Doctor
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus
from tests.factories import DoctorFactory
from tests.routes.utils import get_clerk_dev_user


def _make_meeting(doctor: Doctor, zoom_meeting_id: str = "111222333", **kwargs) -> ZoomMeeting:
    defaults = dict(
        doctor_id=doctor.id,
        zoom_meeting_id=zoom_meeting_id,
        topic="Test Meeting",
        start_time=datetime.now(timezone.utc) + timedelta(hours=1),
        duration=30,
        join_url="https://zoom.us/j/111222333",
    )
    defaults.update(kwargs)
    return ZoomMeeting(**defaults).save()


def test_schedule_list_no_zoom(authenticated_client: TestClient):
    """When doctor has no zoom_user_id, zoom_connected=false is returned."""
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    doctor.zoom_user_id = None
    doctor.save()

    with patch("app.routes.internal.sync_zoom_meetings") as mock_sync:
        response = authenticated_client.get(api_app.url_path_for("schedule_list"))

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["zoom_connected"] is False
    assert data["meetings"] == []
    mock_sync.queue.assert_not_called()


def test_schedule_list_with_zoom(authenticated_client: TestClient):
    """Returns meetings from DB and queues a background sync."""
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    doctor.zoom_user_id = "zoom_abc"
    doctor.zoom_access_token = "tok"
    doctor.save()

    meeting = _make_meeting(doctor)

    with patch("app.routes.internal.sync_zoom_meetings") as mock_sync:
        response = authenticated_client.get(api_app.url_path_for("schedule_list"))

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["zoom_connected"] is True
    assert len(data["meetings"]) == 1
    assert data["meetings"][0]["zoom_meeting_id"] == meeting.zoom_meeting_id
    mock_sync.queue.assert_called_once_with(str(doctor.id))


def test_schedule_list_excludes_cancelled(authenticated_client: TestClient):
    """Cancelled meetings are excluded from the response."""
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    doctor.zoom_user_id = "zoom_abc"
    doctor.save()

    _make_meeting(doctor, zoom_meeting_id="aaa", status=ZoomMeetingStatus.scheduled)
    _make_meeting(doctor, zoom_meeting_id="bbb", status=ZoomMeetingStatus.cancelled)

    with patch("app.routes.internal.sync_zoom_meetings"):
        response = authenticated_client.get(api_app.url_path_for("schedule_list"))

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["meetings"]) == 1
    assert data["meetings"][0]["zoom_meeting_id"] == "aaa"


def test_schedule_cancel(authenticated_client: TestClient):
    """Cancels a meeting via Zoom API and sets status=cancelled in DB."""
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    doctor.zoom_user_id = "zoom_abc"
    doctor.zoom_access_token = "tok"
    doctor.save()

    meeting = _make_meeting(doctor)

    with patch("app.routes.internal.zoom_service.delete_meeting") as mock_delete:
        response = authenticated_client.delete(
            api_app.url_path_for("cancel_meeting", zoom_meeting_id=meeting.zoom_meeting_id)
        )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_delete.assert_called_once()

    updated = ZoomMeeting.get(id=meeting.id)
    assert updated.status == ZoomMeetingStatus.cancelled


def test_schedule_cancel_not_found(authenticated_client: TestClient):
    """Returns 404 if the meeting doesn't exist or belongs to another doctor."""
    response = authenticated_client.delete(
        api_app.url_path_for("cancel_meeting", zoom_meeting_id="nonexistent")
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/routes/schedule_test.py -v
```

Expected: all 5 tests FAIL — endpoints don't exist yet.

- [ ] **Step 3: Add endpoints to internal.py**

Add these imports at the top of `app/routes/internal.py` (after existing imports):

```python
import app.jobs.sync_zoom_meetings as sync_zoom_meetings
from app.services import zoom as zoom_service
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus
```

Then append these new endpoints at the bottom of `app/routes/internal.py`:

```python
class ZoomMeetingResponse(BaseModel, extra="forbid"):
    id: str
    zoom_meeting_id: str
    topic: str
    start_time: datetime
    duration: int
    join_url: str
    status: ZoomMeetingStatus
    appointment_id: str | None


class ScheduleResponse(BaseModel, extra="forbid"):
    zoom_connected: bool
    meetings: list[ZoomMeetingResponse]


@internal_api_app.get("/schedule")
def schedule_list(request: Request) -> ScheduleResponse:
    """
    Returns all non-cancelled meetings within a 30-day past / 90-day future window.
    Queues a background Zoom sync for the authenticated doctor if Zoom is connected.
    """
    from datetime import timezone

    doctor: Doctor = request.state.doctor

    if not doctor.zoom_user_id:
        return ScheduleResponse(zoom_connected=False, meetings=[])

    sync_zoom_meetings.queue(str(doctor.id))

    window_start = datetime.now(timezone.utc) - timedelta(days=30)
    window_end = datetime.now(timezone.utc) + timedelta(days=90)

    raw = (
        ZoomMeeting.where(
            (ZoomMeeting.doctor_id == doctor.id)
            & (cast(Any, ZoomMeeting.status) != ZoomMeetingStatus.cancelled)
            & (cast(Any, ZoomMeeting.start_time) >= window_start)
            & (cast(Any, ZoomMeeting.start_time) <= window_end)
        )
        .order_by(cast(Any, ZoomMeeting.start_time).asc())
        .all()
    )

    meetings = [
        ZoomMeetingResponse(
            id=str(m.id),
            zoom_meeting_id=m.zoom_meeting_id,
            topic=m.topic,
            start_time=m.start_time,
            duration=m.duration,
            join_url=m.join_url,
            status=m.status,
            appointment_id=str(m.appointment_id) if m.appointment_id else None,
        )
        for m in raw
    ]

    return ScheduleResponse(zoom_connected=True, meetings=cast(list[ZoomMeetingResponse], meetings))


@internal_api_app.delete("/schedule/{zoom_meeting_id}", status_code=204)
def cancel_meeting(request: Request, zoom_meeting_id: str) -> None:
    """Cancel a Zoom meeting: calls Zoom API then marks the local record as cancelled."""
    doctor: Doctor = request.state.doctor

    meeting = cast(
        ZoomMeeting | None,
        ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=zoom_meeting_id),
    )

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    zoom_service.delete_meeting(doctor, zoom_meeting_id)

    meeting.status = ZoomMeetingStatus.cancelled
    meeting.save()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/routes/schedule_test.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/internal.py tests/routes/schedule_test.py
git commit -m "feat: add GET /schedule and DELETE /schedule/{zoom_meeting_id} endpoints"
```

---

## Task 6: Update zoom_webhooks to link ZoomMeeting ↔ Appointment

**Files:**
- Modify: `app/jobs/zoom_webhooks.py`

- [ ] **Step 1: Add import at the top of zoom_webhooks.py**

After the existing imports at the top of `app/jobs/zoom_webhooks.py`, add:

```python
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus
```

- [ ] **Step 2: Update handle_meeting_started to set appointment_id + status on ZoomMeeting**

In `handle_meeting_started`, after the line `log.info("recall bot created, doctor identified", ...)`, add:

```python
    # Link the ZoomMeeting record (if it was pre-synced) to the Appointment
    zoom_mtg = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=str(meeting_id))
    if zoom_mtg:
        zoom_mtg.status = ZoomMeetingStatus.started
        if appointment:
            zoom_mtg.appointment_id = appointment.id
        zoom_mtg.save()
        log.info("zoom meeting status updated to started", zoom_meeting_id=str(meeting_id))
```

Note: `appointment` may not be in scope at that point in `handle_meeting_started` — the appointment is created later in `handle_participant_joined`. Update the call site in `handle_participant_joined` instead:

After the line `redis.set(f"recall:bot:{bot_id}:patient_id", str(appointment.patient_id), ex=ttl)`, add:

```python
    # Link ZoomMeeting to Appointment if record exists
    host_id = _rget(redis, f"recall:meeting:{meeting_id}:host_id")
    if host_id:
        candidate_doctor = Doctor.where(Doctor.zoom_user_id == host_id).first()
        if candidate_doctor:
            zoom_mtg = ZoomMeeting.get(
                doctor_id=candidate_doctor.id, zoom_meeting_id=str(meeting_id)
            )
            if zoom_mtg:
                zoom_mtg.appointment_id = appointment.id
                zoom_mtg.status = ZoomMeetingStatus.started
                zoom_mtg.save()
```

And in `handle_meeting_started`, after the bot is created, add the status update only (no appointment yet):

After `log.info("recall bot created, doctor identified", bot_id=bot_id, doctor_name=doctor_name)`, add:

```python
    zoom_mtg = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=str(meeting_id))
    if zoom_mtg and zoom_mtg.status == ZoomMeetingStatus.scheduled:
        zoom_mtg.status = ZoomMeetingStatus.started
        zoom_mtg.save()
```

- [ ] **Step 3: Update handle_meeting_ended to set status=ended**

In `handle_meeting_ended`, after `_clear_meeting_state(redis, meeting_id)`, add:

```python
    # Zoom meeting IDs are globally unique; find the record and mark it ended
    zoom_mtg = ZoomMeeting.where(ZoomMeeting.zoom_meeting_id == str(meeting_id)).first()
    if zoom_mtg:
        zoom_mtg.status = ZoomMeetingStatus.ended
        zoom_mtg.save()
        log.info("zoom meeting status updated to ended", zoom_meeting_id=str(meeting_id))
```

- [ ] **Step 4: Run existing webhook tests to confirm no regressions**

```bash
pytest tests/ -k "zoom" -v
```

Expected: all existing zoom-related tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add app/jobs/zoom_webhooks.py
git commit -m "feat: update zoom webhooks to track ZoomMeeting status and appointment link"
```

---

## Task 7: Regenerate frontend API client

**Files:**
- Modify: `web/client/sdk.gen.ts`, `web/client/types.gen.ts` (auto-generated)

The frontend client is generated from the backend's OpenAPI schema. After adding the backend endpoints, regenerate it.

- [ ] **Step 1: Generate the OpenAPI schema from the backend**

```bash
python -c "import json; from app.server import api_app; json.dump(api_app.openapi(), open('web/openapi.json', 'w'))"
```

Expected: `web/openapi.json` is updated with `/internal/v1/schedule` GET and DELETE paths.

- [ ] **Step 2: Run the openapi generator**

```bash
source .env.shared && cd web && pnpm run openapi
```

Expected: `web/client/sdk.gen.ts` now exports `scheduleList` and `cancelMeeting` functions. `web/client/types.gen.ts` exports `ScheduleResponse` and `ZoomMeetingResponse` types.

Verify:

```bash
grep "scheduleList\|cancelMeeting\|ScheduleResponse\|ZoomMeetingResponse" web/client/sdk.gen.ts web/client/types.gen.ts
```

- [ ] **Step 3: Commit**

```bash
git add web/client/
git commit -m "chore: regenerate frontend client with schedule endpoints"
```

---

## Task 8: Add Schedule to nav + route config

**Files:**
- Modify: `web/app/components/app-sidebar.tsx`
- Modify: `web/app/routes.ts`

- [ ] **Step 1: Add Schedule to NAVIGATION_ITEMS in app-sidebar.tsx**

In `web/app/components/app-sidebar.tsx`, add `Calendar` to the lucide-react import:

```ts
import { Calendar, Command, File, Inbox, Settings } from "lucide-react"
```

Then add the Schedule entry to `NAVIGATION_ITEMS` (between Notetaking and Settings):

```ts
const NAVIGATION_ITEMS = [
  {
    title: "Intake",
    icon: Inbox,
    url: href("/intake"),
  },
  {
    title: "Notetaking",
    icon: File,
    url: href("/notes"),
  },
  {
    title: "Schedule",
    icon: Calendar,
    url: href("/schedule"),
  },
  {
    title: "Settings",
    icon: Settings,
    url: href("/settings"),
  },
]
```

- [ ] **Step 2: Add schedule routes to routes.ts**

In `web/app/routes.ts`, add the schedule prefix inside the authenticated layout block, after the notes prefix:

```ts
import type { RouteConfig } from "@react-router/dev/routes"
import { index, layout, prefix, route } from "@react-router/dev/routes"

export default [
  index("routes/index.tsx"),
  layout("layouts/authenticated.tsx", [
    ...prefix("intake", [
      layout("layouts/intake.tsx", [
        index("routes/intake/index.tsx"),
        route("/:id", "routes/intake/detail.tsx"),
      ]),
    ]),
    ...prefix("notes", [
      layout("layouts/notes.tsx", [
        index("routes/notes/index.tsx"),
        route(":id", "routes/notes/detail.tsx"),
      ]),
    ]),
    ...prefix("schedule", [
      layout("layouts/schedule.tsx", [
        index("routes/schedule/index.tsx"),
        route(":zoom_meeting_id", "routes/schedule/detail.tsx"),
      ]),
    ]),
    route("/settings", "routes/settings.tsx"),
  ]),
  route("chat/intake/:id", "routes/chat/intake.tsx"),
] satisfies RouteConfig
```

- [ ] **Step 3: Run typegen to verify routes are valid**

```bash
cd web && pnpm run typegen:watch &
```

Or run once:

```bash
cd web && npx react-router typegen
```

Expected: generates `web/app/layouts/+types/schedule.ts` and `web/app/routes/schedule/+types/index.ts` etc. without errors.

- [ ] **Step 4: Commit**

```bash
git add web/app/components/app-sidebar.tsx web/app/routes.ts
git commit -m "feat: add Schedule nav item and route config"
```

---

## Task 9: Schedule layout

**Files:**
- Create: `web/app/layouts/schedule.tsx`

- [ ] **Step 1: Create the layout**

The schedule layout follows the exact same pattern as `web/app/layouts/notes.tsx` — loads the meeting list, passes it as sidebar entries, and renders the `<Outlet />` for detail.

```tsx
// web/app/layouts/schedule.tsx
import { Outlet, href } from "react-router"
import { invariant } from "@epic-web/invariant"

import { Route } from "./+types/schedule"

import { AppSidebar } from "@/components/app-sidebar"
import { SidebarProvider } from "@/components/ui/sidebar"
import { scheduleList } from "~/configuration/client"
import type { ZoomMeetingResponse } from "~/configuration/client"

export async function clientLoader(_args: Route.ClientLoaderArgs) {
  const { data } = await scheduleList()
  invariant(data, "schedule data failed to load")
  return data
}

function meetingsToSidebarEntries(meetings: ZoomMeetingResponse[]) {
  return meetings.map((meeting) => ({
    id: meeting.zoom_meeting_id,
    url: href("/schedule/:zoom_meeting_id", { zoom_meeting_id: meeting.zoom_meeting_id }),
    title: meeting.topic,
    location: "",
    date: new Date(meeting.start_time).toLocaleDateString(),
    status: meeting.status === "ended" ? ("completed" as const) : ("in-progress" as const),
    preview: `${meeting.duration} min`,
  }))
}

export default function ScheduleLayout({ loaderData }: Route.ComponentProps) {
  const { meetings, zoom_connected } = loaderData

  return (
    <SidebarProvider
      style={
        {
          "--sidebar-width": "300px",
        } as React.CSSProperties
      }
    >
      <AppSidebar
        isLoading={!meetings}
        header="Schedule"
        entries={zoom_connected ? meetingsToSidebarEntries(meetings) : []}
      />
      <Outlet />
    </SidebarProvider>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add web/app/layouts/schedule.tsx
git commit -m "feat: add schedule layout"
```

---

## Task 10: Schedule index page

**Files:**
- Create: `web/app/routes/schedule/index.tsx`

- [ ] **Step 1: Create the index page**

The index page shows a Zoom connect CTA when not connected, or an empty-state prompt when connected but no meeting is selected.

```tsx
// web/app/routes/schedule/index.tsx
import { SidebarInset } from "@/components/ui/sidebar"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { getClient } from "~/configuration/clerk"
import { requireEnv } from "~/utils/environment"
import { useState } from "react"
import { Icons } from "~/components/icons"
import { useRouteLoaderData } from "react-router"

function ZoomConnectPrompt() {
  const [loading, setLoading] = useState(false)

  async function connectZoom() {
    setLoading(true)
    const clerk = await getClient()
    const token = await clerk!.session!.getToken()
    const base = requireEnv("VITE_PYTHON_URL").replace(/\/$/, "")
    const res = await fetch(`${base}/internal/v1/zoom-auth-url`, {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "include",
    })
    const { url } = (await res.json()) as { url: string }
    if (url) window.location.href = url
    setLoading(false)
  }

  return (
    <div className="flex h-[calc(100vh-5rem)] items-center justify-center">
      <div className="text-center">
        <h2 className="mb-2 text-xl font-semibold">Connect your Zoom account</h2>
        <p className="text-muted-foreground mb-6 text-sm">
          Connect Zoom to view your meeting schedule and access AI notes after each session.
        </p>
        <Button onClick={connectZoom} disabled={loading}>
          {loading ? (
            <>
              <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
              Connecting...
            </>
          ) : (
            "Connect Zoom"
          )}
        </Button>
      </div>
    </div>
  )
}

function EmptySelectionState() {
  return (
    <div className="flex h-[calc(100vh-5rem)] items-center justify-center">
      <div className="text-muted-foreground text-center">
        <h2 className="mb-2 text-xl font-semibold">Select a meeting</h2>
        <p className="text-sm">Choose a meeting from the list to view details</p>
      </div>
    </div>
  )
}

export default function ScheduleIndexPage() {
  // loaderData comes from the parent layout (schedule.tsx clientLoader)
  const loaderData = useRouteLoaderData("layouts/schedule") as { zoom_connected: boolean } | undefined

  return (
    <SidebarInset>
      <header className="bg-background sticky top-0 flex shrink-0 items-center gap-2 border-b p-4">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mr-2 h-4" />
        <h1 className="text-lg font-semibold">Schedule</h1>
      </header>
      {loaderData?.zoom_connected === false ? (
        <ZoomConnectPrompt />
      ) : (
        <EmptySelectionState />
      )}
    </SidebarInset>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add web/app/routes/schedule/index.tsx
git commit -m "feat: add schedule index page"
```

---

## Task 11: Schedule detail page

**Files:**
- Create: `web/app/routes/schedule/detail.tsx`

- [ ] **Step 1: Create the detail page**

```tsx
// web/app/routes/schedule/detail.tsx
import { href, useNavigate } from "react-router"
import { useState } from "react"
import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { Button } from "@/components/ui/button"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { cancelMeeting } from "~/configuration/client"
import { toast } from "~/hooks/use-toast"
import { Route } from "./+types/detail"
import { useRouteLoaderData, useRevalidator } from "react-router"
import type { ZoomMeetingResponse } from "~/configuration/client"

function formatDateTime(isoString: string): string {
  return new Date(isoString).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function MeetingDetail({ meeting }: { meeting: ZoomMeetingResponse }) {
  const [cancelling, setCancelling] = useState(false)
  const { revalidate } = useRevalidator()
  const navigate = useNavigate()

  const isScheduled = meeting.status === "scheduled"
  const isActive = meeting.status === "scheduled" || meeting.status === "started"
  const isEnded = meeting.status === "ended"

  async function handleCancel() {
    setCancelling(true)
    const { error } = await cancelMeeting({
      path: { zoom_meeting_id: meeting.zoom_meeting_id },
    })

    if (error) {
      toast({ title: "Failed to cancel meeting", variant: "destructive" })
      setCancelling(false)
      return
    }

    toast({ title: "Meeting cancelled" })
    revalidate()
    navigate(href("/schedule"))
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="mb-1 text-2xl font-semibold">{meeting.topic}</h1>
        <p className="text-muted-foreground text-sm">
          {formatDateTime(meeting.start_time)} · {meeting.duration} min
        </p>
      </div>

      <div className="flex gap-3">
        {isActive && (
          <Button
            asChild
            className="bg-[#0E72ED] text-white hover:bg-[#0E72ED]/90"
          >
            <a href={meeting.join_url} target="_blank" rel="noopener noreferrer">
              Join Meeting
            </a>
          </Button>
        )}

        {isScheduled && (
          <Button variant="outline" onClick={handleCancel} disabled={cancelling}>
            {cancelling ? "Cancelling..." : "Cancel Meeting"}
          </Button>
        )}
      </div>

      {isEnded && (
        <div className="mt-6 border-t pt-6">
          {meeting.appointment_id ? (
            <div>
              <p className="text-muted-foreground mb-2 text-sm">
                This meeting has ended. AI notes are available.
              </p>
              <Button variant="outline" asChild>
                <a href={href("/notes/:id", { id: meeting.appointment_id })}>
                  View notes →
                </a>
              </Button>
            </div>
          ) : (
            <p className="text-muted-foreground text-sm italic">
              Notes will appear here after the meeting ends and Cosma processes the recording.
            </p>
          )}
        </div>
      )}

      {!isEnded && (
        <div className="mt-6 border-t pt-6">
          <p className="text-muted-foreground text-sm italic">
            Notes will appear here after the meeting ends.
          </p>
        </div>
      )}
    </div>
  )
}

export async function clientLoader(args: Route.ClientLoaderArgs) {
  // The full meeting list is already loaded in the layout; we find the right meeting by zoom_meeting_id
  // React Router doesn't give us direct parent loader data in clientLoader, so we re-fetch.
  const { scheduleList } = await import("~/configuration/client")
  const { data } = await scheduleList()

  const meeting = data?.meetings.find(
    (m) => m.zoom_meeting_id === args.params.zoom_meeting_id
  )

  if (!meeting) {
    throw new Response("Meeting not found", { status: 404 })
  }

  return { meeting }
}

export default function ScheduleDetailPage({ loaderData }: Route.ComponentProps) {
  const { meeting } = loaderData

  return (
    <SidebarInset>
      <header className="bg-background sticky top-0 flex shrink-0 items-center gap-2 border-b p-4">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mr-2 h-4" />
        <h1 className="text-lg font-semibold">{meeting.topic}</h1>
      </header>
      <InnerScrollableContent>
        <MeetingDetail meeting={meeting} />
      </InnerScrollableContent>
    </SidebarInset>
  )
}
```

- [ ] **Step 2: Run the frontend build to verify no TypeScript errors**

```bash
cd web && pnpm run typecheck
```

Expected: no TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git add web/app/routes/schedule/
git commit -m "feat: add schedule detail page"
```

---

## Task 12: Smoke test the full flow

- [ ] **Step 1: Run all backend tests**

```bash
pytest tests/routes/schedule_test.py tests/job/sync_zoom_meetings_test.py -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run the frontend build**

```bash
cd web && pnpm run build
```

Expected: build succeeds with no errors.

- [ ] **Step 3: Manual smoke test (optional, requires a Zoom-connected dev account)**

1. Start the dev server: `fastapi dev app/server.py` and `cd web && pnpm run dev`
2. Open the app, log in, navigate to `/schedule`
3. If Zoom is not connected: see the connect CTA
4. If Zoom is connected: see the meeting list; clicking a meeting shows the detail panel with Join and Cancel buttons
5. Meetings that have ended with a linked Appointment show a "View notes →" link

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "feat: zoom meeting schedule page - complete implementation"
```
