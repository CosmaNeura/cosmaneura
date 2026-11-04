"""Terminal callbacks survive the inbox projection before dispatch.

Synthetic provider DTOs only; no provider, database or Redis calls.
"""

from datetime import UTC, datetime

import pytest

from app.services import external_webhook_inbox as inbox
from app.services import recall_webhooks, zoom_capture_status

BOT = "11111111-1111-4111-8111-111111111111"
UPDATED_AT = "2026-10-29T17:58:39.638137Z"


def payload(code, sub_code=None):
    return {
        "event": "bot." + code,
        "data": {
            "bot": {"id": BOT, "metadata": {"ignored": "synthetic-sentinel"}},
            "data": {
                "code": code,
                "sub_code": sub_code,
                "updated_at": UPDATED_AT,
                "ignored": "synthetic-sentinel",
            },
            "ignored": "synthetic-sentinel",
        },
    }


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("recording_permission_denied", "permission_denied"),
        ("call_ended", "ended"),
        ("done", "ended"),
        ("fatal", "failed"),
    ],
)
def test_inbox_projected_terminal_reaches_bound_capture(monkeypatch, code, expected):
    calls = []
    monkeypatch.setattr(
        zoom_capture_status, "ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED", True
    )
    monkeypatch.setattr(
        zoom_capture_status.zoom_capture_bindings,
        "project_terminal",
        lambda *args: calls.append(args) or True,
    )
    raw = payload(code, "zoom_local_recording_request_denied_by_host")
    projected = inbox.project_provider_payload("recall", raw["event"], raw)
    recall_webhooks.dispatch(projected["event"], projected)

    assert calls == [
        (BOT, expected, datetime.fromisoformat(UPDATED_AT).astimezone(UTC))
    ]
    assert set(projected["data"]) == {"bot", "data"}
    assert set(projected["data"]["bot"]) == {"id"}
    assert set(projected["data"]["data"]) == {"code", "sub_code", "updated_at"}
    assert "synthetic-sentinel" not in str(projected)
    assert raw["data"]["data"]["ignored"] == "synthetic-sentinel"


def test_projected_transient_denial_does_not_write_terminal(monkeypatch):
    monkeypatch.setattr(
        zoom_capture_status.zoom_capture_bindings,
        "project_terminal",
        lambda *_: pytest.fail("transient permission state must not project"),
    )
    raw = payload(
        "recording_permission_denied", "zoom_local_recording_request_disabled"
    )
    projected = inbox.project_provider_payload("recall", raw["event"], raw)
    recall_webhooks.dispatch(projected["event"], projected)


@pytest.mark.parametrize("mutation", ["missing", "code", "timestamp", "timezone"])
def test_malformed_terminal_remains_rejected_after_projection(monkeypatch, mutation):
    monkeypatch.setattr(
        zoom_capture_status.zoom_capture_bindings,
        "project_terminal",
        lambda *_: pytest.fail("malformed status must not project"),
    )
    raw = payload("done")
    if mutation == "missing":
        del raw["data"]["data"]
    elif mutation == "code":
        raw["data"]["data"]["code"] = "call_ended"
    elif mutation == "timestamp":
        raw["data"]["data"]["updated_at"] = "synthetic-sentinel"
    else:
        raw["data"]["data"]["updated_at"] = "2026-10-29T17:58:39"
    projected = inbox.project_provider_payload("recall", raw["event"], raw)
    with pytest.raises(inbox.PermanentWebhookError):
        recall_webhooks.dispatch(projected["event"], projected)


def test_nonterminal_projection_does_not_retain_status_detail():
    raw = payload("done")
    raw["event"] = "recording.done"
    projected = inbox.project_provider_payload("recall", raw["event"], raw)
    assert projected == {"event": "recording.done", "data": {"bot": {"id": BOT}}}
