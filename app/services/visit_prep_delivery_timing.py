"""Provider-neutral Visit Prep timing; never authority to enqueue or send.

Changed/adapted from CAB 01ad97536c4f's chase/scheduling.py quiet-hours and
DST helpers (Apache-2.0, docs/licenses/cab-apache-2.0.txt). Visit Prep offsets
are elapsed UTC hours before the appointment, NOT CAB's local-day cadence.
No persistence, credentials, recipient values, provider I/O or runtime wiring.

The future scheduler must load current, locked Core evidence and independently
enforce consent, STOP/suppression, scope, cohort and channel gates. A timing
candidate proves none of those. Quiet hours and spacing are explicit policy
inputs: this module introduces no default operational policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Channel = Literal["email", "sms"]
Step = Literal["booking_email", "booking_sms", "unopened_48h_sms", "unfinished_12h_sms"]
Action = Literal["candidate", "wait", "omit"]
Reason = Literal[
    "due",
    "not_due",
    "inactive",
    "completed",
    "appointment_elapsed",
    "credential_expired",
    "short_notice",
    "already_opened",
    "open_evidence_unknown",
    "already_planned",
    "channel_pending",
    "submission_uncertain",
    "booking_first",
    "later_reminder_supersedes",
    "no_permitted_window",
    "channel_history_unavailable",
]
_STEPS: tuple[tuple[Step, Channel, int | None], ...] = (
    ("booking_email", "email", None),
    ("booking_sms", "sms", None),
    ("unopened_48h_sms", "sms", 48),
    ("unfinished_12h_sms", "sms", 12),
)
_TIMEZONE = re.compile(r"^[A-Za-z][A-Za-z0-9._+-]*(/[A-Za-z0-9._+-]+)*$")
_MAX_HISTORY = 128


class DeliveryTimingInvalid(ValueError):
    """A fixed, content-free failure: timing inputs are not interpretable."""


@dataclass(frozen=True, repr=False)
class TimingSnapshot:
    """Non-authorizing facts for one preparation's current lineage.

    ``booked_at`` is when this schedule/credential invitation became current,
    not the adapter's ingestion time. ``unopened`` requires trustworthy
    first-party open evidence; absent instrumentation must supply ``unknown``.
    Lifecycle and access revocation are separate, as in Core's aggregate.
    """

    booked_at: datetime
    scheduled_at: datetime
    credential_expires_at: datetime
    timezone_name: str
    schedule_generation: int
    credential_generation: int
    lifecycle_state: Literal[
        "invited", "confirmed", "in_progress", "completed", "misdirected"
    ]
    first_party_open_state: Literal["unopened", "opened", "unknown"]
    opened_at: datetime | None = None
    completed_at: datetime | None = None
    revoked: bool = False
    appointment_cancelled: bool = False

    def __repr__(self) -> str:
        return "TimingSnapshot(<redacted>)"


@dataclass(frozen=True, repr=False)
class TimingTouch:
    """A retained outbox fact, not permission or an exactly-once guarantee.

    Any existing current-step intent prevents a new one, including failed or
    uncertain delivery. Its owned outbox must recover it. Potential submission
    timestamps in any generation impose channel spacing after rotation too.
    The caller scopes the entire history to this same preparation/recipient.
    """

    step: Step
    schedule_generation: int
    credential_generation: int
    state: Literal[
        "queued", "submitting", "accepted", "failed", "suppressed", "uncertain"
    ]
    requested_at: datetime
    submitted_at: datetime | None = None

    def __repr__(self) -> str:
        return "TimingTouch(<redacted>)"


@dataclass(frozen=True, repr=False)
class TimingDecision:
    step: Step
    channel: Channel
    action: Action
    reason: Reason
    cadence_due_at: datetime
    next_check_at: datetime | None = None

    def __repr__(self) -> str:
        return "TimingDecision(<redacted>)"


def _invalid() -> DeliveryTimingInvalid:
    return DeliveryTimingInvalid("Visit Prep delivery timing is invalid.")


def _instant(value: object) -> datetime:
    result = None
    if type(value) is datetime:
        try:
            if value.tzinfo is not None and value.utcoffset() is not None:
                result = value.astimezone(UTC)
        except (OverflowError, OSError, TypeError, ValueError):
            pass
    if result is None:
        raise _invalid()
    return result


def _zone(value: object) -> ZoneInfo:
    result = None
    if type(value) is str and len(value) <= 64 and _TIMEZONE.fullmatch(value):
        try:
            result = ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            pass
    if result is None:
        raise _invalid()
    return result


def _generation(value: object) -> bool:
    return type(value) is int and 0 < value <= 2_147_483_647


def _quiet(value: object) -> tuple[int, int]:
    if (
        type(value) is not tuple
        or len(value) != 2
        or any(type(hour) is not int or not 0 <= hour <= 23 for hour in value)
        or value[0] == value[1]
    ):
        raise _invalid()
    return value


def validate_delivery_timing_policy(
    *, quiet_hours: tuple[int, int], minimum_channel_spacing: timedelta
) -> None:
    """Validate explicit restrictions without introducing an enabled policy."""
    _quiet(quiet_hours)
    if type(minimum_channel_spacing) is not timedelta or not timedelta(
        hours=1
    ) <= minimum_channel_spacing <= timedelta(hours=48):
        raise _invalid()


def _inside_quiet(local: datetime, quiet: tuple[int, int]) -> bool:
    start, end = map(time, quiet)
    clock = local.time().replace(tzinfo=None)
    if start < end:
        return start <= clock < end
    return clock >= start or clock < end


def _end_of_quiet(local: datetime, quiet: tuple[int, int], zone: ZoneInfo) -> datetime:
    """Resolve a real quiet-end instant, never back through a repeated hour."""

    start, end = quiet
    day = local.date()
    if start > end and local.time().replace(tzinfo=None) >= time(start):
        day += timedelta(days=1)
    wall = datetime.combine(day, time(end))
    candidates = []
    for fold in (0, 1):
        represented = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        roundtrip = represented.astimezone(zone)
        if (
            roundtrip.replace(tzinfo=None) == wall
            and roundtrip.fold == fold
            and represented >= local.astimezone(UTC)
        ):
            candidates.append(represented)
    if candidates:
        return min(candidates)
    # A nonexistent boundary moves forward by the transition gap (including
    # non-hour gaps or a skipped calendar day). Recheck quiet hours afterward.
    for fold in (0, 1):
        represented = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if represented.astimezone(zone).replace(
            tzinfo=None
        ) > wall and represented >= local.astimezone(UTC):
            candidates.append(represented)
    if not candidates:
        raise _invalid()
    return min(candidates)


def next_outside_quiet_hours(
    due_at: datetime,
    *,
    as_of: datetime,
    timezone_name: str,
    quiet_hours: tuple[int, int],
) -> datetime:
    """First permitted UTC instant at/after due and current time; no I/O."""

    current = max(_instant(due_at), _instant(as_of))
    zone, quiet = _zone(timezone_name), _quiet(quiet_hours)
    result = None
    try:
        for _ in range(8):
            local = current.astimezone(zone)
            if not _inside_quiet(local, quiet):
                result = current
                break
            next_end = _end_of_quiet(local, quiet, zone)
            if next_end <= current:
                break
            current = next_end
    except (OverflowError, OSError, ValueError):
        pass
    if result is None:
        raise _invalid()
    return result


def _validate(
    snapshot: TimingSnapshot, history: tuple[TimingTouch, ...], now: datetime
) -> None:
    if type(snapshot) is not TimingSnapshot or type(history) is not tuple:
        raise _invalid()
    if (
        not _generation(snapshot.schedule_generation)
        or not _generation(snapshot.credential_generation)
        or type(snapshot.revoked) is not bool
        or type(snapshot.appointment_cancelled) is not bool
        or type(snapshot.lifecycle_state) is not str
        or snapshot.lifecycle_state
        not in {"invited", "confirmed", "in_progress", "completed", "misdirected"}
        or type(snapshot.first_party_open_state) is not str
        or snapshot.first_party_open_state not in {"unopened", "opened", "unknown"}
        or len(history) > _MAX_HISTORY
    ):
        raise _invalid()
    booked, appointment, expires = map(
        _instant,
        (snapshot.booked_at, snapshot.scheduled_at, snapshot.credential_expires_at),
    )
    if booked > now or booked >= appointment or expires <= booked:
        raise _invalid()
    if (snapshot.first_party_open_state == "opened") != (
        snapshot.opened_at is not None
    ):
        raise _invalid()
    # Rotation/reschedule preserves aggregate interaction history; an open or
    # completion may legitimately precede this generation's invitation time.
    if snapshot.opened_at is not None and _instant(snapshot.opened_at) > now:
        raise _invalid()
    if (
        snapshot.lifecycle_state == "completed"
        and snapshot.completed_at is None
        or snapshot.lifecycle_state not in {"completed", "misdirected"}
        and snapshot.completed_at is not None
        or snapshot.lifecycle_state != "invited"
        and snapshot.first_party_open_state == "unopened"
    ):
        raise _invalid()
    if snapshot.completed_at is not None and _instant(snapshot.completed_at) > now:
        raise _invalid()
    # A direct API confirmation/completion may precede the first recorded
    # resume request. Later review does not reset completion or permit sends.
    identities = set()
    for touch in history:
        if (
            type(touch) is not TimingTouch
            or type(touch.step) is not str
            or touch.step not in {step for step, _, _ in _STEPS}
            or not _generation(touch.schedule_generation)
            or not _generation(touch.credential_generation)
            or touch.schedule_generation > snapshot.schedule_generation
            or touch.credential_generation > snapshot.credential_generation
            or type(touch.state) is not str
            or touch.state
            not in {
                "queued",
                "submitting",
                "accepted",
                "failed",
                "suppressed",
                "uncertain",
            }
        ):
            raise _invalid()
        identity = (touch.step, touch.schedule_generation, touch.credential_generation)
        if identity in identities:
            raise _invalid()
        identities.add(identity)
        requested = _instant(touch.requested_at)
        if requested > now:
            raise _invalid()
        if (
            touch.state in {"submitting", "accepted", "uncertain"}
            and touch.submitted_at is None
        ):
            raise _invalid()
        if touch.state == "queued" and touch.submitted_at is not None:
            raise _invalid()
        if (
            touch.submitted_at is not None
            and not requested <= _instant(touch.submitted_at) <= now
        ):
            raise _invalid()


def plan_delivery_timing(
    snapshot: TimingSnapshot,
    *,
    now: datetime,
    quiet_hours: tuple[int, int],
    minimum_channel_spacing: timedelta,
    history: tuple[TimingTouch, ...] = (),
) -> tuple[TimingDecision, ...]:
    """Evaluate all four agreed cadence steps, without authorizing any send.

    Short-notice bookings skip thresholds already past when this invitation
    became current. After an outage, booking comes first on each channel and
    T-12 supersedes an overdue T-48, including quiet-hour collisions. A prior
    possible submission imposes spacing across old credential generations.
    Existing intents are never re-created, especially uncertain submissions.
    """

    current = _instant(now)
    _validate(snapshot, history, current)
    _zone(snapshot.timezone_name)
    validate_delivery_timing_policy(
        quiet_hours=quiet_hours, minimum_channel_spacing=minimum_channel_spacing
    )
    booked, appointment, expires = map(
        _instant,
        (snapshot.booked_at, snapshot.scheduled_at, snapshot.credential_expires_at),
    )
    decisions = []
    failed = False
    try:
        for step, channel, offset in _STEPS:
            due = booked if offset is None else appointment - timedelta(hours=offset)
            reason: Reason | None = None
            if (
                snapshot.revoked
                or snapshot.appointment_cancelled
                or snapshot.lifecycle_state == "misdirected"
            ):
                reason = "inactive"
            elif snapshot.lifecycle_state == "completed":
                reason = "completed"
            elif current >= appointment:
                reason = "appointment_elapsed"
            elif current >= expires:
                reason = "credential_expired"
            elif offset is not None and due <= booked:
                reason = "short_notice"
            elif step == "unopened_48h_sms" and (
                snapshot.first_party_open_state == "opened"
                or snapshot.lifecycle_state in {"confirmed", "in_progress"}
            ):
                reason = "already_opened"
            elif (
                step == "unopened_48h_sms"
                and snapshot.first_party_open_state == "unknown"
            ):
                reason = "open_evidence_unknown"
            elif any(
                touch.step == step
                and touch.schedule_generation == snapshot.schedule_generation
                and touch.credential_generation == snapshot.credential_generation
                for touch in history
            ):
                reason = "already_planned"
            if reason is not None:
                decisions.append(TimingDecision(step, channel, "omit", reason, due))
                continue
            channel_history = [
                touch
                for touch in history
                if ("email" if touch.step == "booking_email" else "sms") == channel
            ]
            if any(touch.state == "uncertain" for touch in channel_history):
                decisions.append(
                    TimingDecision(step, channel, "wait", "submission_uncertain", due)
                )
                continue
            if any(
                touch.state == "submitting"
                or (
                    touch.state == "queued"
                    and touch.schedule_generation == snapshot.schedule_generation
                    and touch.credential_generation == snapshot.credential_generation
                )
                for touch in channel_history
            ):
                decisions.append(
                    TimingDecision(step, channel, "wait", "channel_pending", due)
                )
                continue
            recent = [
                _instant(touch.submitted_at) + minimum_channel_spacing
                for touch in history
                if touch.submitted_at is not None
                and ("email" if touch.step == "booking_email" else "sms") == channel
            ]
            permitted = next_outside_quiet_hours(
                max((due, *recent)),
                as_of=current,
                timezone_name=snapshot.timezone_name,
                quiet_hours=quiet_hours,
            )
            if permitted >= min(appointment, expires):
                decisions.append(
                    TimingDecision(step, channel, "omit", "no_permitted_window", due)
                )
            elif offset is not None and not any(
                touch.step == "booking_sms"
                and touch.schedule_generation == snapshot.schedule_generation
                and touch.credential_generation == snapshot.credential_generation
                for touch in history
            ):
                # Re-evaluate after the booking intent is durably recorded;
                # never release booking plus an overdue reminder together.
                decisions.append(
                    TimingDecision(step, channel, "wait", "booking_first", due)
                )
            elif (
                step == "unopened_48h_sms"
                and appointment - timedelta(hours=12) <= permitted
            ):
                decisions.append(
                    TimingDecision(
                        step, channel, "omit", "later_reminder_supersedes", due
                    )
                )
            else:
                action: Action = "candidate" if permitted <= current else "wait"
                decisions.append(
                    TimingDecision(
                        step,
                        channel,
                        action,
                        "due" if action == "candidate" else "not_due",
                        due,
                        permitted,
                    )
                )
    except (OverflowError, OSError, ValueError):
        failed = True
    if failed:
        raise _invalid()
    return tuple(decisions)
