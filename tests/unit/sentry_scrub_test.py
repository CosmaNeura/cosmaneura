import json

from app.configuration import sentry

SENTINEL = "patient-secret-sentinel"


def _event() -> dict:
    return {
        "request": {
            "url": f"https://api.example.com/internal/v1/notes?patient={SENTINEL}",
            "query_string": f"patient={SENTINEL}",
            "data": {"transcript": SENTINEL},
            "cookies": {"session": SENTINEL},
            "env": {"REMOTE_USER": SENTINEL},
            "headers": {
                "aUtHoRiZaTiOn": f"Bearer {SENTINEL}",
                "X-Clinical-Context": SENTINEL,
            },
        },
        "user": {"email": SENTINEL},
        "extra": {"celery-job": {"args": [SENTINEL]}},
        "tags": {"patient": SENTINEL},
        "contexts": {"trace": {"patient": SENTINEL}},
        "message": SENTINEL,
        "logentry": {"message": SENTINEL},
        "fingerprint": [SENTINEL],
        "breadcrumbs": {
            "values": [{"message": SENTINEL, "data": {"prompt": SENTINEL}}]
        },
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": SENTINEL,
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "app/services/example.py",
                                "lineno": 42,
                                "vars": {"transcript": SENTINEL},
                            }
                        ]
                    },
                }
            ]
        },
        "spans": [
            {
                "op": "http.client",
                "description": f"GET https://api.example.com/users/{SENTINEL}",
                "data": {"url": SENTINEL},
                "tags": {"provider_user": SENTINEL},
                "measurements": {SENTINEL: {"value": 1}},
                "_metrics_summary": {SENTINEL: {"count": 1}},
                "future_sdk_field": {"provider_path": SENTINEL},
            },
            {
                "op": f"http.client.{SENTINEL}",
                "description": SENTINEL,
            },
        ],
        "transaction": "POST /internal/v1/notes",
        "transaction_info": {"source": "route", "future_field": SENTINEL},
        "release": "test-release",
    }


def _assert_scrubbed(event: dict) -> None:
    serialized = json.dumps(event, sort_keys=True)
    assert SENTINEL not in serialized
    exception = event["exception"]["values"][0]
    assert exception["type"] == "ValueError"
    frame = exception["stacktrace"]["frames"][0]
    assert frame["filename"] == "app/services/example.py"
    assert frame["lineno"] == 42
    assert event["transaction"] == "POST /internal/v1/notes"
    assert event["transaction_info"] == {"source": "route"}
    assert event["release"] == "test-release"
    assert event["spans"] == [{"op": "http.client"}, {}]


def test_scrub_removes_phi_from_error_events() -> None:
    event = sentry.scrub_event(_event(), None)
    assert event is not None
    _assert_scrubbed(event)


def test_transaction_callback_uses_the_same_scrubber(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(sentry, "is_production", lambda: True)
    monkeypatch.setattr(sentry, "is_job_monitor", lambda: False)
    monkeypatch.setattr(sentry, "config", lambda *_args, **_kwargs: "configured")
    monkeypatch.setattr(sentry, "PostHogIntegration", lambda: object())
    monkeypatch.setattr(
        sentry.sentry_sdk, "init", lambda **kwargs: captured.update(kwargs)
    )

    sentry.configure_sentry()
    callback = captured["before_send_transaction"]
    event = callback(_event(), None)

    assert event is not None
    _assert_scrubbed(event)


def test_transaction_callback_filters_health_before_scrubbing(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(sentry, "is_production", lambda: True)
    monkeypatch.setattr(sentry, "is_job_monitor", lambda: False)
    monkeypatch.setattr(sentry, "config", lambda *_args, **_kwargs: "configured")
    monkeypatch.setattr(sentry, "PostHogIntegration", lambda: object())
    monkeypatch.setattr(
        sentry.sentry_sdk, "init", lambda **kwargs: captured.update(kwargs)
    )

    sentry.configure_sentry(integrations=[])

    assert (
        captured["before_send_transaction"](
            {"request": {"url": "https://api.example.com/healthcheck"}}, None
        )
        is None
    )


def test_scrub_handles_minimal_events() -> None:
    assert sentry.scrub_event({}, None) == {}
    assert sentry.scrub_event({"request": None, "breadcrumbs": None}, None) == {
        "request": None,
        "breadcrumbs": None,
    }


def test_scrub_replaces_url_or_unknown_transaction_names() -> None:
    for source in ("url", "custom", None):
        event = {
            "transaction": f"GET /external/v1/recall/zak/{SENTINEL}",
            "transaction_info": {"source": source} if source else None,
        }

        scrubbed = sentry.scrub_event(event)

        assert SENTINEL not in json.dumps(scrubbed)
        assert scrubbed["transaction"] == "<redacted transaction>"
        assert scrubbed["transaction_info"] == {"source": "custom"}
