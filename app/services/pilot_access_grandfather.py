"""Atomic service core for the fixed legacy-pilot access cohort.

This module never assigns a tenant or changes practice billing, BAA, or admin
state.  It records a reviewed Clerk export as durable per-account evidence;
routes may later consult :func:`active_pilot_access_grant` as an additional
authorization basis.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from app.environments import is_staging
from app.services.clerk_cohort import (
    ClerkCohortIdentity,
    canonical_clerk_email_pair_sha256,
)
from app.services.doctor_access_row_ownership import DoctorAccessRowOwnership
from app.services.pilot_access_lock import lock_pilot_access_lineage
from app.services.superadmin_capability import (
    SuperadminCapabilityError,
    get_active_runtime_superadmin_capability_kinds,
)

from activemodel.session_manager import get_session
from app.models.doctor import CLERK_OBJECT_PREFIX, Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError, MultipleResultsFound
from sqlmodel import Session, col, select

PILOT_ACCESS_GRANDFATHER_REQUIRED_ACCOUNT_COUNT = 47
PILOT_ACCESS_GRANDFATHER_STAGING_ACCOUNT_COUNT = 6
PILOT_ACCESS_GRANDFATHER_RETENTION_YEARS = 7


class PilotAccessGrandfatherError(ValueError):
    """A safe, aggregate refusal that never identifies a roster account."""


@dataclass(frozen=True)
class GrandfatherGrantInput:
    """One exact Clerk-export identity selected for the reviewed cohort."""

    clerk_id: str
    email: str


@dataclass(frozen=True)
class GrandfatherGrantBatchResult:
    grants: tuple[PilotAccessGrandfatherGrant, ...]
    plan_sha256: str
    created_count: int
    unchanged_count: int


@dataclass(frozen=True)
class GrandfatherGrantPlan:
    plan_sha256: str
    account_count: int
    existing_doctor_count: int
    prelogin_account_count: int
    already_granted_count: int
    already_applied: bool


@dataclass(frozen=True)
class GrandfatherRevokeBatchResult:
    grants: tuple[PilotAccessGrandfatherGrant, ...]
    plan_sha256: str
    revoked_count: int
    unchanged_count: int


def _required(value: str, *, field: str, limit: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise PilotAccessGrandfatherError(f"{field} is required")
    if len(normalized) > limit:
        raise PilotAccessGrandfatherError(f"{field} exceeds {limit} characters")
    return normalized


def _confirmed_sha256(value: str, *, field: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise PilotAccessGrandfatherError(
            f"{field} must be a 64-character lowercase hex digest"
        )
    return normalized


def _aware(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise PilotAccessGrandfatherError("operation timestamp must include a timezone")
    return current


def _normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if (
        not normalized
        or len(normalized) > 320
        or normalized.count("@") != 1
        or not normalized.rsplit("@", 1)[0]
        or "." not in normalized.rsplit("@", 1)[1]
    ):
        raise PilotAccessGrandfatherError(
            "one or more Clerk roster entries are invalid"
        )
    return normalized


def _normalize_clerk_id(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized.startswith(CLERK_OBJECT_PREFIX)
        or len(normalized) <= len(CLERK_OBJECT_PREFIX)
        or len(normalized) > 255
        or any(character.isspace() for character in normalized)
    ):
        raise PilotAccessGrandfatherError(
            "one or more Clerk roster entries are invalid"
        )
    return normalized


def _email_sha256(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def required_pilot_access_grandfather_account_count() -> int:
    """Return the reviewed whole-instance cohort size for this environment."""

    if is_staging():
        return PILOT_ACCESS_GRANDFATHER_STAGING_ACCOUNT_COUNT
    return PILOT_ACCESS_GRANDFATHER_REQUIRED_ACCOUNT_COUNT


def _normalized_grant_entries(
    entries: list[GrandfatherGrantInput],
) -> tuple[tuple[str, str, str], ...]:
    required_count = required_pilot_access_grandfather_account_count()
    if len(entries) != required_count:
        raise PilotAccessGrandfatherError(
            "the grandfather plan does not contain the reviewed account count "
            "for this environment"
        )
    normalized: list[tuple[str, str, str]] = []
    clerk_ids: set[str] = set()
    emails: set[str] = set()
    for entry in entries:
        clerk_id = _normalize_clerk_id(entry.clerk_id)
        email = _normalize_email(entry.email)
        if clerk_id in clerk_ids or email in emails:
            raise PilotAccessGrandfatherError(
                "the Clerk roster contains one or more duplicate identities"
            )
        clerk_ids.add(clerk_id)
        emails.add(email)
        normalized.append((clerk_id, email, _email_sha256(email)))
    return tuple(sorted(normalized, key=lambda row: row[0]))


def _normalized_clerk_ids(clerk_ids: list[str]) -> tuple[str, ...]:
    if not clerk_ids:
        raise PilotAccessGrandfatherError(
            "at least one Clerk account is required for revocation"
        )
    normalized = tuple(sorted(_normalize_clerk_id(value) for value in clerk_ids))
    if len(set(normalized)) != len(normalized):
        raise PilotAccessGrandfatherError(
            "the revocation plan contains duplicate Clerk identities"
        )
    return normalized


def _canonical_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pilot_access_grant_plan_sha256(
    entries: list[GrandfatherGrantInput],
    *,
    source_ref: str,
    source_sha256: str,
    operator_ref: str,
    reason: str,
) -> str:
    """Hash the complete environment-bound plan without exposing email."""

    source = _required(source_ref, field="source_ref", limit=255)
    source_digest = _confirmed_sha256(source_sha256, field="source_sha256")
    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    normalized = _normalized_grant_entries(entries)
    return _canonical_sha256(
        {
            "action": "pilot_access_grandfather_grant",
            "accounts": [
                {"clerk_id": clerk_id, "email_sha256": email_sha256}
                for clerk_id, _, email_sha256 in normalized
            ],
            "operator_ref": operator,
            "reason": explanation,
            "schema_version": 1,
            "source_ref": source,
            "source_sha256": source_digest,
        }
    )


def pilot_access_revoke_plan_sha256(
    clerk_ids: list[str],
    *,
    operator_ref: str,
    reason: str,
) -> str:
    """Hash an exact Clerk-subject revocation plan."""

    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    normalized = _normalized_clerk_ids(clerk_ids)
    return _canonical_sha256(
        {
            "action": "pilot_access_grandfather_revoke",
            "clerk_ids": list(normalized),
            "operator_ref": operator,
            "reason": explanation,
            "schema_version": 1,
        }
    )


def _lock_clerk_subject(session: Session, clerk_id: str) -> None:
    lock_key = int.from_bytes(
        hashlib.sha256(f"pilot-access:{clerk_id}".encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=True,
    )
    session.exec(select(func.pg_advisory_xact_lock(lock_key))).one()


def _doctor_and_practice_snapshot(
    session: Session,
    *,
    clerk_id: str,
    expected_email: str,
    lock: bool,
) -> tuple[Doctor | None, Any | None]:
    email_owner_statement = select(Doctor).where(
        func.lower(func.btrim(Doctor.email)) == expected_email
    )
    if lock:
        email_owner_statement = email_owner_statement.with_for_update()
    email_owners = session.exec(email_owner_statement).all()
    if any(owner.clerk_id != clerk_id for owner in email_owners):
        raise PilotAccessGrandfatherError(
            "one or more roster emails belong to a different existing account"
        )

    statement = select(Doctor).where(Doctor.clerk_id == clerk_id)
    if lock:
        statement = statement.with_for_update()
    doctor = cast(
        Doctor | None,
        session.exec(statement).one_or_none(),
    )
    if doctor is None:
        return None, None
    if doctor.deleted_at is not None or not doctor.email:
        raise PilotAccessGrandfatherError(
            "one or more existing accounts are inactive or no longer match the Clerk roster"
        )
    try:
        stored_email = _normalize_email(doctor.email)
    except PilotAccessGrandfatherError as exc:
        raise PilotAccessGrandfatherError(
            "one or more existing accounts are inactive or no longer match the Clerk roster"
        ) from exc
    if not hmac.compare_digest(
        _email_sha256(stored_email), _email_sha256(expected_email)
    ):
        raise PilotAccessGrandfatherError(
            "one or more existing accounts are inactive or no longer match the Clerk roster"
        )
    member = session.exec(
        select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
    ).one_or_none()
    if member is not None and member.access_revoked_at is not None:
        raise PilotAccessGrandfatherError(
            "one or more existing accounts have a prior access revocation"
        )
    return doctor, member.practice_id if member is not None else None


def _existing_grants_by_clerk(
    session: Session,
    clerk_ids: tuple[str, ...],
    *,
    lock: bool,
) -> dict[str, list[PilotAccessGrandfatherGrant]]:
    statement = select(PilotAccessGrandfatherGrant).where(
        col(PilotAccessGrandfatherGrant.clerk_id).in_(clerk_ids)
    )
    if lock:
        statement = statement.with_for_update()
    rows = session.exec(statement).all()
    grouped = {clerk_id: [] for clerk_id in clerk_ids}
    for row in rows:
        grouped[row.clerk_id].append(row)
    return grouped


def _existing_doctors_by_clerk(
    session: Session, clerk_ids: tuple[str, ...]
) -> dict[str, Doctor]:
    doctors: dict[str, Doctor] = {}
    for clerk_id in clerk_ids:
        doctor = cast(
            Doctor | None,
            session.exec(
                select(Doctor).where(Doctor.clerk_id == clerk_id).with_for_update()
            ).one_or_none(),
        )
        if doctor is not None:
            doctors[clerk_id] = doctor
    return doctors


def _grant_matches_exact_plan(
    grant: PilotAccessGrandfatherGrant,
    *,
    email: str,
    email_sha256: str,
    source_ref: str,
    source_sha256: str,
    operator_ref: str,
    reason: str,
    plan_sha256: str,
) -> bool:
    return (
        grant.revoked_at is None
        and grant.email_snapshot == email
        and hmac.compare_digest(grant.email_sha256, email_sha256)
        and grant.source_ref == source_ref
        and hmac.compare_digest(grant.source_sha256, source_sha256)
        and grant.granted_by_ref == operator_ref
        and grant.grant_reason == reason
        and hmac.compare_digest(grant.grant_plan_sha256, plan_sha256)
    )


def _assess_grant_plan(
    session: Session,
    *,
    normalized: tuple[tuple[str, str, str], ...],
    source_ref: str,
    source_sha256: str,
    operator_ref: str,
    reason: str,
    plan_sha256: str,
    lock: bool,
) -> tuple[
    dict[str, tuple[Doctor | None, Any | None]],
    list[PilotAccessGrandfatherGrant],
]:
    clerk_ids = tuple(row[0] for row in normalized)
    if lock:
        for clerk_id in clerk_ids:
            _lock_clerk_subject(session, clerk_id)

    snapshots: dict[str, tuple[Doctor | None, Any | None]] = {}
    for clerk_id, email, _ in normalized:
        snapshots[clerk_id] = _doctor_and_practice_snapshot(
            session,
            clerk_id=clerk_id,
            expected_email=email,
            lock=lock,
        )

    existing = _existing_grants_by_clerk(session, clerk_ids, lock=lock)
    exact: list[PilotAccessGrandfatherGrant] = []
    has_no_history = True
    for clerk_id, email, email_sha256 in normalized:
        history = existing[clerk_id]
        if history:
            has_no_history = False
        matches = [
            grant
            for grant in history
            if _grant_matches_exact_plan(
                grant,
                email=email,
                email_sha256=email_sha256,
                source_ref=source_ref,
                source_sha256=source_sha256,
                operator_ref=operator_ref,
                reason=reason,
                plan_sha256=plan_sha256,
            )
        ]
        if len(matches) == 1:
            exact.append(matches[0])

    if len(exact) != len(normalized) and (exact or not has_no_history):
        raise PilotAccessGrandfatherError(
            "the grandfather cohort has partial, revoked, or conflicting prior state"
        )
    return snapshots, exact


def plan_pilot_access_grants(
    entries: list[GrandfatherGrantInput],
    *,
    source_ref: str,
    source_sha256: str,
    operator_ref: str,
    reason: str,
) -> GrandfatherGrantPlan:
    """Validate live database compatibility for a no-write grant preview."""

    source = _required(source_ref, field="source_ref", limit=255)
    source_digest = _confirmed_sha256(source_sha256, field="source_sha256")
    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    normalized = _normalized_grant_entries(entries)
    actual_source_digest = canonical_clerk_email_pair_sha256(
        tuple(
            ClerkCohortIdentity(clerk_id=clerk_id, email=email)
            for clerk_id, email, _ in normalized
        )
    )
    if not hmac.compare_digest(actual_source_digest, source_digest):
        raise PilotAccessGrandfatherError(
            "source digest does not match the normalized Clerk identity cohort"
        )
    plan_sha256 = pilot_access_grant_plan_sha256(
        entries,
        source_ref=source,
        source_sha256=source_digest,
        operator_ref=operator,
        reason=explanation,
    )
    with get_session() as session:
        snapshots, exact = _assess_grant_plan(
            session,
            normalized=normalized,
            source_ref=source,
            source_sha256=source_digest,
            operator_ref=operator,
            reason=explanation,
            plan_sha256=plan_sha256,
            lock=False,
        )
    existing_doctor_count = sum(
        1 for doctor, _ in snapshots.values() if doctor is not None
    )
    return GrandfatherGrantPlan(
        plan_sha256=plan_sha256,
        account_count=len(normalized),
        existing_doctor_count=existing_doctor_count,
        prelogin_account_count=len(normalized) - existing_doctor_count,
        already_granted_count=len(exact),
        already_applied=len(exact) == len(normalized),
    )


def grant_pilot_access_grants(
    entries: list[GrandfatherGrantInput],
    *,
    source_ref: str,
    source_sha256: str,
    operator_ref: str,
    reason: str,
    confirm_plan_sha256: str,
    now: datetime | None = None,
) -> GrandfatherGrantBatchResult:
    """Atomically apply the exact reviewed Clerk plan for this environment.

    An exact retry returns the original rows. Any partial, revoked, or
    differently sourced prior state refuses the entire operation.
    """

    source = _required(source_ref, field="source_ref", limit=255)
    source_digest = _confirmed_sha256(source_sha256, field="source_sha256")
    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    normalized = _normalized_grant_entries(entries)
    plan = plan_pilot_access_grants(
        entries,
        source_ref=source,
        source_sha256=source_digest,
        operator_ref=operator,
        reason=explanation,
    )
    confirmed = _confirmed_sha256(confirm_plan_sha256, field="confirm_plan_sha256")
    if not hmac.compare_digest(confirmed, plan.plan_sha256):
        raise PilotAccessGrandfatherError(
            "confirmed plan digest does not match the normalized grant plan"
        )
    current = _aware(now)

    with get_session() as session:
        lock_pilot_access_lineage(session)
        snapshots, exact = _assess_grant_plan(
            session,
            normalized=normalized,
            source_ref=source,
            source_sha256=source_digest,
            operator_ref=operator,
            reason=explanation,
            plan_sha256=plan.plan_sha256,
            lock=True,
        )

        if len(exact) == len(normalized):
            return GrandfatherGrantBatchResult(
                grants=tuple(exact),
                plan_sha256=plan.plan_sha256,
                created_count=0,
                unchanged_count=len(exact),
            )

        grants: list[PilotAccessGrandfatherGrant] = []
        for clerk_id, email, email_sha256 in normalized:
            doctor, practice_id = snapshots[clerk_id]
            grant = PilotAccessGrandfatherGrant(
                clerk_id=clerk_id,
                doctor_id_snapshot=doctor.id if doctor is not None else None,
                practice_id_snapshot=practice_id,
                email_snapshot=email,
                email_sha256=email_sha256,
                source_ref=source,
                source_sha256=source_digest,
                grant_plan_sha256=plan.plan_sha256,
                granted_by_ref=operator,
                grant_reason=explanation,
                granted_at=current,
            )
            grants.append(grant)
            session.add(grant)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise PilotAccessGrandfatherError(
                "a concurrent grandfather access change prevented the batch"
            ) from exc
        for grant in grants:
            session.refresh(grant)
        return GrandfatherGrantBatchResult(
            grants=tuple(grants),
            plan_sha256=plan.plan_sha256,
            created_count=len(grants),
            unchanged_count=0,
        )


def _seven_calendar_years_after(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year + PILOT_ACCESS_GRANDFATHER_RETENTION_YEARS)
    except ValueError:
        return value.replace(
            year=value.year + PILOT_ACCESS_GRANDFATHER_RETENTION_YEARS,
            month=2,
            day=28,
        )


def revoke_pilot_access_grants(
    clerk_ids: list[str],
    *,
    operator_ref: str,
    reason: str,
    confirm_plan_sha256: str,
    now: datetime | None = None,
) -> GrandfatherRevokeBatchResult:
    """Atomically revoke an exact set of Clerk-rooted grants."""

    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    normalized = _normalized_clerk_ids(clerk_ids)
    plan_sha256 = pilot_access_revoke_plan_sha256(
        list(normalized),
        operator_ref=operator,
        reason=explanation,
    )
    confirmed = _confirmed_sha256(confirm_plan_sha256, field="confirm_plan_sha256")
    if not hmac.compare_digest(confirmed, plan_sha256):
        raise PilotAccessGrandfatherError(
            "confirmed plan digest does not match the normalized revocation plan"
        )
    current = _aware(now)

    with get_session() as session:
        lock_pilot_access_lineage(session)
        for clerk_id in normalized:
            _lock_clerk_subject(session, clerk_id)
        doctors = _existing_doctors_by_clerk(session, normalized)
        try:
            protected = any(
                get_active_runtime_superadmin_capability_kinds(
                    session,
                    subject=doctor.clerk_id,
                    doctor_id=doctor.id,
                    at=current,
                    lock_for_update=True,
                )
                for doctor in doctors.values()
            )
        except SuperadminCapabilityError:
            raise PilotAccessGrandfatherError(
                "superadmin capability state could not be verified"
            ) from None
        if protected:
            raise PilotAccessGrandfatherError(
                "active superadmin capabilities must be revoked before pilot access can be revoked"
            )
        existing = _existing_grants_by_clerk(session, normalized, lock=True)

        active: list[PilotAccessGrandfatherGrant] = []
        exact_revoked: list[PilotAccessGrandfatherGrant] = []
        invalid = False
        for clerk_id in normalized:
            history = existing[clerk_id]
            current_rows = [grant for grant in history if grant.revoked_at is None]
            retry_rows = [
                grant
                for grant in history
                if grant.revoked_at is not None
                and grant.revoked_by_ref == operator
                and grant.revocation_reason == explanation
                and grant.revocation_plan_sha256 is not None
                and hmac.compare_digest(
                    grant.revocation_plan_sha256,
                    plan_sha256,
                )
            ]
            if len(current_rows) == 1 and not retry_rows:
                active.append(current_rows[0])
            elif not current_rows and len(retry_rows) == 1:
                exact_revoked.append(retry_rows[0])
            else:
                invalid = True

        if invalid or (active and exact_revoked):
            raise PilotAccessGrandfatherError(
                "one or more requested grants are missing or have conflicting revocation state"
            )
        if exact_revoked:
            doctor_changed = False
            for doctor in doctors.values():
                if doctor.deleted_at is None:
                    doctor.deleted_at = current
                    session.add(doctor)
                    doctor_changed = True
            if doctor_changed:
                session.commit()
            return GrandfatherRevokeBatchResult(
                grants=tuple(exact_revoked),
                plan_sha256=plan_sha256,
                revoked_count=0,
                unchanged_count=len(exact_revoked),
            )

        for grant in active:
            if current < grant.granted_at:
                raise PilotAccessGrandfatherError(
                    "revocation timestamp cannot precede the original grant"
                )
        retain_until = _seven_calendar_years_after(current)
        for grant in active:
            grant.revoked_at = current
            grant.revoked_by_ref = operator
            grant.revocation_reason = explanation
            grant.revocation_plan_sha256 = plan_sha256
            grant.retain_until = retain_until
            session.add(grant)
        for doctor in doctors.values():
            if doctor.deleted_at is None:
                doctor.deleted_at = current
                session.add(doctor)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise PilotAccessGrandfatherError(
                "a concurrent grandfather access change prevented the batch"
            ) from exc
        for grant in active:
            session.refresh(grant)
        return GrandfatherRevokeBatchResult(
            grants=tuple(active),
            plan_sha256=plan_sha256,
            revoked_count=len(active),
            unchanged_count=0,
        )


def revoked_pilot_access_grant(
    clerk_id: str,
    *,
    _ownership: DoctorAccessRowOwnership | None = None,
) -> PilotAccessGrandfatherGrant | None:
    """Return a durable offboarding tombstone when no active replacement exists."""

    try:
        normalized = _normalize_clerk_id(clerk_id)
    except PilotAccessGrandfatherError:
        return None
    with get_session() as session:
        if isinstance(
            _ownership, DoctorAccessRowOwnership
        ) and _ownership.matches_subject(session, normalized):
            active = (
                select(col(PilotAccessGrandfatherGrant.id))
                .where(
                    PilotAccessGrandfatherGrant.clerk_id == normalized,
                    PilotAccessGrandfatherGrant.revoked_at == None,  # noqa: E711
                )
                .correlate(None)
                .exists()
            )
            return session.exec(
                select(PilotAccessGrandfatherGrant)
                .where(
                    PilotAccessGrandfatherGrant.clerk_id == normalized,
                    PilotAccessGrandfatherGrant.revoked_at != None,  # noqa: E711
                    ~active,
                )
                .order_by(col(PilotAccessGrandfatherGrant.revoked_at).desc())
            ).first()
        active = session.exec(
            select(PilotAccessGrandfatherGrant).where(
                PilotAccessGrandfatherGrant.clerk_id == normalized,
                PilotAccessGrandfatherGrant.revoked_at == None,  # noqa: E711
            )
        ).first()
        if active is not None:
            return None
        return cast(
            PilotAccessGrandfatherGrant | None,
            session.exec(
                select(PilotAccessGrandfatherGrant)
                .where(
                    PilotAccessGrandfatherGrant.clerk_id == normalized,
                    PilotAccessGrandfatherGrant.revoked_at != None,  # noqa: E711
                )
                .order_by(col(PilotAccessGrandfatherGrant.revoked_at).desc())
            ).first(),
        )


def active_pilot_access_grant(
    doctor: Doctor,
    *,
    at: datetime | None = None,
    _ownership: DoctorAccessRowOwnership | None = None,
) -> PilotAccessGrandfatherGrant | None:
    """Return a candidate record while stable subject and email still match.

    The email check intentionally fails closed after a Clerk/Doctor email
    change. An operator must review the new Clerk export and explicitly issue
    replacement evidence rather than silently transferring it. Callers may use
    the grant as an entitlement for its snapshotted practice; treating it as
    the legacy access basis additionally requires the Doctor's durable
    completed-onboarding marker.
    """

    current = _aware(at)
    if doctor.deleted_at is not None or not doctor.clerk_id or not doctor.email:
        return None
    try:
        clerk_id = _normalize_clerk_id(doctor.clerk_id)
    except PilotAccessGrandfatherError:
        return None

    with get_session() as session:
        combined = isinstance(
            _ownership, DoctorAccessRowOwnership
        ) and _ownership.matches(session, doctor, practice_id=_ownership._practice_id)
        if combined:
            # Fresh late-stage query: never reuse the earlier tombstone result.
            # Limit two preserves ambiguity refusal after Doctor validation.
            candidates = session.exec(
                select(Doctor, PilotAccessGrandfatherGrant)
                .outerjoin(
                    PilotAccessGrandfatherGrant,
                    and_(
                        PilotAccessGrandfatherGrant.clerk_id == clerk_id,
                        PilotAccessGrandfatherGrant.granted_at <= current,
                        or_(
                            PilotAccessGrandfatherGrant.revoked_at == None,  # noqa: E711
                            PilotAccessGrandfatherGrant.revoked_at > current,
                        ),
                    ),
                )
                .where(Doctor.id == doctor.id, Doctor.clerk_id == clerk_id)
                .limit(2)
            ).all()
            persisted_doctor = candidates[0][0] if candidates else None
        else:
            persisted_doctor = cast(
                Doctor | None,
                session.exec(
                    select(Doctor).where(
                        Doctor.id == doctor.id,
                        Doctor.clerk_id == clerk_id,
                    )
                ).one_or_none(),
            )
        if (
            persisted_doctor is None
            or persisted_doctor.deleted_at is not None
            or not persisted_doctor.email
        ):
            return None
        try:
            normalized_email = _normalize_email(persisted_doctor.email)
        except PilotAccessGrandfatherError:
            return None
        email_sha256 = _email_sha256(normalized_email)
        if combined:
            if len(candidates) > 1:
                raise MultipleResultsFound(
                    "Multiple rows were found when one or none was required"
                )
            grant = candidates[0][1]
        else:
            grant = cast(
                PilotAccessGrandfatherGrant | None,
                session.exec(
                    select(PilotAccessGrandfatherGrant).where(
                        PilotAccessGrandfatherGrant.clerk_id == clerk_id,
                        PilotAccessGrandfatherGrant.granted_at <= current,
                        or_(
                            PilotAccessGrandfatherGrant.revoked_at == None,  # noqa: E711
                            PilotAccessGrandfatherGrant.revoked_at > current,
                        ),
                    )
                ).one_or_none(),
            )
        if grant is None:
            return None
        if (
            grant.doctor_id_snapshot is not None
            and grant.doctor_id_snapshot != doctor.id
        ):
            return None
        if grant.email_snapshot != normalized_email:
            return None
        if not hmac.compare_digest(grant.email_sha256, email_sha256):
            return None
        return grant


@dataclass(frozen=True)
class CurrentProviderAccessRows:
    """Fresh inputs to one locked access decision, never retained permission."""

    member: PracticeMember | None
    practice: Practice | None
    pilot_grant: PilotAccessGrandfatherGrant | None


def owned_provider_access_rows(
    doctor: Doctor,
    *,
    at: datetime,
    ownership: DoctorAccessRowOwnership,
) -> CurrentProviderAccessRows | None:
    """Read the existing member/practice and late grant inputs together.

    Only explicit-clock decisions under actual row ownership use this path.
    Default-clock callers retain their original sampling/query order. Invalid
    evidence or malformed subjects select the existing individual reads.
    """
    current = _aware(at)
    if doctor.deleted_at is not None or not doctor.clerk_id or not doctor.email:
        return None
    try:
        subject = _normalize_clerk_id(doctor.clerk_id)
    except PilotAccessGrandfatherError:
        return None
    with get_session() as session:
        if not ownership.matches(session, doctor, practice_id=ownership._practice_id):
            return None
        rows = session.exec(
            select(Doctor, PracticeMember, Practice, PilotAccessGrandfatherGrant)
            .select_from(Doctor)
            .outerjoin(PracticeMember, col(PracticeMember.doctor_id) == col(Doctor.id))
            .outerjoin(Practice, col(Practice.id) == col(PracticeMember.practice_id))
            .outerjoin(
                PilotAccessGrandfatherGrant,
                and_(
                    PilotAccessGrandfatherGrant.clerk_id == subject,
                    PilotAccessGrandfatherGrant.granted_at <= current,
                    or_(
                        PilotAccessGrandfatherGrant.revoked_at == None,  # noqa: E711
                        PilotAccessGrandfatherGrant.revoked_at > current,
                    ),
                ),
            )
            .where(Doctor.id == doctor.id, Doctor.clerk_id == subject)
            .limit(2)
        ).all()
        if not ownership.matches(session, doctor, practice_id=ownership._practice_id):
            return None
        persisted = rows[0][0] if rows else None
        member, practice = rows[0][1:3] if rows else (None, None)
        grant = None
        # Preserve Doctor validation before grant cardinality refusal, including
        # the existing identity-map behavior (no populate_existing added here).
        if persisted is not None and persisted.deleted_at is None and persisted.email:
            try:
                email = _normalize_email(persisted.email)
            except PilotAccessGrandfatherError:
                email = None
            if email is not None:
                if len(rows) > 1:
                    raise MultipleResultsFound(
                        "Multiple rows were found when one or none was required"
                    )
                candidate = rows[0][3]
                if (
                    candidate is not None
                    and (
                        candidate.doctor_id_snapshot is None
                        or candidate.doctor_id_snapshot == doctor.id
                    )
                    and candidate.email_snapshot == email
                    and hmac.compare_digest(
                        candidate.email_sha256, _email_sha256(email)
                    )
                ):
                    grant = candidate
        return CurrentProviderAccessRows(member, practice, grant)
