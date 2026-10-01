"""Hash-bound operator correction of one existing practice's owner.

This is an administrative operation, not a product-access or BAA gate. It
changes two roles and appends metadata evidence; it never supplies entitlement,
attestation, membership, identity, or permission to process clinical records.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.doctor_access import doctor_tombstone

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import (
    MemberAccessState,
    PracticeMember,
    lock_practice_seats,
    member_access_state,
)
from app.models.practice_access_audit import PracticeAccessAudit
from sqlmodel import col, select

# The existing audit column is a bounded string, not a database enum. Keep this
# closed operator action compatible with the current deployed audit model.
ACTION = "practice_ownership_transferred"


class PracticeOwnerTransferError(ValueError):
    """A content-free transfer precondition failed; no mutation is authorized."""


@dataclass(frozen=True)
class PracticeOwnerTransferPlan:
    practice_id: Any
    previous_owner_doctor_id: Any
    new_owner_doctor_id: Any
    previous_owner_member_id: Any
    new_owner_member_id: Any
    plan_sha256: str
    already_applied: bool = False
    audit_id: Any | None = None


def _require(condition: object, message: str) -> None:
    if not condition:
        raise PracticeOwnerTransferError(message)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _email(value: str | None) -> str:
    _require(isinstance(value, str), "verified identity email is required")
    normalized = str(value).strip().lower()
    _require(
        3 <= len(normalized) <= 320
        and normalized.count("@") == 1
        and not any(c.isspace() or ord(c) < 32 for c in normalized),
        "verified identity email is invalid",
    )
    return normalized


def _state(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    values = record.model_dump(mode="json")
    return {field: values[field] for field in fields}


def transfer_practice_owner(
    *,
    practice_id: Any,
    expected_owner_subject: str,
    new_owner_subject: str,
    new_owner_email_confirmation: str,
    transfer_ref: str,
    operator_ref: str,
    reason: str,
    verify_primary_email: Callable[[str], str | None],
    apply: bool = False,
    confirm_plan_sha256: str | None = None,
    now: datetime | None = None,
) -> PracticeOwnerTransferPlan:
    """Preview by default; apply only the unchanged, explicitly confirmed plan.

    The caller supplies the existing verified-primary-email adapter, invoked
    afresh after locks. No authority is inferred from an old owner's product
    access: expired BAA/billing can be the reason operator repair is needed.
    Explicit Doctor, pilot-grant and membership tombstones still deny repair.
    """

    for subject in (expected_owner_subject, new_owner_subject):
        _require(
            re.fullmatch(r"user_[A-Za-z0-9]{1,128}", subject),
            "exact Clerk subjects are required",
        )
    _require(expected_owner_subject != new_owner_subject, "identities must differ")
    for value, pattern in (
        (transfer_ref, r"[A-Za-z0-9][A-Za-z0-9._:-]{7,95}"),
        (operator_ref, r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}"),
        (reason, r"[a-z][a-z0-9_:-]{2,95}"),
    ):
        _require(
            re.fullmatch(pattern, value), "use opaque references and a reason code"
        )
    if apply:
        _require(
            isinstance(confirm_plan_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", confirm_plan_sha256),
            "apply requires the reviewed plan hash",
        )
    current = now or datetime.now(timezone.utc)
    _require(current.tzinfo is not None, "the current time must be timezone-aware")
    confirmation = _email(new_owner_email_confirmation)
    reference = "owner-transfer:" + transfer_ref

    with get_session() as session:
        try:
            practice = lock_practice_seats(session, practice_id)
            _require(practice is not None, "exact practice was not found")
            assert practice is not None
            _require(
                practice.organization_id is None
                and not practice.negotiated_baa_required,
                "managed practice ownership requires its organization workflow",
            )
            doctors = session.exec(
                select(Doctor)
                .where(
                    col(Doctor.clerk_id).in_(
                        {expected_owner_subject, new_owner_subject}
                    )
                )
                .order_by(col(Doctor.id))
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            _require(len(doctors) == 2, "both exact existing Doctors are required")
            by_subject = {doctor.clerk_id: doctor for doctor in doctors}
            _require(len(by_subject) == 2, "identity mapping is ambiguous")
            old = by_subject[expected_owner_subject]
            new = by_subject[new_owner_subject]
            members = session.exec(
                select(PracticeMember)
                .where(
                    PracticeMember.practice_id == practice.id,
                    col(PracticeMember.doctor_id).in_({old.id, new.id}),
                )
                .order_by(col(PracticeMember.id))
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            _require(
                len(members) == 2, "both memberships must belong to the exact practice"
            )
            by_doctor = {member.doctor_id: member for member in members}
            _require(len(by_doctor) == 2, "membership mapping is ambiguous")
            old_member, new_member = by_doctor[old.id], by_doctor[new.id]
            for doctor, member in ((old, old_member), (new, new_member)):
                _require(
                    doctor_tombstone(doctor) is None,
                    "an account has an explicit access denial",
                )
                _require(
                    member_access_state(member, at=current) is MemberAccessState.active,
                    "both memberships must have active, unrevoked access",
                )

            owners = session.exec(
                select(col(PracticeMember.id))
                .where(
                    PracticeMember.practice_id == practice.id,
                    PracticeMember.role == "owner",
                )
                .limit(2)
            ).all()
            _require(len(owners) == 1, "the exact practice must have one owner")
            # Each lookup must be current, exact-subject and verified. Neither a
            # stored email nor another account's identity can authorize a transfer.
            try:
                old_email = _email(verify_primary_email(old.clerk_id))
                new_email = _email(verify_primary_email(new.clerk_id))
            except Exception:
                raise PracticeOwnerTransferError(
                    "live primary identity verification failed"
                ) from None
            _require(
                _email(old.email) == old_email
                and _email(new.email) == new_email == confirmation,
                "stored, live and confirmed identity bindings must agree",
            )
            _require(
                old_email != new_email, "owner identity overlap must be resolved first"
            )
            metadata = {
                "old_doctor": str(old.id),
                "new_doctor": str(new.id),
                "old_member": str(old_member.id),
                "new_member": str(new_member.id),
                "old_subject": old.clerk_id,
                "new_subject": new.clerk_id,
                "reason": reason,
            }
            audit_reason = _canonical(metadata)
            _require(
                len(audit_reason) <= 512, "transfer metadata exceeds the audit limit"
            )
            audits = session.exec(
                select(PracticeAccessAudit)
                .where(
                    PracticeAccessAudit.practice_id == practice.id,
                    PracticeAccessAudit.action == ACTION,
                    PracticeAccessAudit.document_reference == reference,
                )
                .limit(2)
            ).all()
            _require(len(audits) <= 1, "transfer reference has ambiguous evidence")

            def result(
                digest: str, *, audit_id: Any = None, replay: bool = False
            ) -> PracticeOwnerTransferPlan:
                return PracticeOwnerTransferPlan(
                    practice.id,
                    old.id,
                    new.id,
                    old_member.id,
                    new_member.id,
                    digest,
                    already_applied=replay,
                    audit_id=audit_id,
                )

            if audits:
                audit = audits[0]
                _require(
                    audit.operator_ref == operator_ref
                    and audit.reason == audit_reason
                    and isinstance(audit.evidence_sha256, str)
                    and re.fullmatch(r"[0-9a-f]{64}", audit.evidence_sha256)
                    and old_member.role == "workforce"
                    and new_member.role == "owner"
                    and owners[0] == new_member.id,
                    "prior transfer evidence conflicts with the request or current owner",
                )
                digest = str(audit.evidence_sha256)
                _require(
                    not apply or confirm_plan_sha256 == digest,
                    "reviewed transfer hash does not match",
                )
                return result(digest, audit_id=audit.id, replay=True)

            _require(
                old_member.role == "owner"
                and new_member.role == "workforce"
                and owners[0] == old_member.id,
                "the expected current owner or target role has changed",
            )
            member_fields = (
                "id",
                "doctor_id",
                "practice_id",
                "role",
                "seat_state",
                "access_ends_at",
                "access_revoked_at",
                "attested_at",
                "organization_roster_approval_id",
                "organization_authorized_at",
                "updated_at",
            )
            payload = {
                "schema_version": 1,
                "action": ACTION,
                "practice_id": str(practice.id),
                "practice_updated_at": _state(practice, ("updated_at",))["updated_at"],
                "metadata": metadata,
                "transfer_ref": transfer_ref,
                "operator_ref": operator_ref,
                "previous_member": _state(old_member, member_fields),
                "new_member": _state(new_member, member_fields),
                "doctors": [
                    _state(doctor, ("id", "clerk_id", "deleted_at", "updated_at"))
                    for doctor in (old, new)
                ],
                "verified_email_binding_sha256": _digest([old_email, new_email]),
            }
            digest = _digest(payload)
            if not apply:
                return result(digest)
            _require(
                confirm_plan_sha256 == digest,
                "state changed since the reviewed dry run",
            )

            old_member.role = "workforce"
            new_member.role = "owner"
            audit = PracticeAccessAudit(
                practice_id=practice.id,
                action=ACTION,
                operator_ref=operator_ref,
                reason=audit_reason,
                document_reference=reference,
                evidence_sha256=digest,
                effective_at=current,
            )
            session.add(audit)
            session.flush()
            applied_owners = session.exec(
                select(col(PracticeMember.id))
                .where(
                    PracticeMember.practice_id == practice.id,
                    PracticeMember.role == "owner",
                )
                .limit(2)
            ).all()
            _require(
                applied_owners == [new_member.id],
                "single-owner invariant was not retained",
            )
            applied = result(digest, audit_id=audit.id)
            session.commit()
            return applied
        except BaseException:
            session.rollback()
            raise
