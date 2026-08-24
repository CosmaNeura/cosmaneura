"""Fail-closed identity reconciliation for additive pilot-access omissions.

This module intentionally does not repair identity state.  A changed Clerk
subject must go through :mod:`app.services.doctor_identity_rebind`; an omission
amendment may only authorize an exact live identity whose database state
already agrees with a private, environment-specific manifest.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

from typeid import TypeID

from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS
from app.configuration.clerk import clerk
from app.environments import python_environment
from app.services.organization_access import (
    approval_is_active,
    lock_roster_enrollment_email,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import CLERK_OBJECT_PREFIX, Doctor
from app.models.organization_access import (
    OrganizationRosterApproval,
    ProviderOrganization,
)
from app.models.pilot_access_continuity_amendment import (
    PilotAccessContinuityAmendment,
    PilotAccessContinuityAmendmentIdentity,
)
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, col, select

CONTINUITY_MANIFEST_SCHEMA_VERSION = 1
CONTINUITY_BASIS_OPERATIONAL_OMISSION = "reviewed_operational_user_omission"
CONTINUITY_DEPLOYMENT_ENVIRONMENTS = frozenset({"staging", "production"})
MAX_CONTINUITY_MANIFEST_BYTES = 1_048_576


class ContinuityReconciliationError(ValueError):
    """Privacy-safe refusal; messages never contain manifest identity values."""


@dataclass(frozen=True, slots=True)
class ContinuityIdentityInput:
    issuer: str
    subject: str
    verified_primary_email: str
    doctor_id: TypeIDType | None
    safe_prelogin: bool
    continuity_basis: str
    practice_id: TypeIDType | None = None


@dataclass(frozen=True, slots=True)
class ContinuityManifest:
    environment: str
    identities: tuple[ContinuityIdentityInput, ...]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class ContinuityAmendmentPlan:
    plan_sha256: str
    manifest_sha256: str
    account_count: int
    existing_doctor_count: int
    safe_prelogin_count: int
    already_applied: bool


@dataclass(frozen=True, slots=True)
class ContinuityAmendmentResult:
    plan_sha256: str
    manifest_sha256: str
    account_count: int
    created_count: int
    unchanged_count: int


@dataclass(frozen=True, slots=True)
class _IdentitySnapshot:
    entry: ContinuityIdentityInput
    doctor: Doctor | None
    practice: Practice | None


def _safe_error(message: str) -> ContinuityReconciliationError:
    return ContinuityReconciliationError(message)


def _required(value: object, *, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise _safe_error(f"{field} is invalid")
    normalized = value.strip()
    if not normalized or normalized != value or len(normalized) > limit:
        raise _safe_error(f"{field} is invalid")
    return normalized


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _confirmed_digest(value: str, *, field: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise _safe_error(f"{field} must be a 64-character lowercase hex digest")
    return normalized


def _normalize_environment(value: object) -> str:
    environment = _required(value, field="environment", limit=16)
    if environment not in CONTINUITY_DEPLOYMENT_ENVIRONMENTS:
        raise _safe_error("manifest environment must be staging or production")
    return environment


def _current_environment() -> str:
    return _normalize_environment(python_environment())


def _normalize_issuer(value: object) -> str:
    issuer = _required(value, field="Clerk issuer", limit=255)
    parsed = urlparse(issuer)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
        or issuer.endswith("/")
    ):
        raise _safe_error("one or more manifest Clerk issuers are invalid")
    return issuer


def _normalize_subject(value: object) -> str:
    subject = _required(value, field="Clerk subject", limit=255)
    if (
        not subject.startswith(CLERK_OBJECT_PREFIX)
        or len(subject) <= len(CLERK_OBJECT_PREFIX)
        or any(character.isspace() for character in subject)
    ):
        raise _safe_error("one or more manifest Clerk subjects are invalid")
    return subject


def _normalize_email(value: object) -> str:
    email = _required(value, field="verified primary email", limit=320)
    normalized = email.lower()
    if (
        normalized != email
        or normalized.count("@") != 1
        or not normalized.rsplit("@", 1)[0]
        or "." not in normalized.rsplit("@", 1)[1]
    ):
        raise _safe_error("one or more manifest emails are invalid or not normalized")
    return normalized


def _typeid(value: object, *, prefix: str, optional: bool) -> TypeIDType | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise _safe_error(f"manifest {prefix} reference is invalid")
    try:
        parsed = TypeID.from_string(value)
    except Exception:
        raise _safe_error(f"manifest {prefix} reference is invalid") from None
    if parsed.prefix != prefix or str(parsed) != value:
        raise _safe_error(f"manifest {prefix} reference is invalid")
    return cast(TypeIDType, parsed)


def _parse_manifest_payload(
    payload: object, *, manifest_sha256: str
) -> ContinuityManifest:
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "environment",
        "identities",
    }:
        raise _safe_error(
            "manifest must contain exactly schema_version, environment, and identities"
        )
    if payload["schema_version"] != CONTINUITY_MANIFEST_SCHEMA_VERSION or isinstance(
        payload["schema_version"], bool
    ):
        raise _safe_error("manifest schema_version must be 1")
    environment = _normalize_environment(payload["environment"])
    raw_identities = payload["identities"]
    if not isinstance(raw_identities, list) or not raw_identities:
        raise _safe_error("manifest identities must be a non-empty list")

    identities: list[ContinuityIdentityInput] = []
    seen_issuer_subjects: set[tuple[str, str]] = set()
    seen_emails: set[str] = set()
    seen_doctors: set[str] = set()
    expected_keys = {
        "issuer",
        "subject",
        "verified_primary_email",
        "doctor_id",
        "safe_prelogin",
        "continuity_basis",
        "practice_id",
    }
    for raw in raw_identities:
        if not isinstance(raw, dict) or set(raw) != expected_keys:
            raise _safe_error("each manifest identity must contain the exact v1 fields")
        issuer = _normalize_issuer(raw["issuer"])
        subject = _normalize_subject(raw["subject"])
        email = _normalize_email(raw["verified_primary_email"])
        safe_prelogin = raw["safe_prelogin"]
        if not isinstance(safe_prelogin, bool):
            raise _safe_error("manifest safe_prelogin must be a boolean")
        doctor_id = _typeid(raw["doctor_id"], prefix="doctor", optional=True)
        practice_id = _typeid(raw["practice_id"], prefix="practice", optional=True)
        if safe_prelogin == (doctor_id is not None):
            raise _safe_error(
                "each identity must provide either Doctor ID or explicit safe prelogin state"
            )
        continuity_basis = _required(
            raw["continuity_basis"], field="continuity basis", limit=64
        )
        if continuity_basis != CONTINUITY_BASIS_OPERATIONAL_OMISSION:
            raise _safe_error("manifest continuity basis is not authorized")

        issuer_subject = (issuer, subject)
        if issuer_subject in seen_issuer_subjects or email in seen_emails:
            raise _safe_error("manifest contains duplicate or ambiguous identities")
        if doctor_id is not None and str(doctor_id) in seen_doctors:
            raise _safe_error("manifest contains duplicate or ambiguous identities")
        seen_issuer_subjects.add(issuer_subject)
        seen_emails.add(email)
        if doctor_id is not None:
            seen_doctors.add(str(doctor_id))
        identities.append(
            ContinuityIdentityInput(
                issuer=issuer,
                subject=subject,
                verified_primary_email=email,
                doctor_id=doctor_id,
                safe_prelogin=safe_prelogin,
                continuity_basis=continuity_basis,
                practice_id=practice_id,
            )
        )
    return ContinuityManifest(
        environment=environment,
        identities=tuple(
            sorted(identities, key=lambda identity: (identity.issuer, identity.subject))
        ),
        manifest_sha256=_confirmed_digest(manifest_sha256, field="manifest_sha256"),
    )


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _safe_error("continuity manifest contains duplicate object keys")
        result[key] = value
    return result


def read_private_continuity_manifest(path_value: str) -> ContinuityManifest:
    """Read one regular, non-symlink manifest whose mode is exactly ``0600``."""

    path = Path(path_value)
    if path.is_symlink():
        raise _safe_error("continuity manifest must not be a symbolic link")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise _safe_error("continuity manifest could not be read") from None
    try:
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise _safe_error("continuity manifest must be a regular file")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise _safe_error("continuity manifest mode must be exactly 0600")
            raw = handle.read(MAX_CONTINUITY_MANIFEST_BYTES + 1)
    except OSError:
        raise _safe_error("continuity manifest could not be read") from None
    if len(raw) > MAX_CONTINUITY_MANIFEST_BYTES:
        raise _safe_error("continuity manifest must not exceed 1 MiB")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _safe_error("continuity manifest must be valid UTF-8 JSON") from None
    return _parse_manifest_payload(
        payload, manifest_sha256=hashlib.sha256(raw).hexdigest()
    )


def continuity_amendment_plan_sha256(
    manifest: ContinuityManifest,
    *,
    source_ref: str,
    operator_ref: str,
    reason: str,
) -> str:
    """Hash the complete environment-bound plan without exposing identities."""

    source = _required(source_ref, field="source_ref", limit=255)
    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    payload = {
        "action": "pilot_access_continuity_amendment",
        "environment": manifest.environment,
        "identities": [
            {
                "continuity_basis": entry.continuity_basis,
                "doctor_id": str(entry.doctor_id) if entry.doctor_id else None,
                "email_sha256": _digest(entry.verified_primary_email),
                "issuer_sha256": _digest(entry.issuer),
                "practice_id": str(entry.practice_id) if entry.practice_id else None,
                "safe_prelogin": entry.safe_prelogin,
                "subject_sha256": _digest(entry.subject),
            }
            for entry in manifest.identities
        ],
        "manifest_sha256": manifest.manifest_sha256,
        "operator_ref": operator,
        "reason": explanation,
        "schema_version": 1,
        "source_ref": source,
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return _digest(canonical)


def _validate_environment_and_issuer(manifest: ContinuityManifest) -> str:
    environment = _current_environment()
    if manifest.environment != environment:
        raise _safe_error("manifest environment does not match this deployment")
    settings = AUTHORITY_KERNEL_SETTINGS
    if not settings.has_valid_clerk_issuer():
        raise _safe_error("the deployment Clerk issuer is unavailable or invalid")
    configured_issuer = _normalize_issuer(settings.clerk_issuer)
    if any(
        not hmac.compare_digest(entry.issuer, configured_issuer)
        for entry in manifest.identities
    ):
        raise _safe_error("manifest Clerk issuer does not match this deployment")
    return configured_issuer


def _verify_live_clerk_identity(entry: ContinuityIdentityInput) -> None:
    try:
        user = clerk.users.get(user_id=entry.subject)
    except Exception:
        raise _safe_error(
            "one or more live Clerk identities could not be verified"
        ) from None
    if (
        user is None
        or getattr(user, "id", None) != entry.subject
        or getattr(user, "banned", None) is not False
        or getattr(user, "locked", None) is not False
    ):
        raise _safe_error("one or more live Clerk identities are missing or ineligible")
    primary_id = getattr(user, "primary_email_address_id", None)
    addresses = getattr(user, "email_addresses", None)
    primary = (
        [address for address in addresses if getattr(address, "id", None) == primary_id]
        if isinstance(primary_id, str) and addresses
        else []
    )
    if (
        len(primary) != 1
        or getattr(getattr(primary[0], "verification", None), "status", None)
        != "verified"
    ):
        raise _safe_error("one or more live Clerk primary emails are not verified")
    try:
        live_email = _normalize_email(getattr(primary[0], "email_address", None))
    except ContinuityReconciliationError:
        raise _safe_error("one or more live Clerk primary emails are invalid") from None
    if not hmac.compare_digest(live_email, entry.verified_primary_email):
        raise _safe_error("one or more live Clerk identities do not match the manifest")


def _lock_identity(session: Session, entry: ContinuityIdentityInput) -> None:
    # Match the existing pilot-grant lock namespace so grant/revoke/amendment
    # operations serialize on the same Clerk subject.
    lock_key = int.from_bytes(
        hashlib.sha256(f"pilot-access:{entry.subject}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )
    session.exec(select(func.pg_advisory_xact_lock(lock_key))).one()


def _validate_authority_identity(
    session: Session, *, entry: ContinuityIdentityInput, lock: bool
) -> None:
    statement = select(AuthorityActorExternalIdentity).where(
        AuthorityActorExternalIdentity.subject == entry.subject
    )
    if lock:
        statement = statement.with_for_update()
    identities = list(session.exec(statement).all())
    if any(identity.issuer != entry.issuer for identity in identities):
        raise _safe_error("one or more persisted identity bindings are ambiguous")
    if len(identities) > 1:
        raise _safe_error("one or more persisted identity bindings are ambiguous")
    if not identities:
        return
    identity = identities[0]
    if identity.revoked_at is not None:
        raise _safe_error("one or more persisted identity bindings are revoked")
    actor_statement = select(AuthorityActor).where(
        AuthorityActor.id == identity.actor_id
    )
    if lock:
        actor_statement = actor_statement.with_for_update()
    actor = session.exec(actor_statement).one_or_none()
    if actor is None:
        raise _safe_error("one or more persisted identity bindings are orphaned")
    if actor.actor_kind != "human" or actor.status != "active":
        raise _safe_error("one or more persisted identity bindings are inactive")

    actor_identities_statement = select(AuthorityActorExternalIdentity).where(
        AuthorityActorExternalIdentity.actor_id == actor.id
    )
    if lock:
        actor_identities_statement = actor_identities_statement.with_for_update()
    actor_identities = list(session.exec(actor_identities_statement).all())
    if any(row.issuer != entry.issuer for row in actor_identities):
        raise _safe_error("one or more persisted identity bindings are ambiguous")
    active_identities = [row for row in actor_identities if row.revoked_at is None]
    if len(active_identities) != 1 or active_identities[0].id != identity.id:
        raise _safe_error("one or more persisted identity bindings are ambiguous")

    membership_statement = select(AuthorityTenantMembership).where(
        AuthorityTenantMembership.actor_id == actor.id
    )
    if lock:
        membership_statement = membership_statement.with_for_update()
    memberships = list(session.exec(membership_statement).all())
    if (
        len(memberships) != 1
        or memberships[0].status != "active"
        or memberships[0].membership_kind != "provider"
    ):
        raise _safe_error("one or more persisted authority scopes are ambiguous")
    membership = memberships[0]
    tenant_statement = select(AuthorityTenant).where(
        AuthorityTenant.id == membership.tenant_id
    )
    if lock:
        tenant_statement = tenant_statement.with_for_update()
    tenant = session.exec(tenant_statement).one_or_none()
    if tenant is None:
        raise _safe_error("one or more persisted authority scopes are orphaned")
    if tenant.status != "active":
        raise _safe_error("one or more persisted authority scopes are inactive")


def _validate_roster_state(
    session: Session,
    *,
    entry: ContinuityIdentityInput,
    doctor: Doctor | None,
    lock: bool,
) -> None:
    statement = select(OrganizationRosterApproval).where(
        or_(
            func.lower(func.btrim(OrganizationRosterApproval.approved_email))
            == entry.verified_primary_email,
            col(OrganizationRosterApproval.claimed_doctor_id)
            == (doctor.id if doctor is not None else None),
        )
    )
    if doctor is None:
        statement = select(OrganizationRosterApproval).where(
            func.lower(func.btrim(OrganizationRosterApproval.approved_email))
            == entry.verified_primary_email
        )
    if lock:
        statement = statement.with_for_update()
    approvals = list(session.exec(statement).all())
    if any(approval.revoked_at is not None for approval in approvals):
        raise _safe_error(
            "one or more manifest identities have prior access revocation"
        )
    active = [approval for approval in approvals if approval_is_active(approval)]
    if len(active) != len(approvals):
        raise _safe_error("one or more organization roster identities are inactive")
    if len(active) > 1:
        raise _safe_error("one or more organization roster identities are ambiguous")
    if not active:
        return
    approval = active[0]
    if (
        entry.practice_id is not None
        and approval.target_practice_id != entry.practice_id
    ):
        raise _safe_error("one or more manifest practice assignments conflict")
    if doctor is not None and approval.claimed_doctor_id not in (None, doctor.id):
        raise _safe_error("one or more organization roster identities conflict")
    organization_statement = select(ProviderOrganization).where(
        ProviderOrganization.id == approval.organization_id
    )
    practice_statement = select(Practice).where(
        Practice.id == approval.target_practice_id
    )
    if lock:
        organization_statement = organization_statement.with_for_update()
        practice_statement = practice_statement.with_for_update()
    organization = session.exec(organization_statement).one_or_none()
    practice = session.exec(practice_statement).one_or_none()
    if organization is None or practice is None:
        raise _safe_error("one or more organization roster references are orphaned")
    if organization.status != "active" or practice.organization_id != organization.id:
        raise _safe_error("one or more organization roster scopes are inactive")


def _snapshot_identity(
    session: Session, *, entry: ContinuityIdentityInput, lock: bool
) -> _IdentitySnapshot:
    subject_statement = select(Doctor).where(Doctor.clerk_id == entry.subject)
    email_statement = select(Doctor).where(
        func.lower(func.btrim(Doctor.email)) == entry.verified_primary_email
    )
    if lock:
        subject_statement = subject_statement.with_for_update()
        email_statement = email_statement.with_for_update()
    subject_doctors = list(session.exec(subject_statement).all())
    email_doctors = list(session.exec(email_statement).all())
    if len(subject_doctors) > 1 or len(email_doctors) > 1:
        raise _safe_error("one or more persisted Doctor identities are ambiguous")

    doctor: Doctor | None
    if entry.safe_prelogin:
        if subject_doctors or email_doctors:
            raise _safe_error(
                "one or more safe-prelogin identities already have an account"
            )
        doctor = None
    else:
        if entry.doctor_id is None:
            raise _safe_error("one or more manifest Doctor references are missing")
        doctor_statement = select(Doctor).where(Doctor.id == entry.doctor_id)
        if lock:
            doctor_statement = doctor_statement.with_for_update()
        doctor = cast(Doctor | None, session.exec(doctor_statement).one_or_none())
        if doctor is None:
            raise _safe_error("one or more manifest Doctor references are orphaned")
        if (
            doctor.deleted_at is not None
            or doctor.clerk_id != entry.subject
            or not doctor.email
            or not hmac.compare_digest(
                doctor.email.strip().lower(), entry.verified_primary_email
            )
            or len(subject_doctors) != 1
            or subject_doctors[0].id != doctor.id
            or len(email_doctors) != 1
            or email_doctors[0].id != doctor.id
        ):
            raise _safe_error("one or more manifest Doctor identities do not match")

    member: PracticeMember | None = None
    if doctor is not None:
        member_statement = select(PracticeMember).where(
            PracticeMember.doctor_id == doctor.id
        )
        if lock:
            member_statement = member_statement.with_for_update()
        members = list(session.exec(member_statement).all())
        if len(members) > 1:
            raise _safe_error("one or more Doctor practice assignments are ambiguous")
        member = members[0] if members else None
        if member is not None and member.access_revoked_at is not None:
            raise _safe_error(
                "one or more manifest identities have prior access revocation"
            )
        actual_practice_id = member.practice_id if member is not None else None
        if actual_practice_id != entry.practice_id:
            raise _safe_error("one or more manifest practice assignments conflict")

    practice: Practice | None = None
    if entry.practice_id is not None:
        practice_statement = select(Practice).where(Practice.id == entry.practice_id)
        if lock:
            practice_statement = practice_statement.with_for_update()
        practice = cast(Practice | None, session.exec(practice_statement).one_or_none())
        if practice is None:
            raise _safe_error("one or more manifest practice references are orphaned")

    _validate_authority_identity(session, entry=entry, lock=lock)
    _validate_roster_state(session, entry=entry, doctor=doctor, lock=lock)

    email_sha256 = _digest(entry.verified_primary_email)
    grant_statement = select(PilotAccessGrandfatherGrant).where(
        or_(
            col(PilotAccessGrandfatherGrant.clerk_id) == entry.subject,
            col(PilotAccessGrandfatherGrant.email_sha256) == email_sha256,
            col(PilotAccessGrandfatherGrant.doctor_id_snapshot)
            == (doctor.id if doctor is not None else None),
        )
    )
    if doctor is None:
        grant_statement = select(PilotAccessGrandfatherGrant).where(
            or_(
                col(PilotAccessGrandfatherGrant.clerk_id) == entry.subject,
                col(PilotAccessGrandfatherGrant.email_sha256) == email_sha256,
            )
        )
    if lock:
        grant_statement = grant_statement.with_for_update()
    if session.exec(grant_statement).first() is not None:
        raise _safe_error("one or more manifest identities already have grant history")

    evidence_statement = select(PilotAccessContinuityAmendmentIdentity).where(
        or_(
            col(PilotAccessContinuityAmendmentIdentity.clerk_subject) == entry.subject,
            col(PilotAccessContinuityAmendmentIdentity.email_sha256) == email_sha256,
            col(PilotAccessContinuityAmendmentIdentity.doctor_id_snapshot)
            == (doctor.id if doctor is not None else None),
        )
    )
    if doctor is None:
        evidence_statement = select(PilotAccessContinuityAmendmentIdentity).where(
            or_(
                col(PilotAccessContinuityAmendmentIdentity.clerk_subject)
                == entry.subject,
                col(PilotAccessContinuityAmendmentIdentity.email_sha256)
                == email_sha256,
            )
        )
    if lock:
        evidence_statement = evidence_statement.with_for_update()
    if session.exec(evidence_statement).first() is not None:
        raise _safe_error(
            "one or more manifest identities already have amendment history"
        )
    return _IdentitySnapshot(entry=entry, doctor=doctor, practice=practice)


def _existing_exact_amendment(
    session: Session,
    *,
    manifest: ContinuityManifest,
    plan_sha256: str,
    source_ref: str,
    operator_ref: str,
    reason: str,
    lock: bool,
) -> PilotAccessContinuityAmendment | None:
    statement = select(PilotAccessContinuityAmendment).where(
        PilotAccessContinuityAmendment.plan_sha256 == plan_sha256
    )
    if lock:
        statement = statement.with_for_update()
    batches = list(session.exec(statement).all())
    if not batches:
        return None
    if len(batches) != 1:
        raise _safe_error("continuity amendment evidence is ambiguous")
    batch = batches[0]
    if (
        batch.environment != manifest.environment
        or batch.clerk_issuer != manifest.identities[0].issuer
        or batch.source_ref != source_ref
        or not hmac.compare_digest(batch.manifest_sha256, manifest.manifest_sha256)
        or batch.applied_by_ref != operator_ref
        or batch.reason != reason
        or batch.account_count != len(manifest.identities)
    ):
        raise _safe_error("continuity amendment evidence conflicts with the plan")
    entry_statement = select(PilotAccessContinuityAmendmentIdentity).where(
        PilotAccessContinuityAmendmentIdentity.amendment_id == batch.id
    )
    if lock:
        entry_statement = entry_statement.with_for_update()
    rows = list(session.exec(entry_statement).all())
    if len(rows) != len(manifest.identities):
        raise _safe_error("continuity amendment evidence is incomplete")
    by_subject = {row.clerk_subject: row for row in rows}
    if len(by_subject) != len(rows):
        raise _safe_error("continuity amendment evidence is ambiguous")
    for expected in manifest.identities:
        row = by_subject.get(expected.subject)
        if (
            row is None
            or row.clerk_issuer != expected.issuer
            or not hmac.compare_digest(
                row.email_sha256, _digest(expected.verified_primary_email)
            )
            or row.doctor_id_snapshot != expected.doctor_id
            or row.practice_id_snapshot != expected.practice_id
            or row.safe_prelogin != expected.safe_prelogin
            or row.continuity_basis != expected.continuity_basis
        ):
            raise _safe_error("continuity amendment evidence conflicts with the plan")
        grant = session.exec(
            select(PilotAccessGrandfatherGrant).where(
                PilotAccessGrandfatherGrant.id == row.grant_id
            )
        ).one_or_none()
        if (
            grant is None
            or grant.clerk_id != expected.subject
            or grant.email_snapshot != expected.verified_primary_email
            or not hmac.compare_digest(grant.email_sha256, row.email_sha256)
            or grant.doctor_id_snapshot != expected.doctor_id
            or grant.practice_id_snapshot != expected.practice_id
            or grant.source_ref != source_ref
            or not hmac.compare_digest(grant.source_sha256, manifest.manifest_sha256)
            or not hmac.compare_digest(grant.grant_plan_sha256, plan_sha256)
            or grant.granted_by_ref != operator_ref
            or grant.grant_reason != reason
        ):
            raise _safe_error("continuity amendment grant evidence conflicts")
    return batch


def _assess_database_state(
    session: Session,
    *,
    manifest: ContinuityManifest,
    plan_sha256: str,
    source_ref: str,
    operator_ref: str,
    reason: str,
    lock: bool,
) -> tuple[tuple[_IdentitySnapshot, ...], bool]:
    # Import locally to keep the audit module free to reuse the manifest/digest
    # definitions above.  An amendment is valid only as an additive layer over
    # the intact reviewed base cohort (including valid revocation/rebind
    # history), never as a replacement for it.
    from app.services.pilot_access_audit import audit_pilot_access_state

    audit = audit_pilot_access_state(
        expected_environment=manifest.environment,
        expected_clerk_issuer=manifest.identities[0].issuer,
        session=session,
    )
    if not audit.valid:
        raise _safe_error("the existing pilot continuity evidence is not valid")
    if lock:
        # Roster writers use this same privacy-preserving email lock. Acquire
        # the whole set in stable order before reading any roster state.
        for entry in sorted(
            manifest.identities,
            key=lambda identity: identity.verified_primary_email,
        ):
            lock_roster_enrollment_email(session, entry.verified_primary_email)
        for entry in manifest.identities:
            _lock_identity(session, entry)
    existing = _existing_exact_amendment(
        session,
        manifest=manifest,
        plan_sha256=plan_sha256,
        source_ref=source_ref,
        operator_ref=operator_ref,
        reason=reason,
        lock=lock,
    )
    if existing is not None:
        return (), True
    snapshots = tuple(
        _snapshot_identity(session, entry=entry, lock=lock)
        for entry in manifest.identities
    )
    return snapshots, False


def _validate_live_manifest(manifest: ContinuityManifest) -> None:
    _validate_environment_and_issuer(manifest)
    for entry in manifest.identities:
        _verify_live_clerk_identity(entry)


def plan_continuity_amendment(
    manifest: ContinuityManifest,
    *,
    source_ref: str,
    operator_ref: str,
    reason: str,
) -> ContinuityAmendmentPlan:
    """Live-verify and evaluate an amendment without writing any state."""

    source = _required(source_ref, field="source_ref", limit=255)
    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    _validate_live_manifest(manifest)
    plan_sha256 = continuity_amendment_plan_sha256(
        manifest,
        source_ref=source,
        operator_ref=operator,
        reason=explanation,
    )
    with get_session() as session:
        snapshots, already_applied = _assess_database_state(
            session,
            manifest=manifest,
            plan_sha256=plan_sha256,
            source_ref=source,
            operator_ref=operator,
            reason=explanation,
            lock=False,
        )
    existing_count = sum(snapshot.doctor is not None for snapshot in snapshots)
    if already_applied:
        existing_count = sum(not entry.safe_prelogin for entry in manifest.identities)
    return ContinuityAmendmentPlan(
        plan_sha256=plan_sha256,
        manifest_sha256=manifest.manifest_sha256,
        account_count=len(manifest.identities),
        existing_doctor_count=existing_count,
        safe_prelogin_count=len(manifest.identities) - existing_count,
        already_applied=already_applied,
    )


def apply_continuity_amendment(
    manifest: ContinuityManifest,
    *,
    source_ref: str,
    operator_ref: str,
    reason: str,
    confirm_plan_sha256: str,
    now: datetime | None = None,
) -> ContinuityAmendmentResult:
    """Atomically add exact omission grants after a fresh live verification."""

    source = _required(source_ref, field="source_ref", limit=255)
    operator = _required(operator_ref, field="operator_ref", limit=255)
    explanation = _required(reason, field="reason", limit=512)
    confirmed = _confirmed_digest(confirm_plan_sha256, field="confirm_plan_sha256")
    _validate_live_manifest(manifest)
    plan_sha256 = continuity_amendment_plan_sha256(
        manifest,
        source_ref=source,
        operator_ref=operator,
        reason=explanation,
    )
    if not hmac.compare_digest(confirmed, plan_sha256):
        raise _safe_error("confirmed plan digest does not match the amendment plan")
    applied_at = now or datetime.now(UTC)
    if applied_at.tzinfo is None or applied_at.utcoffset() is None:
        raise _safe_error("operation timestamp must include a timezone")

    with get_session() as session:
        snapshots, already_applied = _assess_database_state(
            session,
            manifest=manifest,
            plan_sha256=plan_sha256,
            source_ref=source,
            operator_ref=operator,
            reason=explanation,
            lock=True,
        )
        if already_applied:
            session.commit()
            return ContinuityAmendmentResult(
                plan_sha256=plan_sha256,
                manifest_sha256=manifest.manifest_sha256,
                account_count=len(manifest.identities),
                created_count=0,
                unchanged_count=len(manifest.identities),
            )

        try:
            amendment = PilotAccessContinuityAmendment(
                environment=manifest.environment,
                clerk_issuer=manifest.identities[0].issuer,
                source_ref=source,
                manifest_sha256=manifest.manifest_sha256,
                plan_sha256=plan_sha256,
                applied_by_ref=operator,
                reason=explanation,
                account_count=len(manifest.identities),
                applied_at=applied_at,
            )
            session.add(amendment)
            session.flush()
            for snapshot in snapshots:
                entry = snapshot.entry
                email_sha256 = _digest(entry.verified_primary_email)
                grant = PilotAccessGrandfatherGrant(
                    clerk_id=entry.subject,
                    doctor_id_snapshot=entry.doctor_id,
                    practice_id_snapshot=entry.practice_id,
                    email_snapshot=entry.verified_primary_email,
                    email_sha256=email_sha256,
                    source_ref=source,
                    source_sha256=manifest.manifest_sha256,
                    grant_plan_sha256=plan_sha256,
                    granted_by_ref=operator,
                    grant_reason=explanation,
                    granted_at=applied_at,
                )
                session.add(grant)
                session.flush()
                session.add(
                    PilotAccessContinuityAmendmentIdentity(
                        amendment_id=amendment.id,
                        grant_id=grant.id,
                        clerk_issuer=entry.issuer,
                        clerk_subject=entry.subject,
                        email_sha256=email_sha256,
                        doctor_id_snapshot=entry.doctor_id,
                        practice_id_snapshot=entry.practice_id,
                        safe_prelogin=entry.safe_prelogin,
                        continuity_basis=entry.continuity_basis,
                    )
                )
            session.commit()
        except (IntegrityError, SQLAlchemyError) as exc:
            session.rollback()
            raise _safe_error(
                "a concurrent continuity amendment prevented the atomic batch"
            ) from exc
    return ContinuityAmendmentResult(
        plan_sha256=plan_sha256,
        manifest_sha256=manifest.manifest_sha256,
        account_count=len(manifest.identities),
        created_count=len(manifest.identities),
        unchanged_count=0,
    )
