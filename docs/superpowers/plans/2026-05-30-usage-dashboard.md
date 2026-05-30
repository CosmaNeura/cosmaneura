# Usage dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship issue #34 — an admin-only `GET /internal/v1/admin/usage` endpoint that returns the 5 platform-usage SQL queries the boss has been running by hand.

**Architecture:** Two files — `app/commands/usage.py` (5 query helpers + their Pydantic row types) and a new route in `app/routes/admin.py` that wraps them in a single response model. Raw SQL via `session.execute(text(...))`. Admin auth flows through the existing `require_admin` dependency on `admin_api_app`.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy `text()`, ActiveModel + SQLModel ORM, Pydantic.

**Spec:** `docs/superpowers/specs/2026-05-30-usage-dashboard-design.md`

**Branch:** `feat/usage-dashboard` (already created from current master).

---

## Task 1: Query helpers in `app/commands/usage.py`

**Files:**
- Create: `app/commands/usage.py`

- [ ] **Step 1: Create the module with Pydantic row types + five helpers**

```python
"""Platform-usage queries the boss runs by hand to assess system health
and adoption. Lifted from issue #34's body verbatim where possible;
parameterised where a literal value (LIMIT, since-date) is clearer as
a module constant.

All five queries are read-only. Used by the /admin/usage endpoint."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from activemodel.session_manager import get_session


RECENT_APPOINTMENTS_LIMIT = 50
DAILY_VOLUME_LIMIT = 10
TIME_TO_NOTE_SINCE = "2025-04-10"


class RecentAppointmentRow(BaseModel):
    created_at: datetime | None
    doctor_email: str | None
    appointment_name: str | None


class DoctorUsageRow(BaseModel):
    appointment_count: int
    doctor_email: str | None
    last_note_at: datetime | None


class DailyVolumeRow(BaseModel):
    day: date
    appointment_count: int
    note_count: int
    doctor_count: int


class PendingUploadRow(BaseModel):
    audio_at: datetime | None
    transcript_at: datetime | None
    note_at: datetime | None
    appointment_name: str | None


class TimeToNoteRow(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    doctor_email: str | None
    avg_time_to_note_seconds: float | None
    max_time_to_note_seconds: float | None
    min_time_to_note_seconds: float | None
    sample_count: int


def _interval_seconds(value) -> float | None:
    """timedelta from SQLAlchemy renders as datetime.timedelta; AVG of
    intervals comes back as the same. Convert to float seconds."""
    if value is None:
        return None
    if hasattr(value, "total_seconds"):
        return value.total_seconds()
    return float(value)


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


def doctor_usage() -> list[DoctorUsageRow]:
    query = text(
        """
        SELECT COUNT(DISTINCT(avn.appointment_id)) AS appointment_count,
               d.email                              AS doctor_email,
               MAX(avn.created_at)                  AS last_note_at
        FROM ai_visit_note avn
        JOIN doctor d ON d.id = avn.doctor_id
        GROUP BY avn.doctor_id, d.email
        ORDER BY appointment_count DESC
        """
    )
    with get_session() as session:
        rows = session.execute(query).all()
    return [DoctorUsageRow(**dict(r._mapping)) for r in rows]


def daily_volume() -> list[DailyVolumeRow]:
    query = text(
        """
        SELECT DATE(a.created_at)               AS day,
               COUNT(DISTINCT a.id)             AS appointment_count,
               COUNT(avn.id)                    AS note_count,
               COUNT(DISTINCT(avn.doctor_id))   AS doctor_count
        FROM appointment a
        JOIN ai_visit_note avn ON avn.appointment_id = a.id
        GROUP BY day
        ORDER BY appointment_count DESC
        LIMIT :limit
        """
    )
    with get_session() as session:
        rows = session.execute(query, {"limit": DAILY_VOLUME_LIMIT}).all()
    return [DailyVolumeRow(**dict(r._mapping)) for r in rows]


def pending_uploads() -> list[PendingUploadRow]:
    query = text(
        """
        SELECT vaf.created_at        AS audio_at,
               avt.created_at        AS transcript_at,
               avn.created_at        AS note_at,
               a.appointment_name    AS appointment_name
        FROM visit_audio_file vaf
        LEFT JOIN ai_visit_transcript avt ON avt.appointment_id = vaf.appointment_id
        JOIN appointment a ON a.id = vaf.appointment_id
        JOIN ai_visit_note avn ON avn.ai_transcript_id = avt.id
        JOIN doctor d ON vaf.doctor_id = d.id
        ORDER BY vaf.created_at DESC
        """
    )
    with get_session() as session:
        rows = session.execute(query).all()
    return [PendingUploadRow(**dict(r._mapping)) for r in rows]


def time_to_note() -> list[TimeToNoteRow]:
    query = text(
        """
        WITH time_to_notes AS (
          SELECT d.email AS doctor_email,
                 avn.created_at - vaf.created_at AS time_to_note
          FROM visit_audio_file vaf
          LEFT JOIN ai_visit_transcript avt ON avt.appointment_id = vaf.appointment_id
          JOIN ai_visit_note avn ON avn.ai_transcript_id = avt.id
          JOIN doctor d ON vaf.doctor_id = d.id
          WHERE vaf.created_at > :since
        )
        SELECT ttn.doctor_email                              AS doctor_email,
               AVG(ttn.time_to_note)                         AS avg_time_to_note,
               MAX(ttn.time_to_note)                         AS max_time_to_note,
               MIN(ttn.time_to_note)                         AS min_time_to_note,
               COUNT(DISTINCT(ttn.time_to_note))             AS sample_count
        FROM time_to_notes ttn
        GROUP BY doctor_email
        ORDER BY AVG(ttn.time_to_note) DESC
        """
    )
    with get_session() as session:
        rows = session.execute(query, {"since": TIME_TO_NOTE_SINCE}).all()

    return [
        TimeToNoteRow(
            doctor_email=row.doctor_email,
            avg_time_to_note_seconds=_interval_seconds(row.avg_time_to_note),
            max_time_to_note_seconds=_interval_seconds(row.max_time_to_note),
            min_time_to_note_seconds=_interval_seconds(row.min_time_to_note),
            sample_count=row.sample_count,
        )
        for row in rows
    ]
```

The five queries reproduce the boss's SQL as closely as possible. Differences:
- `LIMIT 50` and `LIMIT 10` extracted to module constants.
- `where vaf.created_at > '2025-04-10'` parameterised as `:since` with the same default value `'2025-04-10'`.
- The commented-out `WHERE d.email = '...'` filter from the issue's pending-uploads query is dropped — the boss kept it commented because it was a debugging line.
- `AVG(interval)` returns a Python `timedelta`; we convert to float seconds for JSON-friendliness via `_interval_seconds`.

- [ ] **Step 2: Smoke the queries against the test DB**

```bash
PYTHON_ENV=test uv run python -c "
import sys; sys.path.insert(0, '.')
import os; os.environ['PYTHON_ENV'] = 'test'
from app.commands.usage import (
    recent_appointments, doctor_usage, daily_volume, pending_uploads, time_to_note
)
print('recent:', len(recent_appointments()))
print('doctor_usage:', len(doctor_usage()))
print('daily_volume:', len(daily_volume()))
print('pending_uploads:', len(pending_uploads()))
print('time_to_note:', len(time_to_note()))
"
```

Expected: every helper returns a list (likely empty or single-digit count against the small test seed) without raising. If any raise, the error message names the SQL column the query references — usually a copy-paste typo to fix.

---

## Task 2: New endpoint `GET /admin/usage`

**Files:**
- Modify: `app/routes/admin.py`

- [ ] **Step 1: Add the response model + route**

In `app/routes/admin.py`, append (after the existing `login_as_user` route):

```python
import app.commands.usage as usage_cmd
from app.commands.usage import (
    DailyVolumeRow,
    DoctorUsageRow,
    PendingUploadRow,
    RecentAppointmentRow,
    TimeToNoteRow,
)


class UsageResponse(BasePydanticModel):
    recent_appointments: list[RecentAppointmentRow]
    doctor_usage: list[DoctorUsageRow]
    daily_volume: list[DailyVolumeRow]
    pending_uploads: list[PendingUploadRow]
    time_to_note: list[TimeToNoteRow]


@admin_api_app.get("/usage")
def usage_dashboard() -> UsageResponse:
    """Five platform-usage queries the boss runs by hand, lifted to a
    single authenticated endpoint. Admin-only via require_admin on the
    router. Issue #34."""
    return UsageResponse(
        recent_appointments=usage_cmd.recent_appointments(),
        doctor_usage=usage_cmd.doctor_usage(),
        daily_volume=usage_cmd.daily_volume(),
        pending_uploads=usage_cmd.pending_uploads(),
        time_to_note=usage_cmd.time_to_note(),
    )
```

Put the imports at the top of the file with the other imports (don't leave them inline).

- [ ] **Step 2: Smoke-test the route locally**

If you have the dev server up, you can curl the endpoint against your dev DB. Otherwise this step is the test in Task 3.

---

## Task 3: Test (`tests/routes/admin_usage_test.py`)

**Files:**
- Create: `tests/routes/admin_usage_test.py`

- [ ] **Step 1: Create the test file**

```python
from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from tests.routes.utils import (
    get_clerk_admin_user,
    get_clerk_dev_user,
    get_valid_token,
)


def _clerk_authorization(clerk_user):
    return {"Authorization": f"Bearer {get_valid_token(clerk_user)}"}


def test_usage_dashboard_rejects_non_admin(client: TestClient):
    _, _, clerk_user = get_clerk_dev_user()

    response = client.get(
        api_app.url_path_for("usage_dashboard"),
        headers=_clerk_authorization(clerk_user),
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_usage_dashboard_returns_all_five_sections(client: TestClient):
    _, _, clerk_admin = get_clerk_admin_user()

    response = client.get(
        api_app.url_path_for("usage_dashboard"),
        headers=_clerk_authorization(clerk_admin),
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()

    expected_keys = {
        "recent_appointments",
        "doctor_usage",
        "daily_volume",
        "pending_uploads",
        "time_to_note",
    }
    assert set(body.keys()) == expected_keys
    for key in expected_keys:
        assert isinstance(body[key], list), f"{key} should be a list"
```

- [ ] **Step 2: Run the tests**

```bash
PYTHON_ENV=test uv run pytest tests/routes/admin_usage_test.py -v
```

Expected: 2 tests pass.

If the admin-rejection test fails with 200 instead of 401, the `require_admin` dependency is not being honored — verify `admin_api_app` is mounted via `internal_api_app.include_router(admin_api_app)` (it is, per `app/routes/internal.py:56`).

---

## Task 4: Lint, push, open PR

- [ ] **Step 1: Lint**

```bash
just py_lint
```

Expected: clean. If ruff flags formatting, run `just py_lint_fix`.

- [ ] **Step 2: Commit**

If you've been committing per-step:

```bash
git log --oneline origin/master..HEAD
```

Confirm you have one or more commits covering: usage.py module, admin.py route, test file. Squash mentally — they ship as one PR.

If you've batched changes, commit them now in logical chunks:

```bash
git add app/commands/usage.py
git commit -m "feat(commands): add usage queries module for admin dashboard (issue #34)"

git add app/routes/admin.py
git commit -m "feat(admin): /admin/usage endpoint surfacing the five platform queries (issue #34)"

git add tests/routes/admin_usage_test.py
git commit -m "test(admin): admin/usage endpoint auth + structural response shape"
```

- [ ] **Step 3: Push**

```bash
git push -u origin feat/usage-dashboard
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(admin): /admin/usage dashboard endpoint (issue #34)" --body "$(cat <<'EOF'
## Summary
- Closes #34. Adds an admin-only \`GET /internal/v1/admin/usage\` endpoint that runs the five platform-usage SQL queries the boss has been executing by hand and returns the results as JSON.
- No FE. Boss queries with curl/Postman or browser tab; if he wants HTML/React rendering later, that's a follow-up.

## Files
- New: \`app/commands/usage.py\` (5 helpers + Pydantic row types), \`tests/routes/admin_usage_test.py\` (auth + structural).
- Modified: \`app/routes/admin.py\` (UsageResponse + usage_dashboard route).

## Queries
The five queries come from issue #34 verbatim. Differences vs. the issue body:
- \`LIMIT 50\` and \`LIMIT 10\` extracted to module constants (\`RECENT_APPOINTMENTS_LIMIT\`, \`DAILY_VOLUME_LIMIT\`).
- \`WHERE vaf.created_at > '2025-04-10'\` parameterised as \`:since\` with the same default (constant \`TIME_TO_NOTE_SINCE\`).
- Commented-out \`WHERE d.email = '...'\` debug filter in pending-uploads query is dropped.
- Interval-typed columns (avg / max / min time-to-note) are converted to float seconds for JSON-friendly output.

## Curl example
\`\`\`bash
curl -s https://api.cosma.com/internal/v1/admin/usage \\
  -H \"Authorization: Bearer <admin clerk token>\" | jq .
\`\`\`

## Test plan
- [ ] CI green on Backend.
- [ ] Hit the endpoint in staging as the admin user; confirm a JSON object with the five expected keys, each a list.
- [ ] Spot-check that the doctor_usage ordering matches what the boss saw running the SQL by hand.

## Spec & plan
- Spec: \`docs/superpowers/specs/2026-05-30-usage-dashboard-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-30-usage-dashboard.md\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Surface URL and CI status**

---

## Self-review notes

- **Spec coverage:** five query helpers (Task 1), one endpoint (Task 2), two tests (Task 3), lint+push+PR (Task 4). All spec deliverables covered.
- **Raw SQL faithful to issue.** The biggest delta is the interval-to-float conversion, which is for JSON-serialisability, not for query semantics.
- **`session.execute(text(...))`** uses bound parameters for the two LIMITs and the since-date — no string interpolation, so no SQL-injection risk even though the only caller is internal.
- **No FE work.** Boss queries via curl. If he wants a rendered page later, that's a separate PR.
