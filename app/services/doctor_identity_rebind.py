"""Audited recovery when a provider recreates their Clerk sign-in.

Clinical, practice, and legal records stay attached to the original ``Doctor``
row.  The operation only replaces its external Clerk subject after the old
subject is proven absent and the replacement's verified primary email matches.
Any empty Doctor row created by the replacement's first login is retained as a
soft-deleted tombstone rather than being destroyed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from clerk_backend_api.models import ClerkErrors

from app.configuration.clerk import clerk
from app.services.clerk_cohort import ClerkCohortIdentity

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.authority import AuthorityActorExternalIdentity, AuthorityAuditEvent
from app.models.doctor import CLERK_OBJECT_PREFIX, Doctor, DoctorRole, Terminology
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import PracticeMember
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, SQLModel, col, select

IDENTITY_REBIND_RETENTION_YEARS = 7
IDENTITY_REBIND_RESOURCE_TYPE = "doctor_identity_rebind"


class DoctorIdentityRebindError(ValueError):
    """A privacy-safe refusal that never contains an email or Clerk subject."""


@dataclass(frozen=True)
class DoctorIdentityRebindPlan:
    plan_sha256: str
    doctor_id: TypeIDType
    practice_id: TypeIDType
    provisional_doctor_id: TypeIDType | None
    pilot_grant_rebound: bool
    authority_identity_count: int
    already_applied: bool


@dataclass(frozen=True)
class DoctorIdentityRebindResult:
    plan_sha256: str
    doctor_id: TypeIDType
    changed: bool


@dataclass(frozen=True)
class _EvaluatedRebind:
    plan: DoctorIdentityRebindPlan
    doctor: Doctor
    member: PracticeMember
    provisional: Doctor | None
    pilot_grant: PilotAccessGrandfatherGrant | None
    authority_identities: tuple[AuthorityActorExternalIdentity, ...]
    receipt: PracticeAccessAudit | None


def _required(value: str, *, field: str, limit: int) -> str:
    normalized = value.strip()
    if not normalized or normalized != value or len(normalized) > limit:
        raise DoctorIdentityRebindError(f"{field} is invalid")
    return normalized


def _normalize_clerk_id(value: str) -> str:
    normalized = _required(value, field="Clerk subject", limit=255)
    if not normalized.startswith(CLERK_OBJECT_PREFIX) or any(
        character.isspace() for character in normalized
    ):
        raise DoctorIdentityRebindError("Clerk subject is invalid")
    return normalized


def _normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if (
        normalized != value.lower()
        or not normalized
        or len(normalized) > 320
        or normalized.count("@") != 1
        or not normalized.rsplit("@", 1)[0]
        or "." not in normalized.rsplit("@", 1)[1]
    ):
        raise DoctorIdentityRebindError("account email confirmation is invalid")
    return normalized


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _seven_years_after(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year + IDENTITY_REBIND_RETENTION_YEARS)
    except ValueError:
        return value.replace(
            year=value.year + IDENTITY_REBIND_RETENTION_YEARS,
            month=2,
            day=28,
        )


def _is_exact_clerk_resource_not_found(error: Exception) -> bool:
    if not isinstance(error, ClerkErrors):
        return False
    data = getattr(error, "data", None)
    errors = getattr(data, "errors", None)
    return (
        isinstance(errors, list)
        and len(errors) == 1
        and getattr(errors[0], "code", None) == "resource_not_found"
    )


def resolve_live_rebind_identity(
    *,
    stale_clerk_id: str,
    replacement_clerk_id: str,
    confirmed_email: str,
) -> ClerkCohortIdentity:
    """Prove the predecessor is gone and the replacement owns the same email."""

    stale = _normalize_clerk_id(stale_clerk_id)
    replacement = _normalize_clerk_id(replacement_clerk_id)
    email = _normalize_email(confirmed_email)
    if stale == replacement:
        raise DoctorIdentityRebindError("replacement identity must be different")

    try:
        user = clerk.users.get(user_id=replacement)
    except Exception:
        raise DoctorIdentityRebindError(
            "the replacement Clerk identity could not be verified"
        ) from None
    if (
        user is None
        or getattr(user, "id", None) != replacement
        or getattr(user, "banned", None) is not False
        or getattr(user, "locked", None) is not False
    ):
        raise DoctorIdentityRebindError(
            "the replacement Clerk identity could not be verified"
        )
    primary_id = getattr(user, "primary_email_address_id", None)
    addresses = getattr(user, "email_addresses", None)
    primary = (
        [item for item in addresses if getattr(item, "id", None) == primary_id]
        if isinstance(primary_id, str) and addresses
        else []
    )
    if (
        len(primary) != 1
        or getattr(getattr(primary[0], "verification", None), "status", None)
        != "verified"
    ):
        raise DoctorIdentityRebindError(
            "the replacement Clerk identity could not be verified"
        )
    live_email = getattr(primary[0], "email_address", None)
    if not isinstance(live_email, str) or not hmac.compare_digest(
        live_email.strip().lower(), email
    ):
        raise DoctorIdentityRebindError(
            "the replacement Clerk identity could not be verified"
        )

    try:
        predecessor = clerk.users.get(user_id=stale)
    except Exception as exc:
        if _is_exact_clerk_resource_not_found(exc):
            predecessor = None
        else:
            raise DoctorIdentityRebindError(
                "the prior Clerk identity absence could not be proven"
            ) from None
    if predecessor is not None:
        raise DoctorIdentityRebindError("the prior Clerk identity still exists")
    return ClerkCohortIdentity(clerk_id=replacement, email=email)


def _lock_subject(session: Session, subject: str) -> None:
    lock_key = int.from_bytes(
        hashlib.sha256(f"doctor-identity:{subject}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )
    session.exec(select(func.pg_advisory_xact_lock(lock_key))).one()


def _provisional_has_references(session: Session, doctor_id: TypeIDType) -> bool:
    """Fail closed if any persisted object points at the replacement shell."""

    doctor_table = SQLModel.metadata.tables[cast(str, Doctor.__tablename__)]
    for table in SQLModel.metadata.sorted_tables:
        columns = [
            foreign_key.parent
            for foreign_key in table.foreign_keys
            if foreign_key.column.table is doctor_table
            and foreign_key.column.name == "id"
        ]
        if not columns:
            continue
        count = session.exec(
            select(func.count())
            .select_from(table)
            .where(or_(*(column == doctor_id for column in columns)))
        ).one()
        if int(count) > 0:
            return True
    return False


def _tombstone_subject(doctor_id: TypeIDType, replacement_subject: str) -> str:
    digest = _sha256(f"{doctor_id}:{replacement_subject}")
    return f"user_rebound_{digest}"


def _receipt_ref(doctor_id: TypeIDType, replacement_subject: str) -> str:
    return f"doctor-identity-rebind:{doctor_id}:{_sha256(replacement_subject)[:24]}"


def _plan_sha256(
    *,
    doctor: Doctor,
    member: PracticeMember,
    stale_subject: str,
    replacement: ClerkCohortIdentity,
    provisional: Doctor | None,
    pilot_grant: PilotAccessGrandfatherGrant | None,
    authority_identities: tuple[AuthorityActorExternalIdentity, ...],
    operator_ref: str,
    reason: str,
) -> str:
    payload: dict[str, Any] = {
        "action": "doctor_clerk_identity_rebind",
        "authority_identity_ids": sorted(str(row.id) for row in authority_identities),
        "doctor_id": str(doctor.id),
        "email_sha256": _sha256(replacement.email),
        "member_id": str(member.id),
        "operator_ref": operator_ref,
        "pilot_grant_id": str(pilot_grant.id) if pilot_grant else None,
        "practice_id": str(member.practice_id),
        "provisional_doctor_id": str(provisional.id) if provisional else None,
        "reason": reason,
        "replacement_subject_sha256": _sha256(replacement.clerk_id),
        "schema_version": 1,
        "stale_subject_sha256": _sha256(stale_subject),
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return _sha256(canonical)


def _evaluate(
    session: Session,
    *,
    doctor_id: TypeIDType,
    stale_clerk_id: str,
    replacement: ClerkCohortIdentity,
    operator_ref: str,
    reason: str,
    lock: bool,
) -> _EvaluatedRebind:
    stale = _normalize_clerk_id(stale_clerk_id)
    new_subject = _normalize_clerk_id(replacement.clerk_id)
    email = _normalize_email(replacement.email)
    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    if stale == new_subject:
        raise DoctorIdentityRebindError("replacement identity must be different")
    if lock:
        for subject in sorted((stale, new_subject)):
            _lock_subject(session, subject)

    statement = select(Doctor).where(Doctor.id == doctor_id)
    if lock:
        statement = statement.with_for_update()
    doctor = cast(Doctor | None, session.exec(statement).one_or_none())
    if doctor is None or doctor.deleted_at is not None or not doctor.email:
        raise DoctorIdentityRebindError("the retained provider account is unavailable")
    if not hmac.compare_digest(doctor.email.strip().lower(), email):
        raise DoctorIdentityRebindError(
            "the verified replacement email does not match the retained account"
        )

    member_statement = select(PracticeMember).where(
        PracticeMember.doctor_id == doctor.id
    )
    if lock:
        member_statement = member_statement.with_for_update()
    member = cast(PracticeMember | None, session.exec(member_statement).one_or_none())
    if member is None or member.access_revoked_at is not None:
        raise DoctorIdentityRebindError(
            "the retained provider does not have active practice access"
        )

    receipt_ref = _receipt_ref(doctor.id, new_subject)
    receipts = cast(
        list[PracticeAccessAudit],
        list(
            session.exec(
                select(PracticeAccessAudit).where(
                    PracticeAccessAudit.practice_id == member.practice_id,
                    PracticeAccessAudit.action
                    == PracticeAccessAction.doctor_identity_rebound.value,
                    PracticeAccessAudit.document_reference == receipt_ref,
                )
            ).all()
        ),
    )
    if len(receipts) > 1:
        raise DoctorIdentityRebindError("identity recovery has conflicting audit state")
    receipt = receipts[0] if receipts else None
    if doctor.clerk_id == new_subject:
        if receipt is None or not receipt.evidence_sha256:
            raise DoctorIdentityRebindError(
                "identity recovery is missing its audit evidence"
            )
        return _EvaluatedRebind(
            plan=DoctorIdentityRebindPlan(
                plan_sha256=receipt.evidence_sha256,
                doctor_id=doctor.id,
                practice_id=member.practice_id,
                provisional_doctor_id=None,
                pilot_grant_rebound=False,
                authority_identity_count=0,
                already_applied=True,
            ),
            doctor=doctor,
            member=member,
            provisional=None,
            pilot_grant=None,
            authority_identities=(),
            receipt=receipt,
        )
    if doctor.clerk_id != stale:
        raise DoctorIdentityRebindError(
            "the retained provider is not bound to the confirmed prior identity"
        )
    if receipt is not None:
        raise DoctorIdentityRebindError("identity recovery has conflicting audit state")

    provisional_statement = select(Doctor).where(Doctor.clerk_id == new_subject)
    if lock:
        provisional_statement = provisional_statement.with_for_update()
    provisional = cast(Doctor | None, session.exec(provisional_statement).one_or_none())
    if provisional is not None:
        if (
            provisional.id == doctor.id
            or provisional.deleted_at is not None
            or provisional.email
            or provisional.role != DoctorRole.normal
            or provisional.api_key is not None
            or provisional.onboarding_completed_at is not None
            or provisional.biography not in (None, "")
            or provisional.has_natmed_subscription
            or provisional.terminology != Terminology.doctor_patient
            or _provisional_has_references(session, provisional.id)
        ):
            raise DoctorIdentityRebindError(
                "the replacement sign-in already owns application state"
            )

    email_owners = session.exec(
        select(Doctor).where(func.lower(func.btrim(Doctor.email)) == email)
    ).all()
    if len(email_owners) != 1 or email_owners[0].id != doctor.id:
        raise DoctorIdentityRebindError("the retained email ownership is ambiguous")

    grant_statement = select(PilotAccessGrandfatherGrant).where(
        PilotAccessGrandfatherGrant.revoked_at == None,  # noqa: E711
        col(PilotAccessGrandfatherGrant.clerk_id).in_([stale, new_subject]),
    )
    if lock:
        grant_statement = grant_statement.with_for_update()
    grants = cast(
        list[PilotAccessGrandfatherGrant], list(session.exec(grant_statement).all())
    )
    new_grants = [grant for grant in grants if grant.clerk_id == new_subject]
    old_grants = [grant for grant in grants if grant.clerk_id == stale]
    if new_grants or len(old_grants) > 1:
        raise DoctorIdentityRebindError("pilot authorization has conflicting state")
    pilot_grant = old_grants[0] if old_grants else None
    if pilot_grant is not None and (
        pilot_grant.doctor_id_snapshot not in (None, doctor.id)
        or not hmac.compare_digest(pilot_grant.email_snapshot, email)
        or not hmac.compare_digest(pilot_grant.email_sha256, _sha256(email))
    ):
        raise DoctorIdentityRebindError(
            "pilot authorization does not match the account"
        )

    authority_statement = select(AuthorityActorExternalIdentity).where(
        col(AuthorityActorExternalIdentity.subject).in_([stale, new_subject])
    )
    if lock:
        authority_statement = authority_statement.with_for_update()
    authority_rows = cast(
        list[AuthorityActorExternalIdentity],
        list(session.exec(authority_statement).all()),
    )
    if any(row.subject == new_subject for row in authority_rows):
        raise DoctorIdentityRebindError("replacement authority identity already exists")
    active_authority = tuple(
        row for row in authority_rows if row.subject == stale and row.revoked_at is None
    )

    plan_sha256 = _plan_sha256(
        doctor=doctor,
        member=member,
        stale_subject=stale,
        replacement=replacement,
        provisional=provisional,
        pilot_grant=pilot_grant,
        authority_identities=active_authority,
        operator_ref=operator,
        reason=explanation,
    )
    return _EvaluatedRebind(
        plan=DoctorIdentityRebindPlan(
            plan_sha256=plan_sha256,
            doctor_id=doctor.id,
            practice_id=member.practice_id,
            provisional_doctor_id=provisional.id if provisional else None,
            pilot_grant_rebound=pilot_grant is not None,
            authority_identity_count=len(active_authority),
            already_applied=False,
        ),
        doctor=doctor,
        member=member,
        provisional=provisional,
        pilot_grant=pilot_grant,
        authority_identities=active_authority,
        receipt=None,
    )


def plan_doctor_identity_rebind(
    *,
    doctor_id: TypeIDType,
    stale_clerk_id: str,
    replacement: ClerkCohortIdentity,
    operator_ref: str,
    reason: str,
    session: Session | None = None,
) -> DoctorIdentityRebindPlan:
    if session is None:
        with get_session() as managed:
            return plan_doctor_identity_rebind(
                doctor_id=doctor_id,
                stale_clerk_id=stale_clerk_id,
                replacement=replacement,
                operator_ref=operator_ref,
                reason=reason,
                session=cast(Session, managed),
            )
    return _evaluate(
        session,
        doctor_id=doctor_id,
        stale_clerk_id=stale_clerk_id,
        replacement=replacement,
        operator_ref=operator_ref,
        reason=reason,
        lock=False,
    ).plan


def rebind_doctor_identity(
    *,
    doctor_id: TypeIDType,
    stale_clerk_id: str,
    replacement: ClerkCohortIdentity,
    operator_ref: str,
    reason: str,
    confirmed_plan_sha256: str,
    session: Session | None = None,
    now: datetime | None = None,
) -> DoctorIdentityRebindResult:
    if session is None:
        with get_session() as managed:
            return rebind_doctor_identity(
                doctor_id=doctor_id,
                stale_clerk_id=stale_clerk_id,
                replacement=replacement,
                operator_ref=operator_ref,
                reason=reason,
                confirmed_plan_sha256=confirmed_plan_sha256,
                session=cast(Session, managed),
                now=now,
            )
    confirmed = confirmed_plan_sha256.strip().lower()
    if len(confirmed) != 64 or any(ch not in "0123456789abcdef" for ch in confirmed):
        raise DoctorIdentityRebindError(
            "confirmed_plan_sha256 must be a 64-character lowercase hex digest"
        )

    try:
        evaluated = _evaluate(
            session,
            doctor_id=doctor_id,
            stale_clerk_id=stale_clerk_id,
            replacement=replacement,
            operator_ref=operator_ref,
            reason=reason,
            lock=True,
        )
        if not hmac.compare_digest(evaluated.plan.plan_sha256, confirmed):
            raise DoctorIdentityRebindError(
                "confirmed plan digest does not match the current recovery plan"
            )
        if evaluated.plan.already_applied:
            session.commit()
            return DoctorIdentityRebindResult(
                plan_sha256=evaluated.plan.plan_sha256,
                doctor_id=evaluated.doctor.id,
                changed=False,
            )

        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise DoctorIdentityRebindError(
                "operation timestamp must include a timezone"
            )

        if evaluated.provisional is not None:
            evaluated.provisional.clerk_id = _tombstone_subject(
                evaluated.provisional.id,
                replacement.clerk_id,
            )
            evaluated.provisional.deleted_at = current
            session.add(evaluated.provisional)
            session.flush()

        evaluated.doctor.clerk_id = replacement.clerk_id
        session.add(evaluated.doctor)
        session.flush()

        if evaluated.pilot_grant is not None:
            old_grant = evaluated.pilot_grant
            old_grant.revoked_at = current
            old_grant.revoked_by_ref = operator_ref
            old_grant.revocation_reason = reason
            old_grant.revocation_plan_sha256 = evaluated.plan.plan_sha256
            old_grant.retain_until = _seven_years_after(current)
            session.add(old_grant)
            session.flush()
            session.add(
                PilotAccessGrandfatherGrant(
                    clerk_id=replacement.clerk_id,
                    doctor_id_snapshot=evaluated.doctor.id,
                    practice_id_snapshot=evaluated.member.practice_id,
                    email_snapshot=replacement.email,
                    email_sha256=_sha256(replacement.email),
                    source_ref=old_grant.source_ref,
                    source_sha256=old_grant.source_sha256,
                    grant_plan_sha256=evaluated.plan.plan_sha256,
                    granted_by_ref=operator_ref,
                    grant_reason=reason,
                    granted_at=current,
                )
            )

        for identity in evaluated.authority_identities:
            identity.revoked_at = current
            session.add(identity)
            session.add(
                AuthorityActorExternalIdentity(
                    actor_id=identity.actor_id,
                    issuer=identity.issuer,
                    subject=replacement.clerk_id,
                    verified_at=current,
                )
            )
            session.add(
                AuthorityAuditEvent(
                    event_source="platform_authoritative",
                    event_type="authority.provider_identity.rebound",
                    outcome="accepted",
                    occurred_at=current,
                    recorded_at=current,
                    actor_id=identity.actor_id,
                    resource_type=IDENTITY_REBIND_RESOURCE_TYPE,
                    resource_ref=f"identity-rebind:{evaluated.plan.plan_sha256}",
                    request_digest=evaluated.plan.plan_sha256,
                    platform_refs=[],
                )
            )

        session.add(
            PracticeAccessAudit(
                practice_id=evaluated.member.practice_id,
                action=PracticeAccessAction.doctor_identity_rebound.value,
                operator_ref=operator_ref,
                reason=reason,
                effective_at=current,
                document_reference=_receipt_ref(
                    evaluated.doctor.id, replacement.clerk_id
                ),
                evidence_sha256=evaluated.plan.plan_sha256,
            )
        )
        session.commit()
    except DoctorIdentityRebindError:
        session.rollback()
        raise
    except (IntegrityError, SQLAlchemyError):
        session.rollback()
        raise DoctorIdentityRebindError(
            "identity recovery database operation failed; verify state before retrying"
        ) from None

    return DoctorIdentityRebindResult(
        plan_sha256=evaluated.plan.plan_sha256,
        doctor_id=evaluated.doctor.id,
        changed=True,
    )
