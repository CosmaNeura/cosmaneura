# Zoom Meeting Schedule Page

**Date:** 2026-04-06
**Status:** Approved

## Overview

A new authenticated page in the Cosma web app that shows a doctor's Zoom meeting schedule. Doctors can view today's meetings, upcoming meetings, and past meetings. They can join or cancel meetings from the page, and access AI-generated notes for completed meetings.

## Architecture

Four moving parts:

1. **`ZoomMeeting` DB model** — stores synced meetings per doctor, tracks lifecycle status, and links to `Appointment` once a meeting starts (enabling notes access)
2. **Celery sync task** — queued on page load; fetches upcoming meetings from Zoom API and upserts into DB
3. **Backend API endpoints** — `GET /internal/v1/schedule` (returns meetings + queues sync), `DELETE /internal/v1/schedule/{zoom_meeting_id}` (cancel)
4. **Frontend schedule page** — sidebar + detail layout, today-focused, with a Zoom connect CTA if `zoom_user_id` is null

**Data flow:**
1. Doctor opens `/schedule` → frontend calls `GET /schedule`
2. Backend returns DB meetings and queues a background Celery sync for that doctor
3. Sync fetches from Zoom API (`/users/{userId}/meetings?type=upcoming`), upserts `ZoomMeeting` rows
4. Doctor manually refreshes the page → triggers another `GET /schedule` → queues another sync (no auto-polling)
5. When a Zoom meeting starts (existing webhook) → `zoom_webhooks.py` sets `ZoomMeeting.appointment_id` and `status = started`
6. After meeting ends → `status = ended`; notes link becomes available in the detail panel

## Data Model

New model: `app/models/zoom_meeting.py`

```python
class ZoomMeetingStatus(str, Enum):
    scheduled = "scheduled"
    started = "started"
    ended = "ended"
    cancelled = "cancelled"

class ZoomMeeting(BaseModel, TimestampsMixin, TypeIDMixin("zmtg"), table=True):
    doctor_id: TypeIDType          # FK to Doctor
    zoom_meeting_id: str           # Zoom's numeric meeting ID
    topic: str
    start_time: datetime           # timezone-aware
    duration: int                  # minutes
    join_url: str
    status: ZoomMeetingStatus = ZoomMeetingStatus.scheduled
    appointment_id: TypeIDType | None = None  # set by webhook when meeting starts
```

**Upsert key:** `(doctor_id, zoom_meeting_id)`

**Notes on PHI:** No patient name or PHI stored on `ZoomMeeting`. PHI remains on `Appointment` and `Patient` as before.

**Status transitions:**
- `scheduled` → `started` via `meeting.started` webhook
- `started` → `ended` via `meeting.ended` webhook
- `scheduled` → `cancelled` via doctor action (UI + API call to Zoom)

## API Endpoints

All under `/internal/v1`, authenticated via Clerk + `inject_doctor_record`.

### `GET /schedule`

Returns all non-cancelled meetings within a window (past 30 days + next 90 days), sorted by `start_time` ascending. Past meetings only appear if a `ZoomMeeting` row existed before the meeting ended (i.e., the doctor had synced at least once prior); there is no backfill of historical meetings. Also queues a background Celery sync for the authenticated doctor.

**Response:**
```python
class ZoomMeetingResponse(BaseModel):
    id: str
    zoom_meeting_id: str
    topic: str
    start_time: datetime
    duration: int              # minutes
    join_url: str
    status: ZoomMeetingStatus
    appointment_id: str | None # if set, frontend links to /notes/{appointment_id}

class ScheduleResponse(BaseModel):
    zoom_connected: bool
    meetings: list[ZoomMeetingResponse]
```

When `zoom_connected = false`, the frontend renders a connect CTA instead of the meeting list.

Grouping into Today / Upcoming / Past is done client-side based on `start_time` relative to the current date.

### `DELETE /schedule/{zoom_meeting_id}`

Calls Zoom API `DELETE /meetings/{meetingId}`, then sets `ZoomMeeting.status = cancelled` in DB. Returns 204 on success.

**No join endpoint** — `join_url` is returned in the meeting payload and the frontend opens it directly in a new tab.

## Sync Job

New file: `app/jobs/sync_zoom_meetings.py`

```python
def perform(doctor_id: str):
    doctor = Doctor.get(id=doctor_id)
    if not doctor or not doctor.zoom_user_id:
        return

    meetings = zoom_service.get_user_meetings(doctor)

    for m in meetings:
        existing = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=str(m["id"]))
        if existing and existing.status in (ZoomMeetingStatus.started, ZoomMeetingStatus.ended):
            # Don't overwrite status for in-progress or completed meetings
            existing.update(topic=m["topic"], join_url=m["join_url"])
        elif existing:
            existing.update(
                topic=m["topic"],
                start_time=m["start_time"],
                duration=m["duration"],
                join_url=m["join_url"],
            )
        else:
            ZoomMeeting(
                doctor_id=doctor.id,
                zoom_meeting_id=str(m["id"]),
                topic=m["topic"],
                start_time=m["start_time"],
                duration=m["duration"],
                join_url=m["join_url"],
            ).save()
```

New `zoom_service` function:

```python
def get_user_meetings(doctor: Doctor) -> list[dict]:
    data = _zoom_get(
        doctor,
        f"/users/{doctor.zoom_user_id}/meetings",
        params={"type": "upcoming", "page_size": "100"},
    )
    return data.get("meetings", [])
```

## Webhook Updates

`app/jobs/zoom_webhooks.py` — two additions:

**`handle_meeting_started`:** After creating/finding the `Appointment`, find the matching `ZoomMeeting` by `zoom_meeting_id` and set `appointment_id` + `status = started`.

**`handle_meeting_ended`:** Find `ZoomMeeting` by `zoom_meeting_id`, set `status = ended`.

## Frontend Structure

**New files:**
- `web/app/routes/schedule/index.tsx` — sidebar list; meetings grouped Today / Upcoming / Past; Zoom connect CTA if not connected
- `web/app/routes/schedule/detail.tsx` — detail panel for selected meeting: topic, date/time, duration, Join button (opens `join_url` in new tab, shown for `scheduled` and `started` status), Cancel button (shown only for `scheduled` status), notes link if `appointment_id` is set
- `web/app/layouts/schedule.tsx` — sidebar shell wrapping both routes (same pattern as `layouts/notes.tsx`)

**Routes update** (`web/app/routes.ts`):
```ts
...prefix("schedule", [
  layout("layouts/schedule.tsx", [
    index("routes/schedule/index.tsx"),
    route(":zoom_meeting_id", "routes/schedule/detail.tsx"),
  ]),
]),
```

**Sidebar:** Add "Schedule" nav item alongside Notes and Intake in the existing sidebar component.

**Layout:** Sidebar + detail panel (matching the existing Notes page pattern). Today's meetings are visually distinguished (e.g., accent border or "Today" section header).

**Zoom connect state:** When `zoom_connected = false`, the index route renders a card with a "Connect Zoom" button that calls `GET /internal/v1/zoom-auth-url` and redirects — the same flow already used on the Settings page.

## Error Handling

- **Zoom API failure during sync** — task logs the error and exits without modifying DB; page loads with stale data rather than failing
- **Token expiry during sync** — handled by existing `refresh_access_token` in `_zoom_get`; no extra handling needed
- **Cancel API failure** — DB status not updated; frontend shows error toast
- **No notes yet** — `appointment_id` is null; detail panel shows "Notes will appear here after the meeting ends"
- **Zoom not connected** — `GET /schedule` returns `zoom_connected: false`; frontend renders connect CTA

## Out of Scope

- Creating new Zoom meetings from within Cosma
- Real-time meeting status updates (WebSocket/SSE)
- Recurring meeting series management
- Filtering or searching meetings
