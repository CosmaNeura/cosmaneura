"""Real isolated Redis/PostgreSQL, synthetic provider responses, no live APIs."""

import os
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
import redis as redis_client
from typeid import TypeID

from app.services import recall, zoom_webhooks
from app.services import zoom_launch_boundary as launch

from app.models.external_webhook_delivery import ExternalWebhookDelivery
from app.models.zoom_meeting import ZoomMeeting
from sqlalchemy import create_engine, text
from sqlmodel import Session


@pytest.fixture
def fenced():
    url = os.environ.get("ZOOM_LAUNCH_TEST_REDIS_URL")
    if not url:
        pytest.skip("explicit isolated Redis test URL required")
    redis = redis_client.Redis.from_url(url, decode_responses=True)
    meeting = "synthetic-" + uuid4().hex
    prefix = f"recall:meeting:{meeting}"
    doctor = str(TypeID(prefix="doctor"))
    occurrence = uuid4().hex * 2
    redis.mset(
        {prefix + ":doctor_id": doctor, prefix + ":occurrence_digest": occurrence}
    )
    lock = redis.lock(prefix + ":lifecycle_lock", timeout=180)
    assert lock.acquire(blocking=False)
    boundary = launch.ZoomLaunchBoundary(redis, lock, meeting, occurrence, doctor)
    value = SimpleNamespace(
        redis=redis,
        lock=lock,
        boundary=boundary,
        meeting=meeting,
        prefix=prefix,
        doctor=doctor,
        occurrence=occurrence,
        bot=str(uuid4()),
        recording=str(uuid4()),
        appointment=str(TypeID(prefix="appointment")),
        patient=str(TypeID(prefix="patient")),
    )
    try:
        yield value
    finally:
        keys = list(redis.scan_iter(prefix + ":*"))
        keys += list(redis.scan_iter(f"recall:recording:{value.recording}:*"))
        if keys:
            redis.delete(*keys)
        redis.close()


def publish(f):
    f.boundary.publish_bot(f.bot)
    f.boundary.publish_recording(f.bot, f.recording, f.appointment, f.patient)


def test_real_redis_atomic_correspondence_uses_distinct_recording(fenced):
    f = fenced
    publish(f)
    assert f.redis.mget(
        [
            f"recall:recording:{f.recording}:{s}"
            for s in ("doctor_id", "appointment_id", "patient_id")
        ]
    ) == [f.doctor, f.appointment, f.patient]
    assert f.redis.get(f.prefix + ":recording_active") == "1"
    assert not f.redis.exists(f"recall:recording:{f.bot}:doctor_id")


@pytest.mark.parametrize("change", ["token", "occurrence", "doctor", "bot", "expired"])
def test_real_redis_changed_authority_never_publishes_recording(fenced, change):
    f = fenced
    f.boundary.publish_bot(f.bot)
    if change == "expired":
        f.redis.delete(f.lock.name)
    else:
        suffix = {
            "token": "lifecycle_lock",
            "occurrence": "occurrence_digest",
            "doctor": "doctor_id",
            "bot": "bot_id",
        }[change]
        f.redis.set(f.prefix + ":" + suffix, "successor", px=180000)
    with pytest.raises(ValueError):
        f.boundary.publish_recording(f.bot, f.recording, f.appointment, f.patient)
    assert not f.redis.exists(f.prefix + ":recording_active")
    assert not list(f.redis.scan_iter(f"recall:recording:{f.recording}:*"))


def test_atomic_script_catches_token_loss_after_preflight(fenced, monkeypatch):
    f = fenced
    f.boundary.publish_bot(f.bot)
    monkeypatch.setattr(
        f.boundary,
        "require",
        lambda *_: f.redis.set(f.lock.name, "successor", px=180000),
    )
    with pytest.raises(ValueError, match="publication_refused"):
        f.boundary.publish_recording(f.bot, f.recording, f.appointment, f.patient)
    assert not f.redis.exists(f.prefix + ":capture_started")
    assert not f.redis.exists(f"recall:recording:{f.recording}:doctor_id")


def test_original_deadline_is_not_extended_by_fresh_ttl(fenced):
    f = fenced
    f.boundary.deadline = time.monotonic() + 130
    f.redis.pexpire(f.lock.name, 180000)
    with pytest.raises(TimeoutError):
        f.boundary.require(125)


@pytest.fixture
def database(fenced, monkeypatch):
    url = os.environ.get("ZOOM_LAUNCH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("explicit isolated PostgreSQL test URL required")
    admin = create_engine(url)
    schema = "recall_launch_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        for table in ("zoom_meeting", "external_webhook_delivery"):
            connection.execute(
                text(
                    f'CREATE TABLE "{schema}"."{table}" (LIKE public."{table}" INCLUDING ALL)'
                )
            )
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    now = datetime.now(UTC)
    f = fenced
    with Session(engine) as session:
        meeting = ZoomMeeting(
            doctor_id=f.doctor,
            zoom_meeting_id=f.meeting,
            appointment_id=f.appointment,
            topic="Synthetic",
            start_time=now,
            duration=15,
            join_url="https://zoom.us/j/12345678901",
            capture_authorization_occurrence_digest=f.occurrence,
            capture_authorization_expires_at=now + timedelta(minutes=5),
            capture_authorization_consumed_at=now,
        )
        delivery = ExternalWebhookDelivery(
            provider="zoom",
            provider_event_digest=uuid4().hex * 2,
            event_type="internal.zoom_bot_release",
            payload_sha256=uuid4().hex * 2,
            encrypted_payload="synthetic-envelope",
            state="pending",
            received_at=now,
            verified_at=now,
            next_attempt_at=now + timedelta(minutes=3),
            payload_expires_at=now + timedelta(hours=6),
        )
        session.add_all([meeting, delivery])
        session.commit()
        delivery_id = str(delivery.id)

    @contextmanager
    def session_scope():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(launch, "get_session", session_scope)
    try:
        yield SimpleNamespace(engine=engine, delivery_id=delivery_id)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def state(db):
    with db.engine.connect() as c:
        return c.execute(
            text("SELECT state FROM external_webhook_delivery")
        ).scalar_one(), c.execute(text("SELECT status FROM zoom_meeting")).scalar_one()


def test_real_postgres_started_and_intent_retirement_are_one_transaction(
    fenced, database
):
    f = fenced
    publish(f)
    f.boundary.complete(database.delivery_id, f.meeting, f.appointment)
    assert state(database) == ("completed", "started")


@pytest.mark.parametrize(
    "change",
    ["ended", "occurrence", "appointment", "consumed", "processing", "completed"],
)
def test_real_postgres_rejects_ended_changed_or_claimed_launch_and_rolls_back(
    fenced, database, change
):
    f = fenced
    publish(f)
    with database.engine.begin() as c:
        if change in {"processing", "completed"}:
            c.execute(
                text("UPDATE external_webhook_delivery SET state=:state"),
                {"state": change},
            )
        elif change == "ended":
            c.execute(text("UPDATE zoom_meeting SET status='ended'"))
        elif change == "consumed":
            c.execute(
                text("UPDATE zoom_meeting SET capture_authorization_consumed_at=NULL")
            )
        elif change == "occurrence":
            c.execute(
                text(
                    "UPDATE zoom_meeting SET capture_authorization_occurrence_digest=:value"
                ),
                {"value": "a" * 64},
            )
        else:
            c.execute(
                text("UPDATE zoom_meeting SET appointment_id=:value"),
                {"value": str(uuid4())},
            )
    with pytest.raises(ValueError):
        f.boundary.complete(database.delivery_id, f.meeting, f.appointment)
    assert state(database) == (
        change if change in {"processing", "completed"} else "pending",
        "ended" if change == "ended" else "scheduled",
    )


def test_real_postgres_rolls_back_if_original_token_lost_before_commit(
    fenced, database, monkeypatch
):
    f = fenced
    publish(f)
    original = f.boundary.require
    calls = []

    def expire(*args):
        calls.append(1)
        if len(calls) == 3:
            f.redis.set(f.lock.name, "successor", px=180000)
        original(*args)

    monkeypatch.setattr(f.boundary, "require", expire)
    with pytest.raises(ValueError):
        f.boundary.complete(database.delivery_id, f.meeting, f.appointment)
    assert state(database) == ("pending", "scheduled")


def caller(monkeypatch, f):
    events = []
    monkeypatch.setattr(zoom_webhooks, "get_redis", lambda: f.redis)
    monkeypatch.setattr(
        zoom_webhooks.Doctor,
        "get",
        lambda **_: SimpleNamespace(email="synthetic@example.invalid"),
    )
    monkeypatch.setattr(zoom_webhooks, "doctor_access_in_force", lambda _: True)
    monkeypatch.setattr(zoom_webhooks, "zoom_bot_email_allowed", lambda _: True)
    monkeypatch.setattr(
        zoom_webhooks.zoom_service,
        "get_meeting_join_url",
        lambda *_: "https://zoom.us/j/12345678901",
    )
    monkeypatch.setattr(
        zoom_webhooks,
        "_consume_bound_appointment",
        lambda *_, **__: events.append("consumed")
        or SimpleNamespace(id=f.appointment, patient_id=f.patient),
    )
    monkeypatch.setattr(
        zoom_webhooks,
        "store_zoom_teardown_intent",
        lambda **kwargs: events.append(kwargs)
        or SimpleNamespace(delivery_id="owned-intent"),
    )
    monkeypatch.setattr(
        recall,
        "create_bot",
        lambda **kwargs: kwargs["before_mutation"]()
        or events.append("create")
        or {"id": f.bot},
    )
    monkeypatch.setattr(
        recall,
        "start_recording",
        lambda *_, **kwargs: kwargs["before_mutation"]()
        or events.append("start")
        or {"id": f.recording},
    )
    monkeypatch.setattr(
        recall,
        "_bounded_provider_json",
        lambda *_args, **_kwargs: events.append("chat"),
    )
    return events


@pytest.mark.parametrize("phase", ["lookup", "consume", "create", "start"])
def test_caller_expiry_retains_known_bot_intent_without_stale_publication(
    fenced, monkeypatch, phase
):
    f = fenced
    events = caller(monkeypatch, f)

    def expire(result):
        f.redis.delete(f.lock.name)
        return result

    if phase == "lookup":
        monkeypatch.setattr(
            zoom_webhooks.zoom_service,
            "get_meeting_join_url",
            lambda *_: expire("https://zoom.us/j/12345678901"),
        )
    elif phase == "consume":
        monkeypatch.setattr(
            zoom_webhooks,
            "_consume_bound_appointment",
            lambda *_, **__: expire(
                SimpleNamespace(id=f.appointment, patient_id=f.patient)
            ),
        )
    elif phase == "create":
        monkeypatch.setattr(recall, "create_bot", lambda **_: expire({"id": f.bot}))
    else:
        monkeypatch.setattr(
            recall, "start_recording", lambda *_, **__: expire({"id": f.recording})
        )
    with pytest.raises(ValueError):
        zoom_webhooks._start_appointment_session(
            f.meeting, "synthetic", "Synthetic", launch_lock=f.lock
        )
    assert not f.redis.exists(f.prefix + ":recording_active")
    if phase in {"create", "start"}:
        assert any(
            isinstance(event, dict)
            and event["bot_id"] == f.bot
            and event["stop_recording"]
            for event in events
        )
    else:
        assert "create" not in events and "start" not in events


def test_caller_insufficient_remaining_time_refuses_before_consumption_and_create(
    fenced, monkeypatch
):
    f = fenced
    events = caller(monkeypatch, f)
    f.redis.pexpire(f.lock.name, 129000)
    with pytest.raises(TimeoutError):
        zoom_webhooks._start_appointment_session(
            f.meeting, "synthetic", "Synthetic", launch_lock=f.lock
        )
    assert events == []


def test_intent_database_failure_retains_known_bot_without_start(fenced, monkeypatch):
    f = fenced
    events = caller(monkeypatch, f)

    def unavailable(**_):
        raise RuntimeError("synthetic database unavailable")

    monkeypatch.setattr(zoom_webhooks, "store_zoom_teardown_intent", unavailable)
    with pytest.raises(RuntimeError, match="database unavailable"):
        zoom_webhooks._start_appointment_session(
            f.meeting, "synthetic", "Synthetic", launch_lock=f.lock
        )
    assert f.redis.get(f.prefix + ":bot_id") == f.bot
    assert events == ["consumed", "create"]
    assert not f.redis.exists(f.prefix + ":recording_active")


@pytest.mark.parametrize("result", [{}, TimeoutError("synthetic uncertain")])
def test_caller_uncertain_start_keeps_original_bot_release_and_consumed_fence(
    fenced, monkeypatch, result
):
    f = fenced
    events = caller(monkeypatch, f)

    def start(*_, **__):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(recall, "start_recording", start)
    with pytest.raises((ValueError, TimeoutError)):
        zoom_webhooks._start_appointment_session(
            f.meeting, "synthetic", "Synthetic", launch_lock=f.lock
        )
    assert f.redis.get(f.prefix + ":bot_id") == f.bot
    assert events[0] == "consumed"
    assert any(isinstance(event, dict) and event["bot_id"] == f.bot for event in events)
    assert not f.redis.exists(f.prefix + ":recording_active")


def test_caller_commits_exact_ownership_before_best_effort_chat(
    fenced, database, monkeypatch
):
    f = fenced
    events = caller(monkeypatch, f)
    monkeypatch.setattr(
        zoom_webhooks,
        "store_zoom_teardown_intent",
        lambda **_: SimpleNamespace(delivery_id=database.delivery_id),
    )

    def chat(*_, **__):
        assert state(database) == ("completed", "started")
        assert (
            f.redis.get(f"recall:recording:{f.recording}:appointment_id")
            == f.appointment
        )
        events.append("chat-after-commit")
        raise TimeoutError("synthetic chat timeout")

    monkeypatch.setattr(recall, "send_chat_message", chat)
    zoom_webhooks._start_appointment_session(
        f.meeting, "synthetic", "Synthetic", launch_lock=f.lock
    )
    assert events == ["consumed", "create", "start", "chat-after-commit"]
    assert state(database) == ("completed", "started")


def test_actual_start_job_passes_original_acquired_lock(fenced, monkeypatch):
    from app.jobs import start_zoom_capture

    f = fenced
    f.redis.mset({f.prefix + ":doctor_present": "1", f.prefix + ":patient_count": "1"})
    meeting = SimpleNamespace(
        zoom_meeting_id=f.meeting,
        doctor_id=f.doctor,
        capture_authorization_occurrence_digest=f.occurrence,
        capture_authorization_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        capture_authorization_consumed_at=None,
    )
    calls = []
    monkeypatch.setattr(
        zoom_webhooks,
        "_resume_or_start_recording_locked",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    start_zoom_capture._perform_locked(cast(Any, meeting), f.redis, launch_lock=f.lock)
    assert len(calls) == 1 and calls[0][1]["launch_lock"] is f.lock


def test_concurrent_end_row_lock_cannot_be_overwritten_after_wait(fenced, database):
    import threading

    from sqlalchemy.exc import DBAPIError

    f = fenced
    publish(f)
    locked, release = threading.Event(), threading.Event()

    def end():
        with database.engine.begin() as c:
            c.execute(text("UPDATE zoom_meeting SET status='ended'"))
            locked.set()
            assert release.wait(5)

    thread = threading.Thread(target=end)
    thread.start()
    try:
        assert locked.wait(2)
        began = time.monotonic()
        with pytest.raises(DBAPIError):
            f.boundary.complete(database.delivery_id, f.meeting, f.appointment)
        assert time.monotonic() - began < 2
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and state(database) == ("pending", "ended")
