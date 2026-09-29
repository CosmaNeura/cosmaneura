"""The durable Stripe event inbox, on the paths that run unattended.

`process_stripe_event_inbox` is what a Celery worker runs against a verified
Stripe event. Its whole failure branch — the attempts counter, the truncated
error, the exponential backoff, the guard against a worker that already finished
the same row — had no test. Neither did the sweep that repairs quantity
obligations after a process death.

Nothing here reaches Stripe: `apply_stripe_event` is stubbed, because the
subject is the inbox's own bookkeeping.
"""

import secrets
from datetime import datetime, timedelta, timezone

import pytest

from app.configuration import stripe as stripe_config
from app.jobs import stripe_billing as stripe_billing_job
from app.jobs import stripe_events as stripe_events_job
from app.services import stripe_billing
from app.services.public_entitlements import lock_practice_seats

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember, PracticeRole
from app.models.stripe_event_inbox import StripeEventInbox


def _inbox(**overrides) -> StripeEventInbox:
    fields = {
        "event_id": f"evt_{datetime.now(timezone.utc).timestamp()}",
        "event_type": "customer.subscription.updated",
        "payload_json": '{"id": "evt_1", "type": "customer.subscription.updated"}',
        "state": "pending",
        "attempts": 0,
    }
    fields.update(overrides)
    return StripeEventInbox(**fields).save()


def _reload(inbox: StripeEventInbox) -> StripeEventInbox:
    fresh = StripeEventInbox.get(id=inbox.id)
    assert fresh
    return fresh


def test_a_processed_row_is_not_applied_a_second_time(monkeypatch):
    """The sweep and an immediate publish can both arrive for one event."""

    applied: list[dict] = []
    monkeypatch.setattr(
        stripe_billing, "apply_stripe_event", lambda event: applied.append(event)
    )
    inbox = _inbox(state="processed")

    assert stripe_billing.process_stripe_event_inbox(inbox.id) is True
    assert applied == []


def test_success_clears_the_retry_state_and_blanks_the_stored_payload(monkeypatch):
    """The payload is Stripe's copy of billing data; it is not kept after use."""

    monkeypatch.setattr(stripe_billing, "apply_stripe_event", lambda _event: True)
    inbox = _inbox(attempts=2, last_error="earlier failure")

    assert stripe_billing.process_stripe_event_inbox(inbox.id) is True

    stored = _reload(inbox)
    assert stored.state == "processed"
    assert stored.last_error is None
    assert stored.next_attempt_at is None
    assert stored.processed_at is not None
    assert stored.payload_json == "{}"


def test_a_failure_records_the_reason_and_reschedules_rather_than_losing_the_event(
    monkeypatch,
):
    def explode(_event):
        raise RuntimeError("subscription lookup failed")

    monkeypatch.setattr(stripe_billing, "apply_stripe_event", explode)
    inbox = _inbox()
    before = datetime.now(timezone.utc)

    with pytest.raises(RuntimeError):
        stripe_billing.process_stripe_event_inbox(inbox.id)

    stored = _reload(inbox)
    assert stored.state == "pending"
    assert stored.attempts == 1
    assert stored.last_error == "subscription lookup failed"
    assert stored.processed_at is None
    assert stored.next_attempt_at is not None
    assert stored.next_attempt_at > before


def test_the_failure_is_reraised_so_the_worker_does_not_report_success(monkeypatch):
    monkeypatch.setattr(
        stripe_billing,
        "apply_stripe_event",
        lambda _event: (_ for _ in ()).throw(ValueError("bad state")),
    )
    inbox = _inbox()

    with pytest.raises(ValueError):
        stripe_billing.process_stripe_event_inbox(inbox.id)


@pytest.mark.parametrize(
    ("prior_attempts", "expected_minutes"),
    [(0, 2), (1, 4), (2, 8), (5, 60), (9, 60)],
)
def test_backoff_doubles_and_then_holds_at_an_hour(
    monkeypatch, prior_attempts, expected_minutes
):
    """Unbounded doubling would push a stuck event past any useful horizon."""

    monkeypatch.setattr(
        stripe_billing,
        "apply_stripe_event",
        lambda _event: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    inbox = _inbox(attempts=prior_attempts)
    before = datetime.now(timezone.utc)

    with pytest.raises(RuntimeError):
        stripe_billing.process_stripe_event_inbox(inbox.id)

    stored = _reload(inbox)
    assert stored.next_attempt_at is not None
    delay = stored.next_attempt_at - before
    assert timedelta(minutes=expected_minutes) - timedelta(seconds=30) <= delay
    assert delay <= timedelta(minutes=expected_minutes) + timedelta(seconds=30)


def test_a_long_error_is_truncated_to_the_column_width(monkeypatch):
    """A driver traceback can exceed the column and would fail the write."""

    monkeypatch.setattr(
        stripe_billing,
        "apply_stripe_event",
        lambda _event: (_ for _ in ()).throw(RuntimeError("x" * 900)),
    )
    inbox = _inbox()

    with pytest.raises(RuntimeError):
        stripe_billing.process_stripe_event_inbox(inbox.id)

    last_error = _reload(inbox).last_error
    assert last_error is not None
    assert len(last_error) == 512


def test_an_error_with_no_message_still_records_something_useful(monkeypatch):
    class Silent(Exception):
        pass

    monkeypatch.setattr(
        stripe_billing,
        "apply_stripe_event",
        lambda _event: (_ for _ in ()).throw(Silent()),
    )
    inbox = _inbox()

    with pytest.raises(Silent):
        stripe_billing.process_stripe_event_inbox(inbox.id)

    assert _reload(inbox).last_error == "Silent"


def test_a_row_another_worker_finished_first_is_not_reopened(monkeypatch):
    """Two workers can race one event; the loser must not undo the winner."""

    inbox = _inbox()

    def finish_then_fail(_event):
        winner = StripeEventInbox.get(id=inbox.id)
        assert winner
        winner.state = "processed"
        winner.processed_at = datetime.now(timezone.utc)
        winner.save()
        raise RuntimeError("this worker lost the race")

    monkeypatch.setattr(stripe_billing, "apply_stripe_event", finish_then_fail)

    assert stripe_billing.process_stripe_event_inbox(inbox.id) is True

    stored = _reload(inbox)
    assert stored.state == "processed"
    assert stored.attempts == 0
    assert stored.last_error is None


def _bound_practice(subscription_id: str) -> Practice:
    """A practice with a Stripe subscription bound to it, and one attested owner."""

    practice = Practice(
        legal_name="Sweep Test Practice, PLLC",
        join_code=f"S{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        origin_channel="direct",
        stripe_subscription_id=subscription_id,
    ).save()
    owner = Doctor(
        clerk_id=f"user_sweep_{secrets.token_hex(8)}",
        email=f"sweep-{secrets.token_hex(4)}@example.com",
        biography="",
    ).save()
    with get_session() as session:
        lock_practice_seats(session, practice.id)
        session.add(
            PracticeMember(
                practice_id=practice.id,
                doctor_id=owner.id,
                role=PracticeRole.owner.value,
                attested_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    return practice


def test_the_sweep_covers_every_bound_subscription_not_only_flagged_ones(monkeypatch):
    """The fail-safe for a process death between membership commit and flagging.

    A sweep that trusted `stripe_quantity_sync_required` would skip exactly the
    practice whose flag was never written.
    """

    flagged = _bound_practice("sub_flagged")
    flagged.stripe_quantity_sync_required = True
    flagged.save()
    unflagged = _bound_practice("sub_unflagged")
    _unbound = Practice(
        legal_name="No Subscription, PLLC",
        join_code=f"N{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        origin_channel="direct",
    ).save()

    swept: list[str] = []

    def record(practice) -> bool:
        swept.append(practice.stripe_subscription_id)
        return True

    monkeypatch.setattr(stripe_billing, "sync_subscription_quantity", record)

    succeeded, failed = stripe_billing.reconcile_pending_quantity_syncs()

    assert set(swept) == {"sub_flagged", "sub_unflagged"}
    assert (succeeded, failed) == (2, 0)
    assert unflagged.stripe_subscription_id in swept


def test_the_sweep_reports_failures_without_abandoning_the_rest(monkeypatch):
    _bound_practice("sub_ok")
    _bound_practice("sub_broken")

    monkeypatch.setattr(
        stripe_billing,
        "sync_subscription_quantity",
        lambda practice: practice.stripe_subscription_id != "sub_broken",
    )

    succeeded, failed = stripe_billing.reconcile_pending_quantity_syncs()

    assert (succeeded, failed) == (1, 1)


def test_a_sweep_with_nothing_bound_reports_no_work(monkeypatch):
    monkeypatch.setattr(
        stripe_billing,
        "sync_subscription_quantity",
        lambda _practice: (_ for _ in ()).throw(
            AssertionError("nothing should be swept")
        ),
    )

    assert stripe_billing.reconcile_pending_quantity_syncs() == (0, 0)


def test_the_scheduled_tasks_report_counts_in_the_shape_callers_read(monkeypatch):
    """The dict keys are the contract a monitor or alert reads, not incidental."""

    # Patched on the job modules, not the service: each does
    # `from app.services.stripe_billing import ...`, which binds the function
    # into its own namespace at import time.
    monkeypatch.setattr(
        stripe_billing_job, "reconcile_pending_quantity_syncs", lambda: (3, 1)
    )
    monkeypatch.setattr(
        stripe_events_job, "reconcile_pending_stripe_events", lambda: (5, 2)
    )

    assert stripe_billing_job.perform() == {"succeeded": 3, "failed": 1}
    assert stripe_events_job.perform() == {"succeeded": 5, "failed": 2}


def test_the_event_task_passes_its_inbox_id_straight_through(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(
        stripe_events_job,
        "process_stripe_event_inbox",
        lambda inbox_id: seen.append(inbox_id) or True,
    )

    assert stripe_events_job.process("stripe_event_inbox_123") is True
    assert seen == ["stripe_event_inbox_123"]


def test_an_event_on_an_unaccepted_api_version_is_refused(monkeypatch):
    """The pin is the contract; an unrecognised shape must not reach the inbox."""

    monkeypatch.setattr(
        stripe_config,
        "ACCEPTED_EVENT_API_VERSIONS",
        frozenset({"2025-09-30.clover"}),
    )

    with pytest.raises(stripe_billing.StripeStateInvalid):
        stripe_billing.validate_stripe_event_envelope(
            {
                "id": "evt_version",
                "type": "customer.subscription.updated",
                "api_version": "2026-08-26.dahlia",
                "livemode": False,
                "created": 1700000000,
                "data": {"object": {}},
            }
        )


def test_a_migration_window_accepts_both_versions(monkeypatch):
    """Widening the set is what lets the dashboard and the deploy flip separately.

    Without it the two are one atomic change, and every billing event arriving
    in the gap is refused before it is ever persisted.
    """

    monkeypatch.setattr(
        stripe_config,
        "ACCEPTED_EVENT_API_VERSIONS",
        frozenset({"2025-09-30.clover", "2026-08-26.dahlia"}),
    )

    for version in ("2025-09-30.clover", "2026-08-26.dahlia"):
        event = {
            "id": f"evt_{version}",
            "type": "customer.subscription.updated",
            "api_version": version,
            "livemode": False,
            "created": 1700000000,
            "data": {"object": {}},
        }
        # Past the version gate; anything after it is a different concern.
        with pytest.raises(stripe_billing.StripeStateInvalid) as raised:
            stripe_billing.validate_stripe_event_envelope(event)
        assert "API version" not in str(raised.value)


def test_the_accepted_set_contains_the_outbound_pin(monkeypatch):
    """A set that omitted it would refuse the events this deployment causes."""

    assert (
        stripe_config.SUPPORTED_STRIPE_API_VERSION
        in stripe_config.ACCEPTED_EVENT_API_VERSIONS
    )
