from app.configuration.sentry import scrub_event


def _event() -> dict:
    return {
        "request": {
            "url": "https://api.example.com/internal/v1/notes",
            "data": {"transcript": "Patient: my back hurts"},
            "cookies": {"session": "abc"},
            "headers": {
                "Authorization": "Bearer secret",
                "Cookie": "session=abc",
                "User-Agent": "test",
            },
        },
        "breadcrumbs": {
            "values": [
                {
                    "message": "prompting clinical note model",
                    "data": {"prompt": "full PHI prompt"},
                }
            ]
        },
        "exception": {"values": [{"type": "ValueError", "value": "note failed"}]},
    }


def test_scrub_drops_request_body_cookies_and_auth_headers():
    event = scrub_event(_event(), None)

    assert event is not None
    assert "data" not in event["request"]
    assert "cookies" not in event["request"]
    assert "Authorization" not in event["request"]["headers"]
    assert "Cookie" not in event["request"]["headers"]
    # non-sensitive context is preserved
    assert event["request"]["url"].endswith("/internal/v1/notes")
    assert event["request"]["headers"]["User-Agent"] == "test"


def test_scrub_drops_breadcrumb_data_but_keeps_messages():
    event = scrub_event(_event(), None)

    crumb = event["breadcrumbs"]["values"][0]
    assert "data" not in crumb
    assert crumb["message"] == "prompting clinical note model"


def test_scrub_preserves_exception_summary():
    event = scrub_event(_event(), None)

    assert event["exception"]["values"][0]["value"] == "note failed"


def test_scrub_handles_minimal_events():
    assert scrub_event({}, None) == {}
    assert scrub_event({"request": None, "breadcrumbs": None}, None) == {
        "request": None,
        "breadcrumbs": None,
    }
