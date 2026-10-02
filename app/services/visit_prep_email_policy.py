"""Dormant physician confirmation of the fixed invitation policy.

The authenticated owner explicitly confirms a displayed policy fingerprint,
deadline and current sequence. Actor/scope come from locked Core rows. A
receipt, including a historical replay, is not current permission to send.
No route, consent capture, worker or release control is connected here.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from typeid import TypeID

from app.configuration.visit_prep import VISIT_PREP_SETTINGS, VisitPrepSettings
from app.services import visit_prep_access
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_evidence import (
    EmailApprovalEvidence,
    _digest,
    _time,
    _uuid,
    email_policy_fingerprint,
    read_email_approval,
    seal_email_evidence,
)
from app.services.visit_prep_email_payload import _canonical_ref
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import (
    VisitPrepError,
    _lock_owner_scope,
    _visit_prep_session,
)

from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.visit_prep_email_authority import VisitPrepEmailApprovalEvent
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

_ERRORS = (
    VisitPrepEmailInvalid,
    VisitPrepError,
    SQLAlchemyError,
    ValueError,
    TypeError,
    AttributeError,
    OverflowError,
)


@dataclass(frozen=True, repr=False)
class EmailPolicyDecisionReceipt:
    event_id: str
    event_sequence: int
    event_kind: Literal["approved", "withdrawn"]
    policy_fingerprint: str
    expires_at: datetime | None
    recorded_at: datetime
    disposition: Literal["recorded", "replayed"]
    current_event_sequence: int

    def __repr__(self) -> str:
        return "EmailPolicyDecisionReceipt(<redacted>)"


def _authorize_owner(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    settings: VisitPrepSettings,
) -> tuple[PracticeMember, Practice, datetime]:
    owner, practice = _lock_owner_scope(
        session,
        owner_practice_member_id=owner_practice_member_id,
        owner_doctor_id=owner_doctor_id,
        practice_id=practice_id,
        now=_database_now(session),
    )
    # The first clock precedes the Practice wait. Recheck time-dependent Core
    # authority after acquiring all locks, including on receipt replays.
    now = _database_now(session)
    _lock_owner_scope(
        session,
        owner_practice_member_id=owner.id,
        owner_doctor_id=owner.doctor_id,
        practice_id=practice.id,
        now=now,
    )
    doctor = session.exec(select(Doctor).where(Doctor.id == owner.doctor_id)).one()
    decision = visit_prep_access.resolve_visit_prep_access(
        doctor, settings=settings, at=now
    )
    if (
        doctor.clerk_id != expected_clerk_subject
        or decision.can_manage is not True
        or decision.view_as_active is not False
        or decision.practice_member_id != owner.id
        or decision.practice_id != practice.id
        or decision.doctor_id != doctor.id
    ):
        raise VisitPrepEmailInvalid()
    # Access resolution performs additional reads. Do not return its earlier
    # timestamp as if those reads froze expiring BAA/seat/entitlement authority.
    now = _database_now(session)
    _lock_owner_scope(
        session,
        owner_practice_member_id=owner.id,
        owner_doctor_id=owner.doctor_id,
        practice_id=practice.id,
        now=now,
    )
    return owner, practice, now


def _record(
    session: Session,
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    operation_id: str,
    expected_event_sequence: int,
    event_kind: Literal["approved", "withdrawn"],
    policy_fingerprint: str,
    expires_at: datetime | None,
    settings: VisitPrepSettings,
) -> EmailPolicyDecisionReceipt:
    scope = {
        "owner_practice_member_id": owner_practice_member_id,
        "owner_doctor_id": owner_doctor_id,
        "practice_id": practice_id,
        "expected_clerk_subject": expected_clerk_subject,
        "settings": settings,
    }
    owner, practice, _ = _authorize_owner(session, **scope)
    latest_row = session.exec(
        select(VisitPrepEmailApprovalEvent)
        .where(VisitPrepEmailApprovalEvent.owner_practice_member_id == owner.id)
        .order_by(cast(Any, VisitPrepEmailApprovalEvent.event_sequence).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    latest = read_email_approval(latest_row) if latest_row is not None else None
    sequence = latest.event_sequence if latest is not None else 0
    prior = session.exec(
        select(VisitPrepEmailApprovalEvent)
        .where(
            VisitPrepEmailApprovalEvent.owner_practice_member_id == owner.id,
            VisitPrepEmailApprovalEvent.operation_id == UUID(operation_id),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    disposition: Literal["recorded", "replayed"] = "replayed"
    if prior is not None:
        evidence = read_email_approval(prior)
        if (
            evidence.event_sequence != expected_event_sequence + 1
            or evidence.event_kind != event_kind
            or evidence.policy_fingerprint != policy_fingerprint
            or evidence.expires_at != expires_at
        ):
            raise VisitPrepEmailInvalid()
        row = prior
    else:
        # A new approval must confirm the current text. A withdrawal may name
        # the old latest policy after a template update; that never approves
        # the replacement policy. Compare-and-set prevents stale fresh keys.
        if expected_event_sequence != sequence or not hmac.compare_digest(
            policy_fingerprint,
            email_policy_fingerprint()
            if event_kind == "approved" or latest is None
            else latest.policy_fingerprint,
        ):
            raise VisitPrepEmailInvalid()
        _, _, now = _authorize_owner(session, **scope)
        if event_kind == "approved" and (expires_at is None or expires_at <= now):
            raise VisitPrepEmailInvalid()
        evidence = EmailApprovalEvidence(
            approval_event_id=str(TypeID(prefix="visit_prep_email_approval")),
            owner_practice_member_id=str(owner.id),
            practice_id=str(practice.id),
            owner_doctor_id=str(owner.doctor_id),
            actor_doctor_id=str(owner.doctor_id),
            event_sequence=sequence + 1,
            event_kind=event_kind,
            operation_id=operation_id,
            policy_fingerprint=policy_fingerprint,
            occurred_at=now,
            expires_at=expires_at,
        )
        sealed = seal_email_evidence(evidence)
        row = VisitPrepEmailApprovalEvent(
            owner_practice_member_id=owner.id,
            practice_id=practice.id,
            owner_doctor_id=owner.doctor_id,
            event_sequence=evidence.event_sequence,
            event_kind=event_kind,
            operation_id=UUID(operation_id),
            policy_fingerprint=policy_fingerprint,
            payload_hmac_sha256=sealed.payload_hmac_sha256,
            evidence_ciphertext=sealed.ciphertext,
            recorded_at=now,
            expires_at=expires_at,
        )
        row.id = cast(TypeIDType, TypeID.from_string(evidence.approval_event_id))
        session.add(row)
        session.flush()
        session.refresh(row)
        if read_email_approval(row) != evidence:
            raise VisitPrepEmailInvalid()
        session.add(
            AuthorityAuditEvent(
                event_type=f"visit_prep.email_policy_{event_kind}",
                outcome="allowed",
                occurred_at=row.recorded_at,
                client_event_id=UUID(operation_id),
                resource_type="visit_prep_email_approval",
                resource_ref=str(row.id),
                request_digest=sealed.payload_hmac_sha256,
                route="visit_prep_email_policy",
                intent="physician_invitation_policy_confirmation",
                platform_refs=[
                    {"kind": "doctor", "ref": str(owner.doctor_id)},
                    {"kind": "practice", "ref": str(practice.id)},
                    {"kind": "practice_member", "ref": str(owner.id)},
                ],
            )
        )
        session.flush()
        sequence = row.event_sequence
        disposition = "recorded"
    _, _, checked_at = _authorize_owner(session, **scope)
    if (
        disposition == "recorded"
        and event_kind == "approved"
        and (
            row.expires_at is None
            or row.expires_at <= checked_at
            or not hmac.compare_digest(policy_fingerprint, email_policy_fingerprint())
        )
    ):
        raise VisitPrepEmailInvalid()
    receipt = EmailPolicyDecisionReceipt(
        event_id=str(row.id),
        event_sequence=row.event_sequence,
        event_kind=event_kind,
        policy_fingerprint=policy_fingerprint,
        expires_at=row.expires_at,
        recorded_at=row.recorded_at,
        disposition=disposition,
        current_event_sequence=sequence,
    )
    session.commit()
    return receipt


def record_email_policy_decision(
    *,
    owner_practice_member_id: TypeIDType,
    owner_doctor_id: TypeIDType,
    practice_id: TypeIDType,
    expected_clerk_subject: str,
    operation_id: str,
    expected_event_sequence: int,
    event_kind: Literal["approved", "withdrawn"],
    policy_fingerprint: str,
    expires_at: datetime | None,
    view_as_active: bool = False,
    settings: VisitPrepSettings = VISIT_PREP_SETTINGS,
) -> EmailPolicyDecisionReceipt:
    """Commit one attributable decision and audit, or replay its exact receipt.

    Called only by an authenticated Core owner command; a future route must
    derive these scope/subject inputs from the normalized principal, not a
    browser body. Explicit deadlines have no fabricated default. The email
    send gate may remain off while policy is prepared. Every retry reacquires
    current owner authority. Historical receipts do not restore withdrawn or
    expired approval, and do not grant recipient consent or a send capability.
    """
    result = None
    try:
        if (
            type(settings) is not VisitPrepSettings
            or settings.configuration_valid is not True
            or settings.enabled is not True
            or view_as_active is not False
            or not isinstance(expected_clerk_subject, str)
            or not 1 <= len(expected_clerk_subject) <= 255
            or not _uuid(operation_id)
            or type(expected_event_sequence) is not int
            or not 0 <= expected_event_sequence < 2_147_483_647
            or event_kind not in {"approved", "withdrawn"}
            or not _digest(policy_fingerprint)
            or not all(
                _canonical_ref(str(value), prefix)
                for value, prefix in (
                    (owner_practice_member_id, "practice_member"),
                    (owner_doctor_id, "doctor"),
                    (practice_id, "practice"),
                )
            )
        ):
            raise VisitPrepEmailInvalid()
        if event_kind == "approved":
            expires_at = _time(expires_at)
        elif expires_at is not None:
            raise VisitPrepEmailInvalid()
        with _visit_prep_session() as session:
            try:
                result = _record(
                    session,
                    owner_practice_member_id=owner_practice_member_id,
                    owner_doctor_id=owner_doctor_id,
                    practice_id=practice_id,
                    expected_clerk_subject=expected_clerk_subject,
                    operation_id=operation_id,
                    expected_event_sequence=expected_event_sequence,
                    event_kind=event_kind,
                    policy_fingerprint=policy_fingerprint,
                    expires_at=expires_at,
                    settings=settings,
                )
            except _ERRORS:
                session.rollback()
                raise
    except _ERRORS:
        pass
    if result is None:
        # SQL and crypto errors can contain private evidence. Do not carry an
        # exception chain across the domain boundary, including after commit.
        raise VisitPrepEmailInvalid()
    return result
