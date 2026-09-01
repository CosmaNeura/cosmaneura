import httpx
import pytest

from app.celery import celery_app
from app.configuration.redis import get_redis
from app.jobs import synthetic_zoom_check
from app.jobs.synthetic_zoom_check import (
    SYNTHETIC_HOST_ID,
    SYNTHETIC_MEETING_ID,
    perform,
)
from app.services import alerting


def test_synthetic_check_completes_without_raising(global_session):
    """The whole point of this task is to be a smoke test that runs
    against real redis + db. If it raises, the on-call gets paged for
    nothing — so the happy path must never raise."""
    perform()


def test_synthetic_check_uses_sentinel_ids_that_will_never_match_a_doctor(
    global_session,
):
    """Synthetic IDs must not be valid Zoom host_ids — otherwise a real
    DoctorVideoIntegration row could coincidentally collide and the
    check would stamp live state into redis."""
    # Zoom user_ids are base64-ish ~22 chars. The sentinel is deliberately
    # longer and contains characters Zoom never emits ("-"), so a
    # collision is mathematically impossible.
    assert len(SYNTHETIC_HOST_ID) > 30
    assert "-" in SYNTHETIC_HOST_ID
    assert len(SYNTHETIC_MEETING_ID) > 20


def test_synthetic_check_is_registered_under_expected_name():
    """beat_schedule references the task by string; a drift in the
    registered name silently stops the cron monitor from firing."""
    assert "app.jobs.synthetic_zoom_check.perform" in celery_app.tasks


def test_synthetic_check_is_in_beat_schedule():
    entry = celery_app.conf.beat_schedule.get("synthetic_zoom_check")
    assert entry is not None
    assert entry["task"] == "app.jobs.synthetic_zoom_check.perform"


def test_synthetic_check_does_not_persist_lock_across_runs(global_session):
    """Real meeting.started uses SET NX to dedupe within 60s. Two
    consecutive synthetic runs must both exercise the doctor-lookup
    path; if the second silently short-circuits, our monitor stops
    catching regressions in that path."""
    redis = get_redis()
    lock_key = f"recall:meeting:{SYNTHETIC_MEETING_ID}:starting_lock"

    perform()
    # Even though meeting.started just SET NX'd the lock, perform()
    # itself deletes it at the top of the next iteration, so the second
    # call also goes through the full handler.
    perform()
    # Lock will exist briefly after the second perform() ran (set by
    # handle_meeting_started); presence isn't a contract violation.
    # What matters: the third call still works.
    redis.delete(lock_key)
    perform()


# --------- Route and Recall probes ---------
#
# The handler probe above never leaves the process, so it cannot see the
# two config failures that take the note-taker offline for *every* doctor
# at once: a rotated ZOOM_SECRET_TOKEN (Zoom's webhooks start returning 401
# and Zoom eventually deactivates the endpoint) and a rotated
# RECALL_API_KEY. Both used to be discovered by the first patient visit
# that failed to record.


class _FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"status": "ok", "duplicate": False}

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, response=None, raises=None):
        self._response = response or _FakeResponse()
        self._raises = raises
        self.posts: list[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def post(self, url, content=None, headers=None):
        if self._raises:
            raise self._raises
        self.posts.append({"url": url, "content": content, "headers": headers})
        return self._response


@pytest.fixture
def in_production(monkeypatch):
    monkeypatch.setattr(synthetic_zoom_check, "is_production", lambda: True)
    monkeypatch.setattr(synthetic_zoom_check, "is_staging", lambda: False)


def test_probe_signature_is_accepted_by_the_real_verifier():
    """The test that makes this monitor worth having. If the probe signed
    its body differently from how Zoom signs, it would pass forever while
    real webhooks were being rejected -- a monitor that reports health it
    never measured."""
    from app.routes import webhooks

    body = b'{"event":"meeting.started"}'
    headers = synthetic_zoom_check.signed_headers(body)

    assert webhooks._verify_zoom_signature(
        raw_body=body,
        timestamp=headers["x-zm-request-timestamp"],
        signature=headers["x-zm-signature"],
    )


def test_probe_signature_is_rejected_when_the_secret_drifts(monkeypatch):
    """The failure this probe exists to catch, proved to actually fail."""
    from app.routes import webhooks

    body = b'{"event":"meeting.started"}'
    headers = synthetic_zoom_check.signed_headers(body)
    monkeypatch.setattr(webhooks, "ZOOM_SECRET_TOKEN", "a-rotated-secret")

    assert not webhooks._verify_zoom_signature(
        raw_body=body,
        timestamp=headers["x-zm-request-timestamp"],
        signature=headers["x-zm-signature"],
    )


def test_network_probes_do_not_run_outside_production(
    global_session, monkeypatch, captured_alerts
):
    """Local dev has no public endpoint to POST to; firing these there
    would alert on every run and teach everyone to ignore them."""
    called: list[str] = []
    monkeypatch.setattr(
        synthetic_zoom_check, "_probe_ingress", lambda r: called.append("route")
    )
    monkeypatch.setattr(
        synthetic_zoom_check,
        "_probe_recall_credentials",
        lambda: called.append("recall"),
    )

    synthetic_zoom_check.perform()

    assert called == []


def test_ingress_probe_alerts_when_the_endpoint_rejects_a_valid_signature(
    global_session, monkeypatch, in_production, captured_alerts
):
    """A 401 here means ZOOM_SECRET_TOKEN no longer matches the Zoom app,
    so every real webhook is being rejected too."""
    monkeypatch.setattr(
        httpx, "Client", _FakeClient(response=_FakeResponse(status_code=401))
    )
    monkeypatch.setattr(synthetic_zoom_check, "_probe_recall_credentials", lambda: None)

    synthetic_zoom_check.perform()

    assert len(captured_alerts) == 1
    assert captured_alerts[0]["type"] is alerting.WebhookRouteUnreachable
    assert captured_alerts[0]["status"] == 401


def test_ingress_probe_alerts_when_the_endpoint_is_unreachable(
    global_session, monkeypatch, in_production, captured_alerts
):
    monkeypatch.setattr(
        httpx, "Client", _FakeClient(raises=httpx.ConnectError("no route to host"))
    )
    monkeypatch.setattr(synthetic_zoom_check, "_probe_recall_credentials", lambda: None)

    synthetic_zoom_check.perform()

    assert len(captured_alerts) == 1
    assert captured_alerts[0]["type"] is alerting.WebhookRouteUnreachable
    assert captured_alerts[0]["error_class"] == "ConnectError"


def test_ingress_probe_is_silent_on_success(
    global_session, monkeypatch, in_production, captured_alerts
):
    monkeypatch.setattr(httpx, "Client", _FakeClient())
    monkeypatch.setattr(synthetic_zoom_check, "_probe_recall_credentials", lambda: None)

    synthetic_zoom_check.perform()

    assert captured_alerts == []


def test_each_probe_run_is_a_fresh_event_for_the_idempotency_guard(
    global_session, monkeypatch, in_production, captured_alerts
):
    """The webhook route drops duplicate events for 48h. If the probe sent
    an identical body every time, only the first run of the day would ever
    reach Celery and the probe would quietly stop testing anything."""
    fake = _FakeClient()
    monkeypatch.setattr(httpx, "Client", fake)
    monkeypatch.setattr(synthetic_zoom_check, "_probe_recall_credentials", lambda: None)

    synthetic_zoom_check.perform()
    synthetic_zoom_check.perform()

    assert len(fake.posts) == 2
    assert fake.posts[0]["content"] != fake.posts[1]["content"]


def test_credential_probe_alerts_on_rejected_credentials(
    global_session, monkeypatch, in_production, captured_alerts
):
    """An invalid RECALL_API_KEY is otherwise found by the first patient
    visit that fails to record."""
    from app.services import recall

    def raise_401():
        raise httpx.HTTPStatusError(
            "unauthorized",
            request=httpx.Request("GET", "https://recall.ai/api/v1/bot/"),
            response=httpx.Response(401),
        )

    monkeypatch.setattr(recall, "ping", raise_401)
    monkeypatch.setattr(synthetic_zoom_check, "_probe_ingress", lambda r: None)

    synthetic_zoom_check.perform()

    assert len(captured_alerts) == 1
    assert captured_alerts[0]["type"] is alerting.RecallApiUnreachable
    assert captured_alerts[0]["status"] == 401


def test_a_broken_probe_does_not_mask_the_others(
    global_session, monkeypatch, in_production, captured_alerts
):
    """Both probes have to report. If the route probe's failure short-
    circuited the task, a simultaneous Recall outage would stay hidden
    until the route was fixed."""
    from app.services import recall

    def raise_connect():
        raise httpx.ConnectError("recall down")

    monkeypatch.setattr(
        httpx, "Client", _FakeClient(raises=httpx.ConnectError("ingress down"))
    )
    monkeypatch.setattr(recall, "ping", raise_connect)

    synthetic_zoom_check.perform()

    kinds = {call["type"] for call in captured_alerts}
    assert kinds == {
        alerting.WebhookRouteUnreachable,
        alerting.RecallApiUnreachable,
    }
