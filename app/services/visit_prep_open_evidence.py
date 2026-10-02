"""First successful first-party invitation flow, not identity or consent.

Reuse Core's append-only content-free audit store. Creation coverage and first
open commit under the preparation lock with their surrounding operation. An
older preparation without coverage remains unknown unless an open is observed.
No tracking pixel, analytics request, patient text, bearer or new send authority.
Readers must already hold current Core scope and preparation/lineage locks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import uuid4

from typeid import TypeID
from typeid.errors import TypeIDException

from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepScheduleGeneration,
)
from sqlalchemy import func
from sqlmodel import Session, select

_COVERAGE = "visit_prep.invitation_flow_coverage_started"
_OPEN = "visit_prep.invitation_flow_opened"


class InvitationOpenEvidenceInvalid(ValueError):
    """Private fixed failure; ambiguous evidence cannot prove unopened."""


@dataclass(frozen=True, repr=False)
class InvitationOpenEvidence:
    state: Literal["unopened", "opened", "unknown"]
    opened_at: datetime | None = None

    def __repr__(self) -> str:
        return "InvitationOpenEvidence(<redacted>)"


def _invalid() -> InvitationOpenEvidenceInvalid:
    return InvitationOpenEvidenceInvalid("Visit Prep open evidence is unavailable.")


def _time(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _invalid()
    if value.utcoffset() is None:
        raise _invalid()
    return value.astimezone(UTC)


def _refs(
    prep: VisitPrep, credential: VisitPrepCredentialGeneration
) -> list[dict[str, str]]:
    return [
        {"kind": "doctor", "ref": str(prep.owner_doctor_id)},
        {"kind": "practice", "ref": str(prep.practice_id)},
        {"kind": "practice_member", "ref": str(prep.owner_practice_member_id)},
        {"kind": "visit_prep_schedule", "ref": str(credential.schedule_generation_id)},
        {"kind": "visit_prep_credential", "ref": str(credential.id)},
    ]


def _rows(
    session: Session, prep: VisitPrep, event_type: str
) -> list[AuthorityAuditEvent]:
    rows = list(
        session.exec(
            select(AuthorityAuditEvent)
            .where(
                AuthorityAuditEvent.event_type == event_type,
                AuthorityAuditEvent.resource_type == "visit_prep",
                AuthorityAuditEvent.resource_ref == str(prep.id),
            )
            .limit(2)
        ).all()
    )
    if len(rows) > 1:
        raise _invalid()
    return rows


def _validate(
    session: Session, prep: VisitPrep, row: AuthorityAuditEvent, *, at: datetime
) -> datetime:
    refs = row.platform_refs
    if not isinstance(refs, list) or len(refs) != 5:
        raise _invalid()
    credential_ref = refs[-1]
    credential_id = None
    if (
        isinstance(credential_ref, dict)
        and set(credential_ref) == {"kind", "ref"}
        and credential_ref.get("kind") == "visit_prep_credential"
        and isinstance(credential_ref.get("ref"), str)
    ):
        try:
            parsed = TypeID.from_string(credential_ref["ref"])
            if (
                parsed.prefix == "visit_prep_credential"
                and str(parsed) == credential_ref["ref"]
            ):
                credential_id = cast(TypeIDType, parsed)
        except (TypeIDException, ValueError, TypeError):
            pass
    if credential_id is None:
        raise _invalid()
    credential = session.get(VisitPrepCredentialGeneration, credential_id)
    if credential is None:
        raise _invalid()
    schedule = session.get(
        VisitPrepScheduleGeneration, credential.schedule_generation_id
    )
    occurred_at, recorded_at = _time(row.occurred_at), _time(row.recorded_at)
    coverage = row.event_type == _COVERAGE
    if (
        schedule is None
        or schedule.visit_prep_id != prep.id
        or schedule.owner_practice_member_id != prep.owner_practice_member_id
        or credential.visit_prep_id != prep.id
        or credential.owner_practice_member_id != prep.owner_practice_member_id
        or refs != _refs(prep, credential)
        or row.event_source != "platform_authoritative"
        or row.outcome != "observed"
        or row.route
        != ("visit_prep_creation" if coverage else "visit_prep_public_resume")
        or row.intent
        != ("invitation_flow_coverage_v1" if coverage else "bearer_flow_open_v1")
        or row.workload_client_id is not None
        or row.actor_id is not None
        or row.tenant_id is not None
        or row.person_id is not None
        or row.interaction_id is not None
        or row.client_event_id is None
        or row.content_sha256 is not None
        or row.request_digest is not None
        or occurred_at > recorded_at
        or recorded_at > _time(at)
        or (coverage and (credential.generation != 1 or schedule.generation != 1))
        or (
            not coverage
            and not _time(credential.issued_at)
            <= occurred_at
            < _time(credential.expires_at)
        )
    ):
        raise _invalid()
    return occurred_at


def _stage(
    session: Session,
    prep: VisitPrep,
    credential: VisitPrepCredentialGeneration,
    *,
    event_type: str,
    occurred_at: datetime | None = None,
) -> None:
    if (
        credential.visit_prep_id != prep.id
        or credential.owner_practice_member_id != prep.owner_practice_member_id
    ):
        raise _invalid()
    recorded_at = _time(session.exec(select(func.clock_timestamp())).one())
    coverage = event_type == _COVERAGE
    session.add(
        AuthorityAuditEvent(
            event_type=event_type,
            outcome="observed",
            occurred_at=recorded_at if coverage else _time(occurred_at),
            recorded_at=recorded_at,
            client_event_id=uuid4(),
            resource_type="visit_prep",
            resource_ref=str(prep.id),
            route="visit_prep_creation" if coverage else "visit_prep_public_resume",
            intent="invitation_flow_coverage_v1" if coverage else "bearer_flow_open_v1",
            platform_refs=_refs(prep, credential),
        )
    )
    session.flush()


def stage_creation_coverage(
    session: Session, *, prep: VisitPrep, credential: VisitPrepCredentialGeneration
) -> None:
    """Called only on new aggregate creation, never on replay or backfill."""
    if credential.generation != 1 or _rows(session, prep, _COVERAGE):
        raise _invalid()
    _stage(session, prep, credential, event_type=_COVERAGE)


def stage_first_flow_open(
    session: Session,
    *,
    prep: VisitPrep,
    credential: VisitPrepCredentialGeneration,
    at: datetime,
) -> None:
    """Caller successfully resolved the actual first-party interview snapshot.

    Root serialization makes exact retries/rotation preserve one aggregate
    observation. It does not identify the holder or prove that they read it.
    """
    existing = _rows(session, prep, _OPEN)
    if existing:
        _validate(session, prep, existing[0], at=at)
    else:
        _stage(session, prep, credential, event_type=_OPEN, occurred_at=at)


def read_invitation_open_evidence(
    session: Session, *, prep: VisitPrep, at: datetime
) -> InvitationOpenEvidence:
    """Non-authorizing projection under caller-owned current scope/root locks.

    One first open applies across credential rotation and rescheduling. Missing
    historical coverage is unknown; never infer unopened from nonconfirmation.
    Malformed/duplicate audit evidence fails closed instead of repairing it.
    """
    opened = _rows(session, prep, _OPEN)
    if opened:
        return InvitationOpenEvidence(
            "opened", _validate(session, prep, opened[0], at=at)
        )
    coverage = _rows(session, prep, _COVERAGE)
    if coverage:
        _validate(session, prep, coverage[0], at=at)
        # Direct confirmation/submission can precede a resume request. That
        # demonstrates interaction, but is not the recorded flow-open event.
        if prep.lifecycle_state != "invited" or prep.confirmed_at is not None:
            return InvitationOpenEvidence("unknown")
        return InvitationOpenEvidence("unopened")
    return InvitationOpenEvidence("unknown")
