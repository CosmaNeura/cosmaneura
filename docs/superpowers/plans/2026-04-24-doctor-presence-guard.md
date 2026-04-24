# Doctor Presence Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate Recall bot creation and recording on confirmed doctor presence in the Zoom call; each patient session within a PMR becomes its own appointment with its own bot.

**Architecture:** Refactor the Zoom webhook handlers in `app/services/zoom_webhooks.py` to defer bot creation until the doctor + at least one non-doctor coexist in the call. Introduce a new Celery task in `app/jobs/recording_guard.py` for 90s doctor-absence grace. Add a `leave_call` helper to `app/services/recall.py`. Replace name-based doctor match with Zoom `participant.id` matching against `DoctorVideoIntegration.provider_user_id`. Identify appointment boundaries by `patient_count` returning to 0 with the doctor still present.

**Tech Stack:** Python 3.11, FastAPI, Celery + Redis (redbeat scheduler), SQLModel/ActiveModel for ORM, pytest, structlog. External: Zoom Webhooks API, Recall.ai Bot API.

**Spec:** `docs/superpowers/specs/2026-04-24-doctor-presence-guard-design.md`

---

## File Map

**Modify:**
- `app/services/zoom_webhooks.py` — full handler rewrite
- `app/services/recall.py` — add `leave_call`; extend `create_bot` signature with optional `metadata`
- `infra/Justfile` — add `azure_logs_for_meeting` recipe

**Create:**
- `app/jobs/recording_guard.py` — new Celery task `stop_recording_if_absent`
- `tests/services/zoom_webhooks_test.py` — scenario tests for the new behavior
- `tests/services/recall_test.py` — unit tests for the new `leave_call` + `create_bot` metadata

**Unchanged but relevant for reference:**
- `app/services/recall_webhooks.py` — `recall:recording:{recording_id}:*` keys and `recall:bot:{bot_id}:*` fallback continue to work; no change needed
- `app/jobs/webhooks.py` — dispatch unchanged
- `app/configuration/redis.py` — `get_redis()` unchanged

---

## Task 1: Add `leave_call` helper and `metadata` param to `recall.create_bot`

**Files:**
- Modify: `app/services/recall.py`
- Test: `tests/services/recall_test.py` (create)

- [ ] **Step 1: Write failing test for `leave_call` happy path**

Create `tests/services/recall_test.py`:

```python
import httpx
import pytest

from app.services import recall


def test_leave_call_posts_to_bot_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"ok": true}'
        @property
        def is_success(self): return True
        def raise_for_status(self): return None

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", lambda: FakeClient())

    recall.leave_call("bot-abc-123")

    assert captured["url"].endswith("/bot/bot-abc-123/leave_call/")
    assert "Authorization" in captured["headers"]


def test_leave_call_tolerates_already_done_bot(monkeypatch):
    class FakeResponse:
        status_code = 400
        text = "bot already done"
        @property
        def is_success(self): return False
        def raise_for_status(self):
            raise httpx.HTTPStatusError("", request=None, response=self)

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None):
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", lambda: FakeClient())

    # Should NOT raise — 400 means bot already terminated
    recall.leave_call("bot-abc-123")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/services/recall_test.py -v
```

Expected: FAIL with `AttributeError: module 'app.services.recall' has no attribute 'leave_call'`.

- [ ] **Step 3: Implement `leave_call` in `app/services/recall.py`**

Add at the end of `app/services/recall.py`:

```python
def leave_call(bot_id: str) -> None:
    """Tell the bot to leave the meeting. Triggers the Recall `done`
    terminal status and stops billing for this bot. Safe to call on a
    bot that already left."""
    with httpx.Client() as client:
        response = client.post(
            f"{RECALL_API_BASE}/bot/{bot_id}/leave_call/",
            headers={"Authorization": f"Token {RECALL_API_KEY}"},
        )
    if response.status_code == httpx.codes.BAD_REQUEST:
        log.info(
            "recall leave_call skipped, bot already done",
            bot_id=bot_id,
            body=response.text,
        )
        return
    if not response.is_success:
        log.error(
            "recall leave_call error",
            bot_id=bot_id,
            status=response.status_code,
            body=response.text,
        )
    response.raise_for_status()
    log.info("recall bot instructed to leave call", bot_id=bot_id)
```

- [ ] **Step 4: Write failing test for `metadata` param on `create_bot`**

Add to `tests/services/recall_test.py`:

```python
def test_create_bot_forwards_metadata(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"id": "bot-xyz"}'
        @property
        def is_success(self): return True
        def raise_for_status(self): return None
        def json(self): return {"id": "bot-xyz"}

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json=None, headers=None):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", lambda: FakeClient())

    recall.create_bot(
        meeting_url="https://zoom.us/j/123",
        zak_token="zak",
        metadata={"doctor_id": "doctor_x", "meeting_id": "m1"},
    )

    assert captured["payload"]["metadata"] == {
        "doctor_id": "doctor_x",
        "meeting_id": "m1",
    }
```

- [ ] **Step 5: Run test to verify it fails**

```bash
uv run pytest tests/services/recall_test.py -v::test_create_bot_forwards_metadata
```

Expected: FAIL with `TypeError: create_bot() got an unexpected keyword argument 'metadata'`.

- [ ] **Step 6: Extend `create_bot` signature**

In `app/services/recall.py`, modify `create_bot` at the top:

```python
def create_bot(
    meeting_url: str,
    zak_token: str,
    bot_name: str = "Cosma",
    manual_recording: bool = False,
    metadata: dict | None = None,
) -> dict:
    payload = {
        "meeting_url": meeting_url,
        "bot_name": bot_name,
        "zoom": {
            "zak_token": zak_token,
        },
    }
    if manual_recording:
        payload["recording_config"] = None
    if metadata is not None:
        payload["metadata"] = metadata
    # ... rest unchanged
```

- [ ] **Step 7: Run all tests to verify both pass**

```bash
uv run pytest tests/services/recall_test.py -v
```

Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add app/services/recall.py tests/services/recall_test.py
git commit -m "feat(recall): add leave_call helper and metadata param for create_bot

Needed for the per-patient bot lifecycle so we can tag bots with
doctor_id/meeting_id and destroy them cleanly when an appointment
ends within an ongoing PMR session."
```

---

## Task 2: Add `stop_recording_if_absent` Celery task

**Files:**
- Create: `app/jobs/recording_guard.py`
- Test: `tests/services/recording_guard_test.py` (create)

- [ ] **Step 1: Write failing test — task aborts when doctor present**

Create `tests/services/recording_guard_test.py`:

```python
import pytest

from app.configuration.redis import get_redis


@pytest.fixture
def clean_redis():
    redis = get_redis()
    # Nuke test keys for meeting_id "test-mtg-1"
    for suffix in [
        "doctor_present", "bot_id", "recording_active",
        "grace_task_id", "participants_order", "participants_names",
    ]:
        redis.delete(f"recall:meeting:test-mtg-1:{suffix}")
    yield redis
    for suffix in [
        "doctor_present", "bot_id", "recording_active",
        "grace_task_id", "participants_order", "participants_names",
    ]:
        redis.delete(f"recall:meeting:test-mtg-1:{suffix}")


def test_grace_task_aborts_when_doctor_back(monkeypatch, clean_redis):
    from app.jobs import recording_guard

    clean_redis.set("recall:meeting:test-mtg-1:doctor_present", "1")
    clean_redis.set("recall:meeting:test-mtg-1:bot_id", "bot-1")
    clean_redis.set("recall:meeting:test-mtg-1:recording_active", "1")

    called = {"stop": 0, "leave": 0}

    def fake_stop(bot_id): called["stop"] += 1
    def fake_leave(bot_id): called["leave"] += 1

    monkeypatch.setattr(recording_guard.recall, "stop_recording", fake_stop)
    monkeypatch.setattr(recording_guard.recall, "leave_call", fake_leave)

    recording_guard.stop_recording_if_absent("test-mtg-1")

    assert called == {"stop": 0, "leave": 0}
    # Redis state unchanged
    assert clean_redis.get("recall:meeting:test-mtg-1:recording_active") == b"1"


def test_grace_task_stops_and_leaves_when_doctor_still_absent(monkeypatch, clean_redis):
    from app.jobs import recording_guard

    # doctor_present NOT set — simulates doctor still gone
    clean_redis.set("recall:meeting:test-mtg-1:bot_id", "bot-1")
    clean_redis.set("recall:meeting:test-mtg-1:recording_active", "1")

    called = {"stop": [], "leave": []}
    monkeypatch.setattr(
        recording_guard.recall, "stop_recording",
        lambda bid: called["stop"].append(bid),
    )
    monkeypatch.setattr(
        recording_guard.recall, "leave_call",
        lambda bid: called["leave"].append(bid),
    )

    recording_guard.stop_recording_if_absent("test-mtg-1")

    assert called["stop"] == ["bot-1"]
    assert called["leave"] == ["bot-1"]
    # Redis cleaned
    assert clean_redis.get("recall:meeting:test-mtg-1:recording_active") is None
    assert clean_redis.get("recall:meeting:test-mtg-1:bot_id") is None


def test_grace_task_noop_when_no_bot(monkeypatch, clean_redis):
    from app.jobs import recording_guard

    called = {"stop": 0, "leave": 0}
    monkeypatch.setattr(
        recording_guard.recall, "stop_recording",
        lambda bid: called.update(stop=called["stop"] + 1),
    )
    monkeypatch.setattr(
        recording_guard.recall, "leave_call",
        lambda bid: called.update(leave=called["leave"] + 1),
    )

    # No keys set at all
    recording_guard.stop_recording_if_absent("test-mtg-1")

    assert called == {"stop": 0, "leave": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/services/recording_guard_test.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.jobs.recording_guard'`.

- [ ] **Step 3: Create `app/jobs/recording_guard.py`**

```python
"""Celery task that stops a Recall recording if the doctor has not
returned to the Zoom meeting within the grace period.

The task is safe to run even if the doctor has returned — its first
check is against Redis `doctor_present`, which is source of truth."""

from app import log
from app.celery import celery_app
from app.configuration.redis import get_redis
from app.services import recall


def _rget(redis, key: str) -> str | None:
    val = redis.get(key)
    return val.decode() if isinstance(val, bytes) else val


def perform(meeting_id: str) -> None:
    redis = get_redis()
    key_prefix = f"recall:meeting:{meeting_id}"

    if redis.get(f"{key_prefix}:doctor_present"):
        log.info(
            "grace check: doctor back in call, no action",
            meeting_id=meeting_id,
        )
        return

    bot_id = _rget(redis, f"{key_prefix}:bot_id")
    if not bot_id:
        log.info(
            "grace check: no bot tracked, nothing to stop",
            meeting_id=meeting_id,
        )
        return

    if not redis.get(f"{key_prefix}:recording_active"):
        log.info(
            "grace check: recording not active, nothing to stop",
            meeting_id=meeting_id,
            bot_id=bot_id,
        )
        return

    log.info(
        "grace period expired, stopping recording",
        meeting_id=meeting_id,
        bot_id=bot_id,
    )
    recall.stop_recording(bot_id)
    recall.leave_call(bot_id)
    redis.delete(f"{key_prefix}:recording_active")
    redis.delete(f"{key_prefix}:bot_id")
    redis.delete(f"{key_prefix}:grace_task_id")
    redis.delete(f"{key_prefix}:participants_order")
    redis.delete(f"{key_prefix}:participants_names")


stop_recording_if_absent = celery_app.task(perform)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/services/recording_guard_test.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/jobs/recording_guard.py tests/services/recording_guard_test.py
git commit -m "feat(recall): add stop_recording_if_absent celery task

Powers the 90s doctor-absence grace period. The task verifies
doctor_present in Redis before acting, so revocation failure is
not a correctness bug."
```

---

## Task 3: Scaffold test file for zoom_webhooks and add meeting.started test

**Files:**
- Create: `tests/services/zoom_webhooks_test.py`

- [ ] **Step 1: Write scaffolding + first test — meeting.started no longer creates bot**

Create `tests/services/zoom_webhooks_test.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from app.configuration.redis import get_redis
from app.models.doctor import Doctor
from app.models.doctor_video_integration import (
    DoctorVideoIntegration,
    VideoProvider,
)
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus

from tests.factories import DoctorFactory


MEETING_ID = "99911112222"
HOST_ID = "zoom-host-abc"
BOT_ID = "recall-bot-abc"


def _key(suffix: str) -> str:
    return f"recall:meeting:{MEETING_ID}:{suffix}"


@pytest.fixture
def clean_redis():
    redis = get_redis()
    suffixes = [
        "bot_id", "doctor_id", "host_id", "doctor_present", "recording_active",
        "participants_order", "participants_names", "patient_count",
        "starting_lock", "grace_task_id",
    ]
    for s in suffixes:
        redis.delete(_key(s))
    yield redis
    for s in suffixes:
        redis.delete(_key(s))


@pytest.fixture
def doctor_with_zoom():
    doctor = DoctorFactory.build().save()
    DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id=HOST_ID,
        access_token="tok",
        refresh_token="rtok",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ).save()
    return doctor


@pytest.fixture
def mock_recall(monkeypatch):
    calls = {"create_bot": [], "start_recording": [], "stop_recording": [], "leave_call": []}

    from app.services import recall

    def fake_create_bot(meeting_url, zak_token, bot_name="Cosma", manual_recording=False, metadata=None):
        calls["create_bot"].append(
            {"meeting_url": meeting_url, "metadata": metadata, "manual_recording": manual_recording}
        )
        return {"id": BOT_ID}

    def fake_start_recording(bot_id, wait_for_ready=True):
        calls["start_recording"].append(bot_id)
        return {"id": f"recording-{bot_id}"}

    def fake_stop_recording(bot_id):
        calls["stop_recording"].append(bot_id)

    def fake_leave_call(bot_id):
        calls["leave_call"].append(bot_id)

    monkeypatch.setattr(recall, "create_bot", fake_create_bot)
    monkeypatch.setattr(recall, "start_recording", fake_start_recording)
    monkeypatch.setattr(recall, "stop_recording", fake_stop_recording)
    monkeypatch.setattr(recall, "leave_call", fake_leave_call)
    return calls


@pytest.fixture
def mock_zoom(monkeypatch):
    from app.services import zoom as zoom_service

    monkeypatch.setattr(zoom_service, "get_zak_token", lambda doctor: "zak-fake")
    monkeypatch.setattr(
        zoom_service, "get_meeting_join_url",
        lambda doctor, meeting_id: f"https://zoom.us/j/{meeting_id}",
    )
    monkeypatch.setattr(
        zoom_service, "get_user_display_name", lambda doctor: "Test Doctor",
    )


def _started_payload() -> dict:
    return {
        "payload": {
            "object": {
                "id": MEETING_ID,
                "host_id": HOST_ID,
            }
        }
    }


def _participant_joined_payload(participant_id: str, user_name: str) -> dict:
    return {
        "payload": {
            "object": {
                "id": MEETING_ID,
                "participant": {
                    "id": participant_id,
                    "user_name": user_name,
                },
            }
        }
    }


def _participant_left_payload(participant_id: str, user_name: str) -> dict:
    return _participant_joined_payload(participant_id, user_name)


def _meeting_ended_payload() -> dict:
    return {"payload": {"object": {"id": MEETING_ID}}}


# --------- Scenario 1: meeting.started ---------

def test_meeting_started_does_not_create_bot(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())

    assert mock_recall["create_bot"] == []
    assert clean_redis.get(_key("bot_id")) is None
    assert clean_redis.get(_key("doctor_id")) is not None
    assert clean_redis.get(_key("host_id")).decode() == HOST_ID
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v::test_meeting_started_does_not_create_bot
```

Expected: FAIL — current handler calls `recall.create_bot` and stores `bot_id`.

- [ ] **Step 3: Modify `handle_meeting_started` in `app/services/zoom_webhooks.py`**

Replace the body of `handle_meeting_started` (keeping signature) with:

```python
def handle_meeting_started(payload: dict[str, Any]):
    obj = payload.get("payload", {}).get("object", {})
    host_id = obj.get("host_id")
    meeting_id = obj.get("id")

    log.info("meeting.started received", host_id=host_id, meeting_id=meeting_id)

    redis = get_redis()
    lock_key = f"recall:meeting:{meeting_id}:starting_lock"
    if not redis.set(lock_key, "1", nx=True, ex=60):
        log.info(
            "meeting.started duplicate within active lock window, skipping",
            meeting_id=meeting_id,
        )
        return

    doctor = cast(
        Doctor | None,
        Doctor.where(
            Doctor.id
            == select(cast(Any, DoctorVideoIntegration.doctor_id))
            .where(
                (DoctorVideoIntegration.provider == VideoProvider.zoom)
                & (DoctorVideoIntegration.provider_user_id == host_id)
            )
            .scalar_subquery()
        ).first(),
    )
    if not doctor:
        log.info(
            "no doctor found for zoom host_id, skipping session registration",
            host_id=host_id,
        )
        return

    integration = zoom_service.get_zoom_integration(doctor)
    if not integration:
        log.info(
            "doctor has no zoom integration, skipping session registration",
            doctor_id=str(doctor.id),
        )
        return

    _clear_meeting_keys(redis, meeting_id, preserve_lock=True)

    ttl = timedelta(hours=24)
    redis.set(f"recall:meeting:{meeting_id}:doctor_id", str(doctor.id), ex=ttl)
    redis.set(f"recall:meeting:{meeting_id}:host_id", host_id, ex=ttl)

    log.info(
        "meeting session started, awaiting doctor",
        meeting_id=meeting_id,
        doctor_id=str(doctor.id),
    )

    zoom_mtg = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=str(meeting_id))
    if zoom_mtg and zoom_mtg.status == ZoomMeetingStatus.scheduled:
        zoom_mtg.status = ZoomMeetingStatus.started
        zoom_mtg.save()
```

Also update `_clear_meeting_keys` to include new suffixes:

```python
def _clear_meeting_keys(redis, meeting_id: str, preserve_lock: bool = False) -> None:
    suffixes = [
        "bot_id", "doctor_id", "host_id", "doctor_present", "recording_active",
        "participants_order", "participants_names", "patient_count",
        "grace_task_id",
    ]
    if not preserve_lock:
        suffixes.append("starting_lock")
    for suffix in suffixes:
        redis.delete(f"recall:meeting:{meeting_id}:{suffix}")
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v::test_meeting_started_does_not_create_bot
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/zoom_webhooks.py tests/services/zoom_webhooks_test.py
git commit -m "refactor(zoom): defer bot creation out of meeting.started

meeting.started now only registers meeting context in Redis.
Bot creation moves to handle_participant_joined once both a
doctor and a non-doctor are present."
```

---

## Task 4: Replace name-based doctor match with `participant.id` match

**Files:**
- Modify: `app/services/zoom_webhooks.py`
- Test: `tests/services/zoom_webhooks_test.py`

- [ ] **Step 1: Write failing test — participant matching by id**

Add to `tests/services/zoom_webhooks_test.py`:

```python
def test_doctor_identified_by_participant_id_not_name(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    """A guest whose user_name matches the doctor's display name but
    whose participant.id is different must NOT be treated as doctor."""
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())

    # Impersonator: same display name, different id
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("guest-spoof-id", "Test Doctor")
    )

    assert clean_redis.get(_key("doctor_present")) is None
    # They got added to the waiting-room hash, no bot yet
    assert clean_redis.get(_key("host_id")).decode() == HOST_ID


def test_doctor_identified_marks_present(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert clean_redis.get(_key("doctor_present")) == b"1"
    # no non-doctors, so no bot created
    assert mock_recall["create_bot"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v::test_doctor_identified_by_participant_id_not_name tests/services/zoom_webhooks_test.py::test_doctor_identified_marks_present
```

Expected: FAIL — handler still uses name-based match.

- [ ] **Step 3: Rewrite identification + join handler (partial)**

In `app/services/zoom_webhooks.py`, remove `_is_doctor_or_bot` and `BOT_NAME` filtering logic (but keep `BOT_NAME = "Cosma"` as constant). Replace `handle_participant_joined` with:

```python
def _role_for(participant: dict, host_id: str | None) -> str:
    if participant.get("user_name") == BOT_NAME:
        return "bot"
    if host_id and participant.get("id") == host_id:
        return "doctor"
    return "other"


def handle_participant_joined(payload: dict[str, Any]):
    obj = payload.get("payload", {}).get("object", {})
    meeting_id = obj.get("id")
    participant = obj.get("participant", {})
    participant_id = participant.get("id") or ""
    user_name = participant.get("user_name") or ""

    redis = get_redis()
    host_id = _rget(redis, f"recall:meeting:{meeting_id}:host_id")
    role = _role_for(participant, host_id)

    if role == "bot":
        log.info(
            "participant event",
            event="joined", role="bot", meeting_id=meeting_id,
            participant_id=participant_id, user_name=user_name,
        )
        return

    if role == "doctor":
        ttl = timedelta(hours=24)
        redis.set(f"recall:meeting:{meeting_id}:doctor_present", "1", ex=ttl)
        log.info(
            "participant event",
            event="joined", role="doctor", meeting_id=meeting_id,
            participant_id=participant_id, user_name=user_name,
            doctor_present=True,
        )
        # Additional doctor-specific logic in next tasks
        return

    # role == "other" — just stash for now; appointment-start logic in next tasks
    ttl = timedelta(hours=24)
    redis.rpush(f"recall:meeting:{meeting_id}:participants_order", participant_id)
    redis.expire(f"recall:meeting:{meeting_id}:participants_order", int(ttl.total_seconds()))
    redis.hset(f"recall:meeting:{meeting_id}:participants_names", participant_id, user_name)
    redis.expire(f"recall:meeting:{meeting_id}:participants_names", int(ttl.total_seconds()))
    redis.incr(f"recall:meeting:{meeting_id}:patient_count")
    redis.expire(f"recall:meeting:{meeting_id}:patient_count", int(ttl.total_seconds()))
    log.info(
        "participant event",
        event="joined", role="other", meeting_id=meeting_id,
        participant_id=participant_id, user_name=user_name,
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v
```

Expected: all three tests in this file pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/zoom_webhooks.py tests/services/zoom_webhooks_test.py
git commit -m "refactor(zoom): identify doctor by participant.id, not display name

Prevents a guest who sets user_name to the doctor's display name
from bypassing the doctor-presence filter. The doctor's Zoom user
id (DoctorVideoIntegration.provider_user_id) is the source of truth."
```

---

## Task 5: Add `_start_appointment_session` helper and trigger it from participant_joined

**Files:**
- Modify: `app/services/zoom_webhooks.py`
- Test: `tests/services/zoom_webhooks_test.py`

- [ ] **Step 1: Write failing test — doctor joins with patient already waiting, bot is created**

Add to `tests/services/zoom_webhooks_test.py`:

```python
def test_patient_joins_first_then_doctor_starts_session(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-1", "Flor Guerrero")
    )
    # No bot yet (doctor absent)
    assert mock_recall["create_bot"] == []

    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert len(mock_recall["create_bot"]) == 1
    assert mock_recall["create_bot"][0]["metadata"]["meeting_id"] == MEETING_ID
    assert mock_recall["start_recording"] == [BOT_ID]
    assert clean_redis.get(_key("bot_id")).decode() == BOT_ID
    assert clean_redis.get(_key("recording_active")) == b"1"


def test_doctor_joins_first_no_patients_no_bot(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert mock_recall["create_bot"] == []
    assert clean_redis.get(_key("doctor_present")) == b"1"
    assert clean_redis.get(_key("bot_id")) is None
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v::test_patient_joins_first_then_doctor_starts_session
```

Expected: FAIL — `create_bot` not called yet when doctor joins.

- [ ] **Step 3: Add `_start_appointment_session` helper and wire it into `handle_participant_joined`**

In `app/services/zoom_webhooks.py`, add this helper and extend the doctor branch:

```python
def _start_appointment_session(meeting_id: str, participant_id: str, user_name: str):
    """Idempotent via recording_active. Creates bot, waits for ready,
    starts recording, resolves or creates the appointment/patient."""
    redis = get_redis()
    if redis.get(f"recall:meeting:{meeting_id}:recording_active"):
        return

    doctor_id = _rget(redis, f"recall:meeting:{meeting_id}:doctor_id")
    if not doctor_id:
        log.warning(
            "cannot start appointment: no doctor_id in redis",
            meeting_id=meeting_id,
        )
        return

    doctor = cast(Doctor | None, Doctor.get(id=doctor_id))
    if not doctor:
        log.warning(
            "cannot start appointment: doctor not found",
            doctor_id=doctor_id, meeting_id=meeting_id,
        )
        return

    zak_token = zoom_service.get_zak_token(doctor)
    meeting_url = zoom_service.get_meeting_join_url(doctor, meeting_id)
    bot_data = recall.create_bot(
        meeting_url=meeting_url,
        zak_token=zak_token,
        manual_recording=True,
        metadata={"doctor_id": doctor_id, "meeting_id": str(meeting_id)},
    )
    bot_id = bot_data["id"]
    ttl = timedelta(hours=24)
    redis.set(f"recall:meeting:{meeting_id}:bot_id", bot_id, ex=ttl)
    redis.set(f"recall:bot:{bot_id}:doctor_id", doctor_id, ex=ttl)

    recording_data = recall.start_recording(bot_id)
    recording_id = recording_data.get("id")
    if not recording_id:
        log.warning(
            "bot never reached ready state, aborting appointment",
            bot_id=bot_id, meeting_id=meeting_id,
        )
        recall.leave_call(bot_id)
        redis.delete(f"recall:meeting:{meeting_id}:bot_id")
        return

    redis.set(f"recall:meeting:{meeting_id}:recording_active", "1", ex=ttl)

    now = datetime.utcnow()
    appointment = cast(
        Appointment | None,
        Appointment.where(
            (Appointment.doctor_id == doctor_id)
            & (Appointment.appointment_date >= now - timedelta(hours=4))
            & (Appointment.appointment_date <= now + timedelta(hours=1))
        )
        .order_by(cast(Any, Appointment.appointment_date).desc())
        .first(),
    )
    if not appointment:
        appointment = _ensure_appointment(
            doctor_id=doctor_id,
            patient_name=user_name or "Unknown",
            now=now,
        )

    redis.set(f"recall:recording:{recording_id}:doctor_id", doctor_id, ex=ttl)
    redis.set(
        f"recall:recording:{recording_id}:appointment_id",
        str(appointment.id), ex=ttl,
    )
    redis.set(
        f"recall:recording:{recording_id}:patient_id",
        str(appointment.patient_id), ex=ttl,
    )
    redis.set(f"recall:bot:{bot_id}:appointment_id", str(appointment.id), ex=ttl)
    redis.set(f"recall:bot:{bot_id}:patient_id", str(appointment.patient_id), ex=ttl)

    log.info(
        "appointment session started",
        meeting_id=meeting_id, bot_id=bot_id,
        appointment_id=str(appointment.id),
        patient_name=user_name,
    )

    if doctor_id:
        zoom_mtg = ZoomMeeting.get(doctor_id=doctor_id, zoom_meeting_id=str(meeting_id))
        if zoom_mtg:
            zoom_mtg.appointment_id = appointment.id
            zoom_mtg.status = ZoomMeetingStatus.started
            zoom_mtg.save()
```

Then in the doctor branch of `handle_participant_joined` (added in Task 4), before the return, add:

```python
    if role == "doctor":
        ttl = timedelta(hours=24)
        redis.set(f"recall:meeting:{meeting_id}:doctor_present", "1", ex=ttl)
        log.info(
            "participant event",
            event="joined", role="doctor", meeting_id=meeting_id,
            participant_id=participant_id, user_name=user_name,
            doctor_present=True,
        )

        # If non-doctors are already waiting, start the session now
        waiting = redis.llen(f"recall:meeting:{meeting_id}:participants_order")
        recording_active = redis.get(f"recall:meeting:{meeting_id}:recording_active")
        if waiting and not recording_active:
            first_id = redis.lindex(f"recall:meeting:{meeting_id}:participants_order", 0)
            first_id = first_id.decode() if isinstance(first_id, bytes) else first_id
            first_name = redis.hget(
                f"recall:meeting:{meeting_id}:participants_names", first_id,
            )
            first_name = first_name.decode() if isinstance(first_name, bytes) else first_name
            _start_appointment_session(meeting_id, first_id, first_name or "Unknown")
        return
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v
```

Expected: all tests in file pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/zoom_webhooks.py tests/services/zoom_webhooks_test.py
git commit -m "feat(zoom): start appointment session when doctor + non-doctor coexist

When the doctor joins and at least one non-doctor is already waiting,
create the Recall bot, wait for it to join, start recording, and
create/link the appointment. No-op when either side is missing."
```

---

## Task 6: Trigger session start when a non-doctor joins after doctor

**Files:**
- Modify: `app/services/zoom_webhooks.py`
- Test: `tests/services/zoom_webhooks_test.py`

- [ ] **Step 1: Write failing test — doctor present, patient joins → bot created**

Add to `tests/services/zoom_webhooks_test.py`:

```python
def test_doctor_present_then_patient_joins_starts_session(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    assert mock_recall["create_bot"] == []  # no patient yet

    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan Perez")
    )

    assert len(mock_recall["create_bot"]) == 1
    assert mock_recall["start_recording"] == [BOT_ID]
    assert clean_redis.get(_key("recording_active")) == b"1"


def test_additional_participant_during_active_session_no_new_bot(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan Perez")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("family-b", "Maria Perez (mamá)")
    )

    assert len(mock_recall["create_bot"]) == 1  # still just one bot
    assert mock_recall["start_recording"] == [BOT_ID]  # one start
    assert int(clean_redis.get(_key("patient_count"))) == 2
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v::test_doctor_present_then_patient_joins_starts_session
```

Expected: FAIL — the "other" branch in `handle_participant_joined` still doesn't trigger session start.

- [ ] **Step 3: Extend the "other" branch in `handle_participant_joined`**

In `app/services/zoom_webhooks.py`, modify the `role == "other"` branch to:

```python
    # role == "other"
    ttl = timedelta(hours=24)
    redis.rpush(f"recall:meeting:{meeting_id}:participants_order", participant_id)
    redis.expire(f"recall:meeting:{meeting_id}:participants_order", int(ttl.total_seconds()))
    redis.hset(f"recall:meeting:{meeting_id}:participants_names", participant_id, user_name)
    redis.expire(f"recall:meeting:{meeting_id}:participants_names", int(ttl.total_seconds()))
    new_count = redis.incr(f"recall:meeting:{meeting_id}:patient_count")
    redis.expire(f"recall:meeting:{meeting_id}:patient_count", int(ttl.total_seconds()))

    doctor_present = bool(redis.get(f"recall:meeting:{meeting_id}:doctor_present"))
    recording_active = bool(redis.get(f"recall:meeting:{meeting_id}:recording_active"))

    log.info(
        "participant event",
        event="joined", role="other", meeting_id=meeting_id,
        participant_id=participant_id, user_name=user_name,
        doctor_present=doctor_present, recording_active=recording_active,
        patient_count=new_count,
    )

    if not doctor_present:
        log.info(
            "non-doctor joined before doctor, holding",
            meeting_id=meeting_id, user_name=user_name,
        )
        return

    if not recording_active:
        _start_appointment_session(meeting_id, participant_id, user_name)
    else:
        log.info(
            "additional participant in active session",
            meeting_id=meeting_id, user_name=user_name, patient_count=new_count,
        )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/zoom_webhooks.py tests/services/zoom_webhooks_test.py
git commit -m "feat(zoom): start appointment when non-doctor joins with doctor present

Non-doctors that join an active session just increment the counter
(accompanying family). Non-doctors that join an idle-with-doctor
session trigger a new appointment + bot + recording."
```

---

## Task 7: Implement appointment close on patient_count → 0 (in `handle_participant_left`)

**Files:**
- Modify: `app/services/zoom_webhooks.py`
- Test: `tests/services/zoom_webhooks_test.py`

- [ ] **Step 1: Write failing test — last patient leaves, appointment closes, bot leaves**

Add to `tests/services/zoom_webhooks_test.py`:

```python
def test_last_patient_leaves_closes_appointment_and_leaves_bot(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    # Now recording is active
    zoom_webhooks.handle_participant_left(
        _participant_left_payload("patient-a", "Juan")
    )

    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == [BOT_ID]
    assert clean_redis.get(_key("recording_active")) is None
    assert clean_redis.get(_key("bot_id")) is None
    assert clean_redis.llen(_key("participants_order")) == 0


def test_sequential_patients_produce_two_bots(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    # Patient A cycle
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_participant_left(
        _participant_left_payload("patient-a", "Juan")
    )

    # Patient B enters — should create a brand-new bot
    mock_recall["create_bot"][0]["__marker__"] = True  # mark old bot
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-b", "Sofia")
    )

    assert len(mock_recall["create_bot"]) == 2
    assert mock_recall["start_recording"] == [BOT_ID, BOT_ID]
    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == [BOT_ID]


def test_accompanying_family_leaves_keeps_recording(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("family-b", "Maria")
    )
    zoom_webhooks.handle_participant_left(
        _participant_left_payload("family-b", "Maria")
    )

    # family left but patient still there: keep recording
    assert mock_recall["stop_recording"] == []
    assert mock_recall["leave_call"] == []
    assert clean_redis.get(_key("recording_active")) == b"1"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v::test_last_patient_leaves_closes_appointment_and_leaves_bot
```

Expected: FAIL — `handle_participant_left` still uses the old logic.

- [ ] **Step 3: Rewrite `handle_participant_left`**

Replace the body of `handle_participant_left` in `app/services/zoom_webhooks.py`:

```python
def handle_participant_left(payload: dict[str, Any]):
    obj = payload.get("payload", {}).get("object", {})
    meeting_id = obj.get("id")
    participant = obj.get("participant", {})
    participant_id = participant.get("id") or ""
    user_name = participant.get("user_name") or ""

    redis = get_redis()
    host_id = _rget(redis, f"recall:meeting:{meeting_id}:host_id")
    role = _role_for(participant, host_id)

    if role == "bot":
        log.info(
            "participant event",
            event="left", role="bot", meeting_id=meeting_id,
            participant_id=participant_id, user_name=user_name,
        )
        return

    if role == "doctor":
        redis.delete(f"recall:meeting:{meeting_id}:doctor_present")
        recording_active = bool(redis.get(f"recall:meeting:{meeting_id}:recording_active"))
        log.info(
            "participant event",
            event="left", role="doctor", meeting_id=meeting_id,
            participant_id=participant_id, user_name=user_name,
            doctor_present=False, recording_active=recording_active,
        )
        # Grace scheduling in next task
        return

    # role == "other"
    redis.lrem(f"recall:meeting:{meeting_id}:participants_order", 0, participant_id)
    redis.hdel(f"recall:meeting:{meeting_id}:participants_names", participant_id)
    new_count = redis.decr(f"recall:meeting:{meeting_id}:patient_count")

    if new_count < 0:
        log.warning(
            "patient_count went negative, resetting to 0",
            meeting_id=meeting_id, count=new_count,
        )
        redis.set(f"recall:meeting:{meeting_id}:patient_count", 0)
        return

    doctor_present = bool(redis.get(f"recall:meeting:{meeting_id}:doctor_present"))
    recording_active = bool(redis.get(f"recall:meeting:{meeting_id}:recording_active"))

    log.info(
        "participant event",
        event="left", role="other", meeting_id=meeting_id,
        participant_id=participant_id, user_name=user_name,
        doctor_present=doctor_present, recording_active=recording_active,
        patient_count=new_count,
    )

    if new_count == 0 and doctor_present and recording_active:
        bot_id = _rget(redis, f"recall:meeting:{meeting_id}:bot_id")
        if bot_id:
            recall.stop_recording(bot_id)
            recall.leave_call(bot_id)
        redis.delete(f"recall:meeting:{meeting_id}:recording_active")
        redis.delete(f"recall:meeting:{meeting_id}:bot_id")
        redis.delete(f"recall:meeting:{meeting_id}:participants_order")
        redis.delete(f"recall:meeting:{meeting_id}:participants_names")
        log.info(
            "appointment session closed",
            meeting_id=meeting_id, bot_id=bot_id,
        )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v
```

Expected: the three new tests pass; all previous tests still pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/zoom_webhooks.py tests/services/zoom_webhooks_test.py
git commit -m "feat(zoom): close appointment session when patient_count drops to 0

In a PMR left open all day, each patient leaving the call ends
their appointment (stop_recording + leave_call). The next non-doctor
to arrive triggers a fresh bot/recording/appointment."
```

---

## Task 8: Schedule grace task when doctor leaves during active recording

**Files:**
- Modify: `app/services/zoom_webhooks.py`
- Test: `tests/services/zoom_webhooks_test.py`

- [ ] **Step 1: Write failing test — doctor leaves, grace task scheduled**

Add to `tests/services/zoom_webhooks_test.py`:

```python
def test_doctor_leaves_during_recording_schedules_grace(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom, monkeypatch,
):
    scheduled = {}

    class FakeAsyncResult:
        id = "celery-task-42"

    def fake_apply_async(args, countdown):
        scheduled["args"] = args
        scheduled["countdown"] = countdown
        return FakeAsyncResult()

    from app.jobs import recording_guard
    monkeypatch.setattr(
        recording_guard.stop_recording_if_absent, "apply_async", fake_apply_async
    )

    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )

    zoom_webhooks.handle_participant_left(
        _participant_left_payload(HOST_ID, "Test Doctor")
    )

    assert scheduled == {"args": [MEETING_ID], "countdown": 90}
    assert clean_redis.get(_key("grace_task_id")).decode() == "celery-task-42"
    # Recording is still active — grace hasn't expired
    assert clean_redis.get(_key("recording_active")) == b"1"
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v::test_doctor_leaves_during_recording_schedules_grace
```

Expected: FAIL — doctor-leaves branch doesn't schedule anything.

- [ ] **Step 3: Extend doctor-leaves branch in `handle_participant_left`**

In `app/services/zoom_webhooks.py`, import and add:

```python
# top of file with other imports
from app.jobs import recording_guard

GRACE_PERIOD_SECONDS = 90
```

Then in the `role == "doctor"` branch of `handle_participant_left`, replace with:

```python
    if role == "doctor":
        redis.delete(f"recall:meeting:{meeting_id}:doctor_present")
        recording_active = bool(redis.get(f"recall:meeting:{meeting_id}:recording_active"))
        log.info(
            "participant event",
            event="left", role="doctor", meeting_id=meeting_id,
            participant_id=participant_id, user_name=user_name,
            doctor_present=False, recording_active=recording_active,
        )

        if recording_active:
            task = recording_guard.queue(meeting_id, countdown=GRACE_PERIOD_SECONDS)
            redis.set(
                f"recall:meeting:{meeting_id}:grace_task_id", task.id,
                ex=timedelta(hours=24),
            )
            log.info(
                "doctor left, grace period armed",
                meeting_id=meeting_id, grace_task_id=task.id,
                countdown_s=GRACE_PERIOD_SECONDS,
            )
        return
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/zoom_webhooks.py tests/services/zoom_webhooks_test.py
git commit -m "feat(zoom): schedule grace-period stop task when doctor leaves recording

If the doctor leaves during an active recording, we wait 90s before
stopping the bot. This tolerates brief network disconnects."
```

---

## Task 9: Revoke grace task when doctor returns within 90s

**Files:**
- Modify: `app/services/zoom_webhooks.py`
- Test: `tests/services/zoom_webhooks_test.py`

- [ ] **Step 1: Write failing test — doctor returns, task revoked, recording continues**

Add to `tests/services/zoom_webhooks_test.py`:

```python
def test_doctor_returns_within_grace_revokes_task(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom, monkeypatch,
):
    revoked = []

    class FakeAsyncResult:
        id = "celery-task-99"

    from app.jobs import recording_guard
    monkeypatch.setattr(
        recording_guard.stop_recording_if_absent, "apply_async",
        lambda args, countdown: FakeAsyncResult(),
    )

    from app.celery import celery_app
    monkeypatch.setattr(
        celery_app.control, "revoke",
        lambda task_id, terminate=False: revoked.append(task_id),
    )

    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_participant_left(
        _participant_left_payload(HOST_ID, "Test Doctor")
    )
    # Doctor returns
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )

    assert revoked == ["celery-task-99"]
    assert clean_redis.get(_key("grace_task_id")) is None
    assert clean_redis.get(_key("doctor_present")) == b"1"
    assert clean_redis.get(_key("recording_active")) == b"1"
    # Only one bot should have been created across the whole flow
    assert len(mock_recall["create_bot"]) == 1
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v::test_doctor_returns_within_grace_revokes_task
```

Expected: FAIL — doctor-joins branch does not revoke the task.

- [ ] **Step 3: Extend doctor-joins branch**

In `app/services/zoom_webhooks.py`, at the top of the `role == "doctor"` branch in `handle_participant_joined`, add:

```python
    if role == "doctor":
        ttl = timedelta(hours=24)
        redis.set(f"recall:meeting:{meeting_id}:doctor_present", "1", ex=ttl)

        # Revoke any pending grace task — doctor returned in time
        grace_task_id = _rget(redis, f"recall:meeting:{meeting_id}:grace_task_id")
        if grace_task_id:
            celery_app.control.revoke(grace_task_id)
            redis.delete(f"recall:meeting:{meeting_id}:grace_task_id")
            log.info(
                "doctor returned within grace, task revoked",
                meeting_id=meeting_id, grace_task_id=grace_task_id,
            )

        log.info(
            "participant event",
            event="joined", role="doctor", meeting_id=meeting_id,
            participant_id=participant_id, user_name=user_name,
            doctor_present=True,
        )

        waiting = redis.llen(f"recall:meeting:{meeting_id}:participants_order")
        recording_active = redis.get(f"recall:meeting:{meeting_id}:recording_active")
        if waiting and not recording_active:
            first_id = redis.lindex(f"recall:meeting:{meeting_id}:participants_order", 0)
            first_id = first_id.decode() if isinstance(first_id, bytes) else first_id
            first_name = redis.hget(
                f"recall:meeting:{meeting_id}:participants_names", first_id,
            )
            first_name = first_name.decode() if isinstance(first_name, bytes) else first_name
            _start_appointment_session(meeting_id, first_id, first_name or "Unknown")
        return
```

Also add the import at top of file:

```python
from app.celery import celery_app
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/zoom_webhooks.py tests/services/zoom_webhooks_test.py
git commit -m "feat(zoom): revoke grace task when doctor returns within window

When the doctor rejoins before the 90s timeout fires, cancel the
pending stop_recording task so the recording stays uninterrupted."
```

---

## Task 10: Grace expiration — task stops and destroys bot

**Files:**
- Test: `tests/services/zoom_webhooks_test.py`

(Implementation is already done by Task 2; this task verifies the end-to-end flow using `sync_celery`.)

- [ ] **Step 1: Write failing test — grace expires, stop_recording + leave_call fire**

Add to `tests/services/zoom_webhooks_test.py`:

```python
def test_grace_period_expires_stops_and_leaves(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom, sync_celery,
):
    """With sync_celery, apply_async(countdown=...) runs immediately
    but our task still checks doctor_present. We bypass the countdown
    by calling `perform` directly, matching the spec's claim that the
    task is safe regardless of when it runs."""
    from app.services import zoom_webhooks
    from app.jobs import recording_guard

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_participant_left(
        _participant_left_payload(HOST_ID, "Test Doctor")
    )

    # Simulate the grace task firing after 90s with doctor still absent
    recording_guard.perform(MEETING_ID)

    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == [BOT_ID]
    assert clean_redis.get(_key("recording_active")) is None
    assert clean_redis.get(_key("bot_id")) is None
```

- [ ] **Step 2: Run test to verify it passes**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v::test_grace_period_expires_stops_and_leaves
```

Expected: PASS (the logic is already wired through Tasks 2, 7, 8).

- [ ] **Step 3: Commit**

```bash
git add tests/services/zoom_webhooks_test.py
git commit -m "test(zoom): verify grace expiration path stops and leaves bot"
```

---

## Task 11: Update `handle_meeting_ended` for new lifecycle

**Files:**
- Modify: `app/services/zoom_webhooks.py`
- Test: `tests/services/zoom_webhooks_test.py`

- [ ] **Step 1: Write failing tests — meeting.ended scenarios**

Add to `tests/services/zoom_webhooks_test.py`:

```python
def test_meeting_ended_with_no_bot_logs_no_recording(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    # Doctor never joins
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    assert mock_recall["create_bot"] == []
    assert mock_recall["stop_recording"] == []
    assert mock_recall["leave_call"] == []
    # All keys cleared
    for suffix in ["doctor_id", "host_id", "participants_order", "participants_names"]:
        assert clean_redis.get(_key(suffix)) is None


def test_meeting_ended_with_active_recording_stops_and_leaves(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom,
):
    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    assert mock_recall["stop_recording"] == [BOT_ID]
    assert mock_recall["leave_call"] == [BOT_ID]
    # All keys cleared
    assert clean_redis.get(_key("bot_id")) is None
    assert clean_redis.get(_key("recording_active")) is None


def test_meeting_ended_revokes_pending_grace_task(
    clean_redis, doctor_with_zoom, mock_recall, mock_zoom, monkeypatch,
):
    revoked = []

    class FakeAsyncResult:
        id = "celery-task-77"

    from app.jobs import recording_guard
    monkeypatch.setattr(
        recording_guard.stop_recording_if_absent, "apply_async",
        lambda args, countdown: FakeAsyncResult(),
    )

    from app.celery import celery_app
    monkeypatch.setattr(
        celery_app.control, "revoke",
        lambda task_id, terminate=False: revoked.append(task_id),
    )

    from app.services import zoom_webhooks

    zoom_webhooks.handle_meeting_started(_started_payload())
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_participant_joined(
        _participant_joined_payload("patient-a", "Juan")
    )
    zoom_webhooks.handle_participant_left(
        _participant_left_payload(HOST_ID, "Test Doctor")
    )
    zoom_webhooks.handle_meeting_ended(_meeting_ended_payload())

    assert "celery-task-77" in revoked
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v::test_meeting_ended_with_no_bot_logs_no_recording tests/services/zoom_webhooks_test.py::test_meeting_ended_revokes_pending_grace_task
```

Expected: FAIL — meeting-ended handler doesn't revoke grace task or call `leave_call`.

- [ ] **Step 3: Rewrite `handle_meeting_ended`**

Replace the body of `handle_meeting_ended` in `app/services/zoom_webhooks.py`:

```python
def handle_meeting_ended(payload: dict[str, Any]):
    obj = payload.get("payload", {}).get("object", {})
    meeting_id = obj.get("id")

    redis = get_redis()
    bot_id = _rget(redis, f"recall:meeting:{meeting_id}:bot_id")
    grace_task_id = _rget(redis, f"recall:meeting:{meeting_id}:grace_task_id")

    if grace_task_id:
        celery_app.control.revoke(grace_task_id)

    if bot_id:
        log.info(
            "meeting ended with active recording",
            meeting_id=meeting_id, bot_id=bot_id,
        )
        recall.stop_recording(bot_id)
        recall.leave_call(bot_id)
    else:
        log.info(
            "meeting ended without recording",
            meeting_id=meeting_id,
        )

    _clear_meeting_keys(redis, meeting_id)

    zoom_mtg = cast(
        ZoomMeeting | None,
        ZoomMeeting.where(
            cast(Any, ZoomMeeting.zoom_meeting_id) == str(meeting_id)
        ).first(),
    )
    if zoom_mtg:
        zoom_mtg.status = ZoomMeetingStatus.ended
        zoom_mtg.save()
        log.info(
            "zoom meeting status updated to ended",
            zoom_meeting_id=str(meeting_id),
        )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/services/zoom_webhooks_test.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/zoom_webhooks.py tests/services/zoom_webhooks_test.py
git commit -m "feat(zoom): meeting.ended revokes grace + calls leave_call

Ends the meeting cleanly: revoke any pending doctor-absence task,
stop the bot, tell it to leave (stops Recall billing), and clear
all per-meeting Redis keys."
```

---

## Task 12: Add Justfile recipe for per-meeting log query

**Files:**
- Modify: `infra/Justfile`

- [ ] **Step 1: Add the recipe**

Append to `infra/Justfile` after the existing `azure_logs_for_user` recipe:

```makefile
# query the timeline of events for a single Zoom meeting id
# usage: just azure_logs_for_meeting 83027877199
azure_logs_for_meeting meeting_id:
	az monitor log-analytics query -w '2a7d954b-373c-4de6-884f-c050ecc1d863' \
	  --analytics-query "union * | where TimeGenerated > ago(24h) | where ContainerAppName_s in ('cosmaneura-prod-app', 'cosmaneura-prod-worker') | where Log_meeting_id_s == '{{meeting_id}}' | project TimeGenerated, Log_event_s, Log_role_s, Log_user_name_s, Log_doctor_present_b, Log_recording_active_b, Log_patient_count_d, Log_bot_id_g, Log_appointment_id_s | order by TimeGenerated asc" \
	  -o table
```

- [ ] **Step 2: Verify the recipe parses**

```bash
just --list | grep azure_logs_for_meeting
```

Expected: recipe listed.

- [ ] **Step 3: Commit**

```bash
git add infra/Justfile
git commit -m "chore(infra): add per-meeting KQL recipe for audit timelines

\`just azure_logs_for_meeting <meeting_id>\` returns the full sequence
of participant events + state transitions for one Zoom meeting, so
investigations like the 2026-04-24 incident take one command."
```

---

## Task 13: Run full test suite and smoke-check lint

**Files:** none (validation step)

- [ ] **Step 1: Run the whole test suite**

```bash
just py_test
```

Expected: all tests pass, including any that were already there.

- [ ] **Step 2: Run the linter**

```bash
just py_lint
```

Expected: no warnings.

- [ ] **Step 3: Final manual inspection**

Re-read `app/services/zoom_webhooks.py` top to bottom. Verify:

- [ ] `BOT_NAME` still referenced by `_role_for` only
- [ ] `_is_doctor_or_bot` is removed (replaced by `_role_for`)
- [ ] No reference to the removed `doctor_name` Redis key
- [ ] `_clear_meeting_keys` includes all new suffixes
- [ ] Every `log.info("participant event", ...)` call has `role=`, `event=`, `meeting_id=`, `participant_id=`, `user_name=`

- [ ] **Step 4: Commit any lint fixes**

```bash
git add app/services/zoom_webhooks.py
git commit -m "style: satisfy lint for doctor-presence guard"
```

(Skip this step if no lint fixes were needed.)

---

## Self-Review

**Spec coverage:**
- Identity signals (participant.id for doctor, user_name for bot) — Tasks 4
- State model with new Redis keys — Tasks 3, 4, 5, 6, 7, 8, 9
- `handle_meeting_started` no bot — Task 3
- `handle_participant_joined` full branching — Tasks 4, 5, 6, 9
- `handle_participant_left` full branching — Tasks 7, 8
- Appointment lifecycle per patient — Tasks 5, 6, 7
- Grace period (90s) scheduling + revocation + expiration — Tasks 2, 8, 9, 10
- `handle_meeting_ended` update — Task 11
- `recall.leave_call` helper + `metadata` param — Task 1
- Enhanced participant-event logging — Tasks 4, 5, 6, 7, 8, 9 (inline)
- KQL Justfile recipe — Task 12
- Scenario tests (14 in spec) — Covered by Tasks 3, 4, 5, 6, 7, 8, 9, 10, 11

**Placeholder scan:** No "TBD", "TODO", or "implement later" remain in the plan body. The "Open questions" from the spec (automatic_leave timeouts, log levels) are deferred intentionally and don't block execution — they can be opened as follow-up tickets.

**Type consistency:** `_role_for` used consistently across joined/left handlers. Redis key names match across tasks. Celery task exported as `stop_recording_if_absent` (an alias for `perform`) and referenced by that name in Tasks 8, 9, 11.

---

## Execution Handoff

Plan complete and will be saved to `docs/superpowers/plans/2026-04-24-doctor-presence-guard.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
