# Design — Usage dashboard endpoint

**Issue:** #34
**Date:** 2026-05-30
**Author:** Oscar (with Claude)

## Background

Boss confirmed priority #33 → #29 → #34. With #33 + #29 closed (PRs #60-65), #34 is the last item. The boss has been running 5 SQL queries by hand to assess platform performance + usage; he wants them collected behind one authenticated endpoint so he stops copy-pasting from the issue body.

Boss is the sole consumer. Per his guidance: cheapest first pass — JSON endpoint, live queries on each request. No daily snapshot, no BI tool, no FE.

## What already exists

- `app/routes/admin.py` with `require_admin` dependency and `admin_api_app` router (mounted under `internal_api_app` at `/internal/v1/admin/`).
- `DoctorRole.admin` enum value already used by boss for `login_as` impersonation.
- Test helper `get_clerk_admin_user` in `tests/routes/utils.py` for admin-authenticated requests.
- Existing precedent for raw SQL: `app/configuration/versions.py:49` uses `sa.text()` to run `SHOW server_version`.

## What's missing

The endpoint itself + the five SQL helpers.

## Constraints

- **Raw SQL, copied near-verbatim from the issue.** Per user decision in brainstorming: copy the 5 queries the boss already vetted, run them via `session.execute(text(...))`, package the rows into Pydantic models. Avoid ORM-rewriting them — risks divergence from boss's intent.
- **No caching.** Live queries on each request. Volume is small enough today; if it stops being small, that's a separate problem.
- **Admin-only.** Endpoint mounted on `admin_api_app` which already requires `DoctorRole.admin` via `require_admin`.
- **Hard-coded date in query #5.** The original SQL says `where vaf.created_at > '2025-04-10'` — that filter was added when the boss first ran it and meant to skip pre-prod-launch noise. Keep the filter but parameterize the date as a module constant so it's discoverable instead of buried in a SQL string. Default value: `'2025-04-10'`.

## Approach

Two files:

### 1. `app/commands/usage.py` — query helpers

Five `def` functions, each runs one query and returns a list of typed Pydantic rows. Module-level constants for shared knobs (the 2025-04-10 cutoff, the LIMIT on recent appts).

```python
from datetime import date, datetime
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import text

from activemodel.session_manager import get_session


RECENT_APPOINTMENTS_LIMIT = 50
TIME_TO_NOTE_SINCE = "2025-04-10"  # original cutoff from issue #34


class RecentAppointmentRow(BaseModel):
    created_at: datetime
    doctor_email: str | None
    appointment_name: str | None


# ... (one BaseModel per query)


def recent_appointments() -> list[RecentAppointmentRow]:
    query = text(
        """
        SELECT MAX(avn.created_at) AS created_at,
               MAX(d.email)        AS doctor_email,
               MAX(a.appointment_name) AS appointment_name
        FROM ai_visit_note avn
        JOIN doctor d ON d.id = avn.doctor_id
        JOIN appointment a ON avn.appointment_id = a.id
        GROUP BY avn.appointment_id
        ORDER BY MAX(avn.created_at) DESC
        LIMIT :limit
        """
    )
    with get_session() as session:
        rows = session.execute(query, {"limit": RECENT_APPOINTMENTS_LIMIT}).all()
    return [RecentAppointmentRow(**dict(r._mapping)) for r in rows]


# ... four more, one per query.
```

### 2. New route in `app/routes/admin.py` — `GET /admin/usage`

```python
class UsageResponse(BaseModel):
    recent_appointments: list[RecentAppointmentRow]
    doctor_usage: list[DoctorUsageRow]
    daily_volume: list[DailyVolumeRow]
    pending_uploads: list[PendingUploadRow]
    time_to_note: list[TimeToNoteRow]


@admin_api_app.get("/usage")
def usage_dashboard() -> UsageResponse:
    return UsageResponse(
        recent_appointments=usage_cmd.recent_appointments(),
        doctor_usage=usage_cmd.doctor_usage(),
        daily_volume=usage_cmd.daily_volume(),
        pending_uploads=usage_cmd.pending_uploads(),
        time_to_note=usage_cmd.time_to_note(),
    )
```

Auth flows through `admin_api_app`'s `require_admin` dependency — no extra wiring needed.

### 3. Test (`tests/routes/admin_usage_test.py`)

- One test that hits the endpoint as a non-admin user and asserts 401. (Mirrors `test_non_admin_role` in `login_as_test.py`.)
- One test that hits the endpoint as an admin and asserts 200 + the response object has the five expected list keys + each is a JSON array (may be empty, may have rows depending on seed data).

We don't assert exact row counts because seed data evolves; the structural assertion is enough as a regression guard. Boss's day-to-day usage is the real signal for query correctness.

## Out of scope

- A frontend page that renders the JSON. Boss queries with curl/Postman or a browser tab against the JSON; if he later asks for HTML/React rendering, that's a follow-up.
- Date-range parameters or pagination. Add when needed.
- Daily snapshot persistence. Add when live-query latency matters.
- Sharing this dashboard with non-admins.
- Translating the 5 queries to ORM. Raw SQL is the boss's vetted source.

## Risks and rollback

- **Risk: schema drift breaks one of the raw queries.** If we later rename `ai_visit_note.doctor_id` or similar, the query fails at runtime. Mitigation: the test exercises the endpoint, so CI catches it on any schema rename PR. We do NOT pre-emptively add column-existence guards.
- **Risk: a query gets slow at scale.** Boss is sole consumer hitting maybe a few times a week; even with all five queries running it's well below load concern. Defer caching until profiling says otherwise.
- **Rollback:** revert one commit. No DB migration, no schema change, no FE change.

## Affected files

- `app/commands/usage.py` — NEW.
- `app/routes/admin.py` — add response models + new route.
- `tests/routes/admin_usage_test.py` — NEW.
