"""Visit Prep timing only: no mocked send is claimed as delivery acceptance.

CAB 01ad97536c4f quiet/DST cases adapted and extended; Apache-2.0 retained
in docs/licenses/cab-apache-2.0.txt. No CAB insurance state or day cadence.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.visit_prep_delivery_timing import (
    DeliveryTimingInvalid,
    TimingDecision,
    TimingSnapshot,
    TimingTouch,
    next_outside_quiet_hours,
    plan_delivery_timing,
)

EASTERN = "America/New_York"
QUIET = (21, 8)
SPACING = timedelta(hours=4)


def instant(value):
    return datetime.fromisoformat(value).astimezone(UTC)


BOOKED = instant("2026-10-01T12:00:00-04:00")
APPOINTMENT = instant("2026-10-05T12:00:00-04:00")


def snapshot(**changes):
    return replace(
        TimingSnapshot(
            booked_at=BOOKED,
            scheduled_at=APPOINTMENT,
            credential_expires_at=APPOINTMENT + timedelta(hours=72),
            timezone_name=EASTERN,
            schedule_generation=1,
            credential_generation=1,
            lifecycle_state="invited",
            first_party_open_state="unopened",
        ),
        **changes,
    )


def booking(*, state="accepted", submitted_at=BOOKED, **changes):
    return replace(
        TimingTouch(
            "booking_sms",
            1,
            1,
            state,
            BOOKED,
            None if state == "queued" else submitted_at,
        ),
        **changes,
    )


def plan(*, prep=None, now=BOOKED, history=(), **kwargs):
    return {
        decision.step: decision
        for decision in plan_delivery_timing(
            prep or snapshot(),
            now=now,
            quiet_hours=QUIET,
            minimum_channel_spacing=SPACING,
            history=history,
            **kwargs,
        )
    }


@pytest.mark.parametrize(
    "due,now,quiet,expected",
    [
        (
            "2026-10-01T22:00-04:00",
            "2026-10-01T22:00-04:00",
            (21, 8),
            "2026-10-02T08:00-04:00",
        ),
        (
            "2026-10-01T08:00-04:00",
            "2026-10-01T08:00-04:00",
            (21, 8),
            "2026-10-01T08:00-04:00",
        ),
        (
            "2026-10-01T21:00-04:00",
            "2026-10-01T21:00-04:00",
            (21, 8),
            "2026-10-02T08:00-04:00",
        ),
        (
            "2026-10-01T10:30-04:00",
            "2026-10-01T10:30-04:00",
            (8, 17),
            "2026-10-01T17:00-04:00",
        ),
        (
            "2026-10-01T17:00-04:00",
            "2026-10-01T17:00-04:00",
            (8, 17),
            "2026-10-01T17:00-04:00",
        ),
        # Overdue retries recheck CURRENT quiet hours, not just original due.
        (
            "2026-10-01T12:00-04:00",
            "2026-10-02T22:00-04:00",
            (21, 8),
            "2026-10-03T08:00-04:00",
        ),
        (
            "2026-03-07T22:00-05:00",
            "2026-03-07T22:00-05:00",
            (21, 8),
            "2026-03-08T08:00-04:00",
        ),
        (
            "2026-10-31T22:00-04:00",
            "2026-10-31T22:00-04:00",
            (21, 8),
            "2026-11-01T08:00-05:00",
        ),
        # A quiet-end DST gap resolves to a real time after the gap.
        (
            "2026-03-08T01:30-05:00",
            "2026-03-08T01:30-05:00",
            (0, 2),
            "2026-03-08T03:00-04:00",
        ),
        # Never return first-fold end before a retry in the SECOND fold.
        (
            "2026-11-01T00:30-04:00",
            "2026-11-01T00:30-04:00",
            (0, 1),
            "2026-11-01T01:00-04:00",
        ),
    ],
)
def test_quiet_hours_are_real_instants_never_before_due_or_now(
    due, now, quiet, expected
):
    result = next_outside_quiet_hours(
        instant(due),
        as_of=instant(now),
        timezone_name=EASTERN,
        quiet_hours=quiet,
    )
    assert result == instant(expected)
    assert result >= max(instant(due), instant(now))


def test_flow_review_after_completion_never_restarts_delivery():
    completed = snapshot(
        lifecycle_state="completed",
        completed_at=BOOKED,
        first_party_open_state="opened",
        opened_at=BOOKED + timedelta(minutes=1),
    )
    assert {
        d.reason
        for d in plan(prep=completed, now=BOOKED + timedelta(minutes=2)).values()
    } == {"completed"}


def test_second_fold_inside_quiet_selects_second_end_not_past_first_end():
    current = instant("2026-11-01T01:30-05:00")
    result = next_outside_quiet_hours(
        current,
        as_of=current,
        timezone_name=EASTERN,
        quiet_hours=(0, 2),
    )
    assert result == instant("2026-11-01T02:00-05:00")


@pytest.mark.parametrize(
    "zone,current,expected",
    [
        ("Australia/Lord_Howe", "2026-10-04T01:45+10:30", "2026-10-04T02:30+11:00"),
        ("Pacific/Apia", "2011-12-29T22:00-10:00", "2011-12-31T08:00+14:00"),
    ],
)
def test_non_hour_transition_and_skipped_calendar_day_are_real(zone, current, expected):
    now = instant(current)
    permitted = next_outside_quiet_hours(
        now,
        as_of=now,
        timezone_name=zone,
        quiet_hours=(0, 2) if zone == "Australia/Lord_Howe" else QUIET,
    )
    assert permitted == instant(expected) and permitted >= now


@pytest.mark.parametrize(
    "appointment", ["2026-03-09T10:00-04:00", "2026-11-02T10:00-05:00"]
)
def test_visit_prep_thresholds_use_elapsed_hours_not_cab_calendar_days(appointment):
    at = instant(appointment)
    booked = at - timedelta(days=7)
    prep = snapshot(
        booked_at=booked,
        scheduled_at=at,
        credential_expires_at=at + timedelta(hours=72),
    )
    decisions = plan(
        prep=prep,
        now=booked,
        history=(booking(requested_at=booked, submitted_at=booked),),
    )
    assert at - decisions["unopened_48h_sms"].cadence_due_at == timedelta(hours=48)
    assert at - decisions["unfinished_12h_sms"].cadence_due_at == timedelta(hours=12)
    expected_hour = 9 if "03-" in appointment else 11
    assert (
        decisions["unopened_48h_sms"].cadence_due_at.astimezone(ZoneInfo(EASTERN)).hour
        == expected_hour
    )


def test_booking_has_both_channels_and_exact_four_steps():
    decisions = plan()
    assert tuple(decisions) == (
        "booking_email",
        "booking_sms",
        "unopened_48h_sms",
        "unfinished_12h_sms",
    )
    assert (
        decisions["booking_email"].action
        == decisions["booking_sms"].action
        == "candidate"
    )
    assert decisions["booking_email"].channel == "email"
    assert all(decisions[step].channel == "sms" for step in tuple(decisions)[1:])
    assert decisions["unopened_48h_sms"].reason == "booking_first"


@pytest.mark.parametrize("hours", [48, 12])
def test_exact_threshold_and_one_microsecond_before(hours):
    step = "unopened_48h_sms" if hours == 48 else "unfinished_12h_sms"
    due = APPOINTMENT - timedelta(hours=hours)
    # T-12 is midnight local, so explicit quiet hours not covering midnight.
    kwargs = dict(
        quiet_hours=(1, 2), minimum_channel_spacing=SPACING, history=(booking(),)
    )
    before = {
        d.step: d
        for d in plan_delivery_timing(
            snapshot(), now=due - timedelta(microseconds=1), **kwargs
        )
    }[step]
    exact = {d.step: d for d in plan_delivery_timing(snapshot(), now=due, **kwargs)}[
        step
    ]
    assert before.action == "wait" and before.next_check_at == due
    assert exact.action == "candidate" and exact.next_check_at == due


@pytest.mark.parametrize(
    "remaining,omit48,omit12",
    [
        (60, False, False),
        (48, True, False),
        (24, True, False),
        (12, True, True),
        (2, True, True),
    ],
)
def test_short_notice_never_replays_past_thresholds(remaining, omit48, omit12):
    booked = APPOINTMENT - timedelta(hours=remaining)
    decisions = plan(prep=snapshot(booked_at=booked), now=booked)
    assert (decisions["unopened_48h_sms"].reason == "short_notice") == omit48
    assert (decisions["unfinished_12h_sms"].reason == "short_notice") == omit12
    assert decisions["booking_email"].action in {"candidate", "wait"}


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"revoked": True}, "inactive"),
        ({"appointment_cancelled": True}, "inactive"),
        (
            {
                "lifecycle_state": "completed",
                "completed_at": BOOKED,
                "first_party_open_state": "unknown",
            },
            "completed",
        ),
        (
            {"lifecycle_state": "misdirected", "first_party_open_state": "unknown"},
            "inactive",
        ),
    ],
)
def test_terminal_or_cancelled_context_omits_every_step(change, reason):
    assert {d.reason for d in plan(prep=snapshot(**change)).values()} == {reason}
    assert {d.action for d in plan(prep=snapshot(**change)).values()} == {"omit"}


def test_quarantine_after_completion_retains_history_but_omits_delivery():
    prep = snapshot(
        lifecycle_state="misdirected",
        first_party_open_state="unknown",
        completed_at=BOOKED,
    )
    assert all(d.reason == "inactive" for d in plan(prep=prep).values())


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(seconds=1)])
def test_no_step_after_appointment(delta):
    assert {d.reason for d in plan(now=APPOINTMENT + delta).values()} == {
        "appointment_elapsed"
    }


def test_credential_expiry_before_appointment_closes_every_step():
    expires = BOOKED + timedelta(hours=1)
    assert {
        d.reason
        for d in plan(
            prep=snapshot(credential_expires_at=expires), now=expires
        ).values()
    } == {"credential_expired"}


@pytest.mark.parametrize(
    "open_state,lifecycle",
    [("opened", "invited"), ("unknown", "confirmed"), ("unknown", "in_progress")],
)
def test_first_party_open_or_confirmed_progress_omits_only_unopened_reminder(
    open_state, lifecycle
):
    prep = snapshot(
        first_party_open_state=open_state,
        opened_at=BOOKED if open_state == "opened" else None,
        lifecycle_state=lifecycle,
    )
    decisions = plan(
        prep=prep, now=APPOINTMENT - timedelta(hours=11), history=(booking(),)
    )
    assert decisions["unopened_48h_sms"].reason == "already_opened"
    assert decisions["unfinished_12h_sms"].action == "wait"  # quiet until 08:00


def test_unknown_first_party_open_instrumentation_is_not_tracking_pixel_evidence():
    decisions = plan(
        prep=snapshot(first_party_open_state="unknown"),
        now=APPOINTMENT - timedelta(hours=48),
        history=(booking(),),
    )
    assert decisions["unopened_48h_sms"].reason == "open_evidence_unknown"
    assert decisions["unfinished_12h_sms"].reason == "not_due"


@pytest.mark.parametrize(
    "state", ["queued", "submitting", "accepted", "failed", "suppressed", "uncertain"]
)
def test_existing_current_intent_never_mints_second_same_step(state):
    decisions = plan(history=(booking(state=state),))
    assert decisions["booking_sms"].reason == "already_planned"
    assert decisions["booking_sms"].action == "omit"


@pytest.mark.parametrize(
    "state,reason",
    [
        ("queued", "channel_pending"),
        ("submitting", "channel_pending"),
        ("uncertain", "submission_uncertain"),
    ],
)
def test_overdue_reminder_does_not_burst_behind_unresolved_booking(state, reason):
    decisions = plan(
        now=APPOINTMENT - timedelta(hours=8), history=(booking(state=state),)
    )
    assert decisions["unfinished_12h_sms"].action == "wait"
    assert decisions["unfinished_12h_sms"].reason == reason
    # SMS state cannot block independent booking email timing.
    assert decisions["booking_email"].action == "wait"  # 04:00 quiet


def test_outage_recovery_sends_booking_before_any_overdue_reminder():
    decisions = plan(now=APPOINTMENT - timedelta(hours=4))
    assert decisions["booking_sms"].action == "candidate"
    assert decisions["unopened_48h_sms"].reason == "booking_first"
    assert decisions["unfinished_12h_sms"].reason == "booking_first"


def test_later_reminder_supersedes_earlier_when_outage_makes_both_due():
    decisions = plan(now=APPOINTMENT - timedelta(hours=4), history=(booking(),))
    assert decisions["unopened_48h_sms"].reason == "later_reminder_supersedes"
    assert decisions["unfinished_12h_sms"].action == "candidate"


def test_quiet_hours_collision_omits_t48_even_before_t12_nominal_threshold():
    now = APPOINTMENT - timedelta(hours=15)  # 21:00 the prior evening
    decisions = plan(now=now, history=(booking(),))
    assert decisions["unopened_48h_sms"].reason == "later_reminder_supersedes"
    assert decisions["unfinished_12h_sms"].action == "wait"
    assert decisions["unfinished_12h_sms"].next_check_at == APPOINTMENT - timedelta(
        hours=4
    )


def test_quiet_delay_after_appointment_omits_both_overdue_reminders():
    appointment = instant("2026-10-05T01:00-04:00")
    prep = snapshot(
        scheduled_at=appointment,
        credential_expires_at=appointment + timedelta(hours=72),
    )
    decisions = {
        d.step: d
        for d in plan_delivery_timing(
            prep,
            now=appointment - timedelta(hours=13),
            quiet_hours=(8, 7),
            minimum_channel_spacing=SPACING,
            history=(booking(),),
        )
    }
    assert decisions["unopened_48h_sms"].reason == "no_permitted_window"
    assert decisions["unfinished_12h_sms"].reason == "no_permitted_window"


def test_recent_possible_submission_defers_next_reminder_then_exact_spacing_is_due():
    now = APPOINTMENT - timedelta(hours=4)
    sent = now - timedelta(hours=2)
    decisions = plan(now=now, history=(booking(submitted_at=sent),))
    assert decisions["unfinished_12h_sms"].next_check_at == sent + SPACING
    assert decisions["unfinished_12h_sms"].action == "wait"
    assert (
        plan(now=sent + SPACING, history=(booking(submitted_at=sent),))[
            "unfinished_12h_sms"
        ].action
        == "candidate"
    )


def test_rotation_preserves_prior_channel_spacing_but_replaces_step_identity():
    now = APPOINTMENT - timedelta(hours=4)
    prior = booking(submitted_at=now - timedelta(hours=2))
    prep = snapshot(credential_generation=2, booked_at=now)
    decisions = plan(prep=prep, now=now, history=(prior,))
    assert decisions["booking_sms"].action == "wait"
    assert decisions["booking_sms"].next_check_at == prior.submitted_at + SPACING
    assert decisions["booking_email"].action == "candidate"
    assert decisions["unfinished_12h_sms"].reason == "short_notice"


def test_reschedule_recomputes_threshold_and_ignores_obsolete_queued_intent():
    at = APPOINTMENT + timedelta(days=7)
    prep = snapshot(
        scheduled_at=at,
        credential_expires_at=at + timedelta(hours=72),
        schedule_generation=2,
        credential_generation=2,
    )
    decisions = plan(prep=prep, history=(booking(state="queued"),))
    assert decisions["booking_sms"].action == "candidate"
    assert decisions["unopened_48h_sms"].cadence_due_at == at - timedelta(hours=48)


@pytest.mark.parametrize("state", ["submitting", "uncertain"])
def test_rotation_cannot_bypass_prior_unresolved_submission(state):
    decisions = plan(
        prep=snapshot(credential_generation=2), history=(booking(state=state),)
    )
    assert decisions["booking_sms"].action == "wait"


def test_rotation_does_not_erase_first_party_open_or_completed_history():
    now = BOOKED + timedelta(hours=1)
    opened = snapshot(
        booked_at=now,
        credential_generation=2,
        first_party_open_state="opened",
        opened_at=BOOKED,
        lifecycle_state="confirmed",
    )
    assert plan(prep=opened, now=now)["unopened_48h_sms"].reason == "already_opened"
    completed = replace(opened, lifecycle_state="completed", completed_at=BOOKED)
    assert {d.reason for d in plan(prep=completed, now=now).values()} == {"completed"}


def test_no_delivery_window_does_not_send_at_or_after_appointment():
    booked = instant("2026-10-05T05:00-04:00")
    appointment = instant("2026-10-05T07:00-04:00")
    decisions = plan(
        prep=snapshot(booked_at=booked, scheduled_at=appointment), now=booked
    )
    assert decisions["booking_sms"].reason == "no_permitted_window"
    assert decisions["booking_email"].reason == "no_permitted_window"


@pytest.mark.parametrize("quiet", [(8, 8), (True, 8), (-1, 8), (21, 24), [21, 8], None])
def test_invalid_quiet_configuration_is_content_free(quiet):
    with pytest.raises(DeliveryTimingInvalid):
        next_outside_quiet_hours(
            BOOKED, as_of=BOOKED, timezone_name=EASTERN, quiet_hours=quiet
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("timezone_name", "recipient-secret-not/a-zone"),
        ("timezone_name", []),
        ("schedule_generation", True),
        ("credential_generation", 0),
        ("lifecycle_state", []),
        ("first_party_open_state", []),
        ("booked_at", "recipient-secret"),
        ("booked_at", BOOKED.replace(tzinfo=None)),
        ("booked_at", APPOINTMENT),
        ("revoked", 1),
        ("opened_at", BOOKED),
        ("first_party_open_state", "opened"),
        ("completed_at", BOOKED),
    ],
)
def test_malformed_snapshot_never_echoes_or_retains_parser_error(field, value):
    with pytest.raises(DeliveryTimingInvalid) as caught:
        plan(prep=snapshot(**{field: value}))
    assert str(caught.value) == "Visit Prep delivery timing is invalid."
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "change",
    [
        {"state": []},
        {"step": []},
        {"requested_at": APPOINTMENT},
        {"submitted_at": APPOINTMENT},
        {"schedule_generation": 2},
        {"credential_generation": 2},
        {"state": "queued"},
        {"submitted_at": None},
    ],
)
def test_malformed_touch_never_becomes_a_candidate(change):
    with pytest.raises(DeliveryTimingInvalid):
        plan(history=(replace(booking(), **change),))


def test_duplicate_and_unbounded_history_are_rejected_not_truncated():
    for history in [(booking(), booking()), (booking(),) * 129]:
        with pytest.raises(DeliveryTimingInvalid):
            plan(history=history)


def test_datetime_underflow_is_fixed_and_has_no_exception_chain():
    booked = datetime(1, 1, 1, tzinfo=UTC)
    prep = snapshot(
        booked_at=booked,
        scheduled_at=booked + timedelta(days=1),
        credential_expires_at=booked + timedelta(days=4),
        timezone_name="UTC",
    )
    with pytest.raises(DeliveryTimingInvalid) as caught:
        plan(prep=prep, now=booked)
    assert str(caught.value) == "Visit Prep delivery timing is invalid."
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize(
    "spacing", [None, timedelta(0), timedelta(minutes=30), timedelta(hours=49)]
)
def test_anti_burst_spacing_must_be_explicit_and_bounded(spacing):
    with pytest.raises(DeliveryTimingInvalid):
        plan_delivery_timing(
            snapshot(), now=BOOKED, quiet_hours=QUIET, minimum_channel_spacing=spacing
        )


def test_timing_values_have_redacted_representations_and_no_send_capability():
    values = (snapshot(), booking(), next(iter(plan().values())))
    assert all(
        "2026" not in repr(value) and "New_York" not in repr(value) for value in values
    )
    assert all("<redacted>" in repr(value) for value in values)
    assert not hasattr(values[2], "automated_dispatch_eligible")
    assert not hasattr(values[2], "access_token")
    assert type(values[2]) is TimingDecision


def test_at_most_one_sms_step_is_a_candidate_at_every_recovery_hour():
    for hour in range(96):
        now = BOOKED + timedelta(hours=hour)
        for history in (
            (),
            (booking(),),
            (booking(state="queued"),),
            (booking(state="uncertain"),),
        ):
            decisions = plan(now=now, history=history).values()
            assert (
                sum(d.action == "candidate" and d.channel == "sms" for d in decisions)
                <= 1
            )


@pytest.mark.parametrize(
    "zone_name", [EASTERN, "Australia/Lord_Howe", "Pacific/Apia", "Europe/London"]
)
def test_dst_quiet_resolution_invariant_across_transition_hours(zone_name):
    zone = ZoneInfo(zone_name)
    for anchor in (
        instant("2026-03-08T00:00+00:00"),
        instant("2026-11-01T00:00+00:00"),
        instant("2011-12-29T00:00+00:00"),
    ):
        for hour in range(72):
            now = anchor + timedelta(hours=hour, minutes=30)
            for quiet in ((21, 8), (0, 2), (8, 17)):
                permitted = next_outside_quiet_hours(
                    now, as_of=now, timezone_name=zone_name, quiet_hours=quiet
                )
                assert permitted >= now
                clock = permitted.astimezone(zone).hour
                start, end = quiet
                assert not (
                    start <= clock < end
                    if start < end
                    else clock >= start or clock < end
                )
