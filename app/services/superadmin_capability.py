"""Fail-closed lifecycle for environment-scoped superadmin capabilities.

Runtime authorization is keyed only by the authenticated Clerk issuer and
subject, the durable Doctor id, the deployment environment, and the explicit
capability kind.  Email is used only as live provisioning evidence and is
stored solely as a digest.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from typeid import TypeID

from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS
from app.configuration.clerk import clerk
from app.environments import python_environment

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.doctor import CLERK_OBJECT_PREFIX, Doctor
from app.models.superadmin_capability import (
    SuperadminCapabilityAudit,
    SuperadminCapabilityAuditAction,
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
    SuperadminCapabilityKind,
)
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

SUPERADMIN_CAPABILITY_MANIFEST_SCHEMA_VERSION = 1
SUPERADMIN_CAPABILITY_RETENTION_YEARS = 7
SUPERADMIN_CAPABILITY_MAX_ACCOUNTS = 32
_REASON_CODE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{2,63}$")
_OPAQUE_REF = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}:[A-Za-z0-9][A-Za-z0-9_.:/-]{1,222}$"
)
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SuperadminCapabilityError(ValueError):
    """Privacy-safe refusal that never identifies an account."""


@dataclass(frozen=True, slots=True)
class SuperadminCapabilityManifestAccount:
    clerk_subject: str
    verified_primary_email: str
    doctor_id: TypeIDType
    capability_kinds: tuple[SuperadminCapabilityKind, ...]


@dataclass(frozen=True, slots=True)
class SuperadminCapabilityManifest:
    environment: SuperadminCapabilityEnvironment
    clerk_issuer: str
    accounts: tuple[SuperadminCapabilityManifestAccount, ...]
    source_sha256: str


@dataclass(frozen=True, slots=True)
class SuperadminCapabilityPlan:
    plan_sha256: str
    source_sha256: str
    account_count: int
    capability_count: int
    platform_admin_count: int
    view_as_count: int
    already_applied_count: int
    already_applied: bool


@dataclass(frozen=True, slots=True)
class SuperadminCapabilityApplyResult:
    plan_sha256: str
    source_sha256: str
    capability_count: int
    created_count: int
    revoked_count: int
    unchanged_count: int


@dataclass(frozen=True, slots=True)
class SuperadminCapabilitySummary:
    environment: SuperadminCapabilityEnvironment
    total_count: int
    active_count: int
    revoked_count: int
    platform_admin_active_count: int
    view_as_active_count: int
    audit_count: int
    active_binding_sha256: str


@dataclass(frozen=True, slots=True)
class _CapabilityTarget:
    clerk_subject: str
    verified_primary_email: str
    verified_primary_email_sha256: str
    doctor_id: TypeIDType
    capability_kind: SuperadminCapabilityKind


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _sha256(serialized)


def _required(value: object, *, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise SuperadminCapabilityError(f"{field} is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise SuperadminCapabilityError(f"{field} is invalid")
    return normalized


def _normalize_reason_code(value: object) -> str:
    normalized = _required(value, field="reason_code", limit=64).lower()
    if not _REASON_CODE.fullmatch(normalized):
        raise SuperadminCapabilityError("reason_code is invalid")
    return normalized


def _normalize_opaque_ref(value: object, *, field: str) -> str:
    normalized = _required(value, field=field, limit=255)
    if "@" in normalized or not _OPAQUE_REF.fullmatch(normalized):
        raise SuperadminCapabilityError(f"{field} is invalid")
    return normalized


def _normalize_digest(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise SuperadminCapabilityError(f"{field} is invalid")
    normalized = value.strip().lower()
    if not _HEX_SHA256.fullmatch(normalized):
        raise SuperadminCapabilityError(f"{field} is invalid")
    return normalized


def _normalize_issuer(value: object) -> str:
    issuer = _required(value, field="clerk_issuer", limit=255)
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
        raise SuperadminCapabilityError("clerk_issuer is invalid")
    return issuer


def _normalize_subject(value: object) -> str:
    subject = _required(value, field="clerk_subject", limit=255)
    if (
        not subject.startswith(CLERK_OBJECT_PREFIX)
        or len(subject) <= len(CLERK_OBJECT_PREFIX)
        or any(character.isspace() for character in subject)
    ):
        raise SuperadminCapabilityError("clerk_subject is invalid")
    return subject


def _normalize_email(value: object) -> str:
    if not isinstance(value, str):
        raise SuperadminCapabilityError("verified_primary_email is invalid")
    email = value.strip().lower()
    if (
        not email
        or len(email) > 320
        or email.count("@") != 1
        or not email.rsplit("@", 1)[0]
        or "." not in email.rsplit("@", 1)[1]
        or any(character.isspace() for character in email)
    ):
        raise SuperadminCapabilityError("verified_primary_email is invalid")
    return email


def _normalize_doctor_id(value: object) -> TypeIDType:
    if not isinstance(value, str):
        raise SuperadminCapabilityError("doctor_id is invalid")
    try:
        parsed = TypeID.from_string(value.strip())
    except Exception:
        raise SuperadminCapabilityError("doctor_id is invalid") from None
    if parsed.prefix != "doctor":
        raise SuperadminCapabilityError("doctor_id is invalid")
    return cast(TypeIDType, parsed)


def _normalize_environment(value: object) -> SuperadminCapabilityEnvironment:
    try:
        return SuperadminCapabilityEnvironment(value)
    except (TypeError, ValueError):
        raise SuperadminCapabilityError("environment is invalid") from None


def _normalize_capability_kind(value: object) -> SuperadminCapabilityKind:
    try:
        return SuperadminCapabilityKind(value)
    except (TypeError, ValueError):
        raise SuperadminCapabilityError("capability kind is invalid") from None


def configured_superadmin_capability_environment() -> SuperadminCapabilityEnvironment:
    """Map only deployable Platform environments; all others fail closed."""

    try:
        return SuperadminCapabilityEnvironment(python_environment())
    except ValueError:
        raise SuperadminCapabilityError(
            "superadmin capabilities are unavailable in this environment"
        ) from None


def configured_superadmin_clerk_issuer() -> str:
    """Return the single configured Clerk issuer, or fail closed."""

    if not AUTHORITY_KERNEL_SETTINGS.has_valid_clerk_issuer():
        raise SuperadminCapabilityError("the configured Clerk issuer is unavailable")
    return _normalize_issuer(AUTHORITY_KERNEL_SETTINGS.clerk_issuer)


def parse_superadmin_capability_manifest(
    payload: object,
) -> SuperadminCapabilityManifest:
    """Validate the strict private manifest and derive its canonical digest."""

    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "environment",
        "clerk_issuer",
        "accounts",
    }:
        raise SuperadminCapabilityError("capability manifest shape is invalid")
    if payload["schema_version"] != SUPERADMIN_CAPABILITY_MANIFEST_SCHEMA_VERSION:
        raise SuperadminCapabilityError("capability manifest version is invalid")
    environment = _normalize_environment(payload["environment"])
    issuer = _normalize_issuer(payload["clerk_issuer"])
    raw_accounts = payload["accounts"]
    if (
        not isinstance(raw_accounts, list)
        or not raw_accounts
        or len(raw_accounts) > SUPERADMIN_CAPABILITY_MAX_ACCOUNTS
    ):
        raise SuperadminCapabilityError("capability manifest accounts are invalid")

    accounts: list[SuperadminCapabilityManifestAccount] = []
    seen_subjects: set[str] = set()
    seen_emails: set[str] = set()
    seen_doctors: set[str] = set()
    canonical_accounts: list[dict[str, object]] = []
    for raw in raw_accounts:
        if not isinstance(raw, dict) or set(raw) != {
            "clerk_subject",
            "verified_primary_email",
            "doctor_id",
            "capability_kinds",
        }:
            raise SuperadminCapabilityError("capability manifest account is invalid")
        subject = _normalize_subject(raw["clerk_subject"])
        email = _normalize_email(raw["verified_primary_email"])
        doctor_id = _normalize_doctor_id(raw["doctor_id"])
        raw_kinds = raw["capability_kinds"]
        if not isinstance(raw_kinds, list) or not raw_kinds:
            raise SuperadminCapabilityError("capability manifest account is invalid")
        kinds = tuple(
            sorted(
                {_normalize_capability_kind(kind) for kind in raw_kinds},
                key=lambda kind: kind.value,
            )
        )
        if len(kinds) != len(raw_kinds):
            raise SuperadminCapabilityError("capability manifest account is invalid")
        doctor_key = str(doctor_id)
        if (
            subject in seen_subjects
            or email in seen_emails
            or doctor_key in seen_doctors
        ):
            raise SuperadminCapabilityError(
                "capability manifest contains duplicate identities"
            )
        seen_subjects.add(subject)
        seen_emails.add(email)
        seen_doctors.add(doctor_key)
        account = SuperadminCapabilityManifestAccount(
            clerk_subject=subject,
            verified_primary_email=email,
            doctor_id=doctor_id,
            capability_kinds=kinds,
        )
        accounts.append(account)
        canonical_accounts.append(
            {
                "capability_kinds": [kind.value for kind in kinds],
                "clerk_subject": subject,
                "doctor_id": doctor_key,
                "verified_primary_email": email,
            }
        )

    canonical_payload = {
        "accounts": sorted(
            canonical_accounts, key=lambda row: str(row["clerk_subject"])
        ),
        "clerk_issuer": issuer,
        "environment": environment.value,
        "schema_version": SUPERADMIN_CAPABILITY_MANIFEST_SCHEMA_VERSION,
    }
    return SuperadminCapabilityManifest(
        environment=environment,
        clerk_issuer=issuer,
        accounts=tuple(sorted(accounts, key=lambda account: account.clerk_subject)),
        source_sha256=_canonical_sha256(canonical_payload),
    )


def load_private_superadmin_capability_manifest(
    path: Path,
) -> SuperadminCapabilityManifest:
    """Read one owner-only regular JSON file without following a symlink."""

    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.geteuid()
        ):
            raise SuperadminCapabilityError(
                "capability manifest must be an owner-only mode-0600 regular file"
            )
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            after = os.fstat(descriptor)
            if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                raise SuperadminCapabilityError(
                    "capability manifest changed while opening"
                )
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                content = handle.read(131_073)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(content.encode("utf-8")) > 131_072:
            raise SuperadminCapabilityError("capability manifest is too large")
        return parse_superadmin_capability_manifest(json.loads(content))
    except SuperadminCapabilityError:
        raise
    except Exception:
        raise SuperadminCapabilityError(
            "capability manifest could not be read"
        ) from None


def validate_superadmin_manifest_runtime_scope(
    manifest: SuperadminCapabilityManifest,
) -> None:
    """Bind an operator manifest to this exact deployment and Clerk tenant."""

    expected_environment = configured_superadmin_capability_environment()
    expected_issuer = configured_superadmin_clerk_issuer()
    if manifest.environment != expected_environment or not hmac.compare_digest(
        manifest.clerk_issuer, expected_issuer
    ):
        raise SuperadminCapabilityError(
            "capability manifest does not match this deployment"
        )


def _manifest_targets(
    manifest: SuperadminCapabilityManifest,
) -> tuple[_CapabilityTarget, ...]:
    targets = [
        _CapabilityTarget(
            clerk_subject=account.clerk_subject,
            verified_primary_email=account.verified_primary_email,
            verified_primary_email_sha256=_sha256(account.verified_primary_email),
            doctor_id=account.doctor_id,
            capability_kind=kind,
        )
        for account in manifest.accounts
        for kind in account.capability_kinds
    ]
    return tuple(
        sorted(
            targets,
            key=lambda target: (
                target.clerk_subject,
                target.capability_kind.value,
            ),
        )
    )


def _live_verified_primary_email(user: Any) -> str:
    primary_id = getattr(user, "primary_email_address_id", None)
    addresses = getattr(user, "email_addresses", None)
    if not isinstance(primary_id, str) or not primary_id or not addresses:
        raise SuperadminCapabilityError("live Clerk identity verification failed")
    matches = [
        address for address in addresses if getattr(address, "id", None) == primary_id
    ]
    if len(matches) != 1:
        raise SuperadminCapabilityError("live Clerk identity verification failed")
    primary = matches[0]
    if getattr(getattr(primary, "verification", None), "status", None) != "verified":
        raise SuperadminCapabilityError("live Clerk identity verification failed")
    return _normalize_email(getattr(primary, "email_address", None))


def _verify_live_clerk_accounts(
    manifest: SuperadminCapabilityManifest,
) -> None:
    """Prove every exact subject currently owns the expected verified email."""

    for account in manifest.accounts:
        try:
            user = clerk.users.get(user_id=account.clerk_subject)
        except Exception:
            raise SuperadminCapabilityError(
                "live Clerk identity verification failed"
            ) from None
        if (
            user is None
            or getattr(user, "id", None) != account.clerk_subject
            or getattr(user, "banned", None) is not False
            or getattr(user, "locked", None) is not False
        ):
            raise SuperadminCapabilityError("live Clerk identity verification failed")
        live_email = _live_verified_primary_email(user)
        if not hmac.compare_digest(
            _sha256(live_email),
            _sha256(account.verified_primary_email),
        ):
            raise SuperadminCapabilityError("live Clerk identity verification failed")


def _identity_binding_sha256(
    *,
    environment: SuperadminCapabilityEnvironment,
    capability_kind: SuperadminCapabilityKind,
    clerk_issuer: str,
    clerk_subject: str,
    doctor_id: TypeIDType,
) -> str:
    return _canonical_sha256(
        {
            "capability_kind": capability_kind.value,
            "clerk_issuer_sha256": _sha256(clerk_issuer),
            "clerk_subject_sha256": _sha256(clerk_subject),
            "doctor_id_sha256": _sha256(str(doctor_id)),
            "environment": environment.value,
        }
    )


def _plan_sha256(
    manifest: SuperadminCapabilityManifest,
    *,
    action: str,
    source_ref: str,
    operator_ref: str,
    reason_code: str,
) -> str:
    targets = _manifest_targets(manifest)
    return _canonical_sha256(
        {
            "action": action,
            "environment": manifest.environment.value,
            "issuer_sha256": _sha256(manifest.clerk_issuer),
            "operator_ref": operator_ref,
            "reason_code": reason_code,
            "schema_version": 1,
            "source_ref": source_ref,
            "source_sha256": manifest.source_sha256,
            "targets": [
                {
                    "binding_sha256": _identity_binding_sha256(
                        environment=manifest.environment,
                        capability_kind=target.capability_kind,
                        clerk_issuer=manifest.clerk_issuer,
                        clerk_subject=target.clerk_subject,
                        doctor_id=target.doctor_id,
                    ),
                    "verified_primary_email_sha256": (
                        target.verified_primary_email_sha256
                    ),
                }
                for target in targets
            ],
        }
    )


def _operation_fields(
    *, source_ref: object, operator_ref: object, reason_code: object
) -> tuple[str, str, str]:
    return (
        _normalize_opaque_ref(source_ref, field="source_ref"),
        _normalize_opaque_ref(operator_ref, field="operator_ref"),
        _normalize_reason_code(reason_code),
    )


def _aware(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise SuperadminCapabilityError("operation timestamp must include a timezone")
    return current


def _lock_target(session: Session, binding_sha256: str) -> None:
    lock_key = int.from_bytes(
        hashlib.sha256(
            f"superadmin-capability:{binding_sha256}".encode("utf-8")
        ).digest()[:8],
        byteorder="big",
        signed=True,
    )
    session.exec(select(func.pg_advisory_xact_lock(lock_key))).one()


def _doctor_matches_target(
    session: Session,
    *,
    target: _CapabilityTarget,
    lock: bool,
) -> Doctor:
    statement = select(Doctor).where(Doctor.id == target.doctor_id)
    if lock:
        statement = statement.with_for_update()
    doctor = cast(Doctor | None, session.exec(statement).one_or_none())
    if (
        doctor is None
        or doctor.deleted_at is not None
        or doctor.clerk_id != target.clerk_subject
        or not doctor.email
    ):
        raise SuperadminCapabilityError("capability identity reconciliation failed")
    try:
        stored_email = _normalize_email(doctor.email)
    except SuperadminCapabilityError:
        raise SuperadminCapabilityError(
            "capability identity reconciliation failed"
        ) from None
    if not hmac.compare_digest(
        _sha256(stored_email), target.verified_primary_email_sha256
    ):
        raise SuperadminCapabilityError("capability identity reconciliation failed")
    return doctor


def _target_grants(
    session: Session,
    *,
    manifest: SuperadminCapabilityManifest,
    target: _CapabilityTarget,
    lock: bool,
) -> list[SuperadminCapabilityGrant]:
    statement = select(SuperadminCapabilityGrant).where(
        SuperadminCapabilityGrant.environment == manifest.environment,
        SuperadminCapabilityGrant.capability_kind == target.capability_kind,
        or_(
            col(SuperadminCapabilityGrant.clerk_subject) == target.clerk_subject,
            col(SuperadminCapabilityGrant.doctor_id) == target.doctor_id,
        ),
    )
    if lock:
        statement = statement.with_for_update()
    return list(session.exec(statement).all())


def _grant_matches_plan(
    grant: SuperadminCapabilityGrant,
    *,
    manifest: SuperadminCapabilityManifest,
    target: _CapabilityTarget,
    source_ref: str,
    operator_ref: str,
    reason_code: str,
    plan_sha256: str,
) -> bool:
    return (
        grant.revoked_at is None
        and grant.environment == manifest.environment
        and grant.capability_kind == target.capability_kind
        and grant.clerk_issuer == manifest.clerk_issuer
        and grant.clerk_subject == target.clerk_subject
        and grant.doctor_id == target.doctor_id
        and hmac.compare_digest(
            grant.verified_primary_email_sha256,
            target.verified_primary_email_sha256,
        )
        and grant.source_ref == source_ref
        and hmac.compare_digest(grant.source_sha256, manifest.source_sha256)
        and grant.granted_by_ref == operator_ref
        and grant.grant_reason_code == reason_code
        and hmac.compare_digest(grant.grant_plan_sha256, plan_sha256)
    )


def _assess_grant_plan(
    session: Session,
    *,
    manifest: SuperadminCapabilityManifest,
    source_ref: str,
    operator_ref: str,
    reason_code: str,
    plan_sha256: str,
    lock: bool,
) -> tuple[tuple[_CapabilityTarget, ...], list[SuperadminCapabilityGrant]]:
    targets = _manifest_targets(manifest)
    exact: list[SuperadminCapabilityGrant] = []
    absent_count = 0
    for target in targets:
        binding = _identity_binding_sha256(
            environment=manifest.environment,
            capability_kind=target.capability_kind,
            clerk_issuer=manifest.clerk_issuer,
            clerk_subject=target.clerk_subject,
            doctor_id=target.doctor_id,
        )
        if lock:
            _lock_target(session, binding)
        _doctor_matches_target(session, target=target, lock=lock)
        history = _target_grants(
            session,
            manifest=manifest,
            target=target,
            lock=lock,
        )
        active = [grant for grant in history if grant.revoked_at is None]
        if len(active) > 1:
            raise SuperadminCapabilityError("capability state is ambiguous")
        if active:
            if not _grant_matches_plan(
                active[0],
                manifest=manifest,
                target=target,
                source_ref=source_ref,
                operator_ref=operator_ref,
                reason_code=reason_code,
                plan_sha256=plan_sha256,
            ):
                raise SuperadminCapabilityError("capability state conflicts with plan")
            exact.append(active[0])
            continue
        if any(
            hmac.compare_digest(grant.grant_plan_sha256, plan_sha256)
            for grant in history
        ):
            raise SuperadminCapabilityError(
                "a retired capability plan cannot be reused"
            )
        absent_count += 1
    if exact and absent_count:
        raise SuperadminCapabilityError("capability plan is only partially applied")
    return targets, exact


def plan_superadmin_capability_grants(
    manifest: SuperadminCapabilityManifest,
    *,
    source_ref: str,
    operator_ref: str,
    reason_code: str,
) -> SuperadminCapabilityPlan:
    """Verify live Clerk and database state without writing anything."""

    source, operator, reason = _operation_fields(
        source_ref=source_ref,
        operator_ref=operator_ref,
        reason_code=reason_code,
    )
    validate_superadmin_manifest_runtime_scope(manifest)
    _verify_live_clerk_accounts(manifest)
    plan_sha256 = _plan_sha256(
        manifest,
        action="grant",
        source_ref=source,
        operator_ref=operator,
        reason_code=reason,
    )
    with get_session() as session:
        targets, exact = _assess_grant_plan(
            session,
            manifest=manifest,
            source_ref=source,
            operator_ref=operator,
            reason_code=reason,
            plan_sha256=plan_sha256,
            lock=False,
        )
    return SuperadminCapabilityPlan(
        plan_sha256=plan_sha256,
        source_sha256=manifest.source_sha256,
        account_count=len(manifest.accounts),
        capability_count=len(targets),
        platform_admin_count=sum(
            target.capability_kind is SuperadminCapabilityKind.platform_admin
            for target in targets
        ),
        view_as_count=sum(
            target.capability_kind is SuperadminCapabilityKind.view_as
            for target in targets
        ),
        already_applied_count=len(exact),
        already_applied=len(exact) == len(targets),
    )


def grant_superadmin_capabilities(
    manifest: SuperadminCapabilityManifest,
    *,
    source_ref: str,
    operator_ref: str,
    reason_code: str,
    confirm_plan_sha256: str,
    now: datetime | None = None,
) -> SuperadminCapabilityApplyResult:
    """Atomically create the exact dry-run plan and append grant audits."""

    source, operator, reason = _operation_fields(
        source_ref=source_ref,
        operator_ref=operator_ref,
        reason_code=reason_code,
    )
    plan = plan_superadmin_capability_grants(
        manifest,
        source_ref=source,
        operator_ref=operator,
        reason_code=reason,
    )
    confirmed = _normalize_digest(
        confirm_plan_sha256,
        field="confirm_plan_sha256",
    )
    if not hmac.compare_digest(confirmed, plan.plan_sha256):
        raise SuperadminCapabilityError("confirmed plan digest does not match")
    current = _aware(now)

    with get_session() as session:
        targets, exact = _assess_grant_plan(
            session,
            manifest=manifest,
            source_ref=source,
            operator_ref=operator,
            reason_code=reason,
            plan_sha256=plan.plan_sha256,
            lock=True,
        )
        if len(exact) == len(targets):
            return SuperadminCapabilityApplyResult(
                plan_sha256=plan.plan_sha256,
                source_sha256=manifest.source_sha256,
                capability_count=len(targets),
                created_count=0,
                revoked_count=0,
                unchanged_count=len(targets),
            )
        try:
            for target in targets:
                grant = SuperadminCapabilityGrant(
                    environment=manifest.environment,
                    capability_kind=target.capability_kind,
                    clerk_issuer=manifest.clerk_issuer,
                    clerk_subject=target.clerk_subject,
                    doctor_id=target.doctor_id,
                    issuer_sha256=_sha256(manifest.clerk_issuer),
                    subject_sha256=_sha256(target.clerk_subject),
                    verified_primary_email_sha256=(
                        target.verified_primary_email_sha256
                    ),
                    source_ref=source,
                    source_sha256=manifest.source_sha256,
                    grant_plan_sha256=plan.plan_sha256,
                    granted_by_ref=operator,
                    grant_reason_code=reason,
                    granted_at=current,
                )
                session.add(grant)
                session.flush()
                session.add(
                    SuperadminCapabilityAudit(
                        grant_id=grant.id,
                        action=SuperadminCapabilityAuditAction.granted,
                        environment=manifest.environment,
                        capability_kind=target.capability_kind,
                        identity_binding_sha256=_identity_binding_sha256(
                            environment=manifest.environment,
                            capability_kind=target.capability_kind,
                            clerk_issuer=manifest.clerk_issuer,
                            clerk_subject=target.clerk_subject,
                            doctor_id=target.doctor_id,
                        ),
                        evidence_sha256=plan.plan_sha256,
                        operator_ref=operator,
                        reason_code=reason,
                        occurred_at=current,
                        recorded_at=current,
                    )
                )
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise SuperadminCapabilityError(
                "a concurrent capability change prevented the batch"
            ) from exc
    return SuperadminCapabilityApplyResult(
        plan_sha256=plan.plan_sha256,
        source_sha256=manifest.source_sha256,
        capability_count=len(targets),
        created_count=len(targets),
        revoked_count=0,
        unchanged_count=0,
    )


def _seven_calendar_years_after(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year + SUPERADMIN_CAPABILITY_RETENTION_YEARS)
    except ValueError:
        return value.replace(
            year=value.year + SUPERADMIN_CAPABILITY_RETENTION_YEARS,
            month=2,
            day=28,
        )


def _assess_revoke_plan(
    session: Session,
    *,
    manifest: SuperadminCapabilityManifest,
    operator_ref: str,
    reason_code: str,
    plan_sha256: str,
    lock: bool,
) -> tuple[
    tuple[_CapabilityTarget, ...],
    list[SuperadminCapabilityGrant],
    list[SuperadminCapabilityGrant],
]:
    targets = _manifest_targets(manifest)
    active_targets: list[SuperadminCapabilityGrant] = []
    exact_revoked: list[SuperadminCapabilityGrant] = []
    for target in targets:
        binding = _identity_binding_sha256(
            environment=manifest.environment,
            capability_kind=target.capability_kind,
            clerk_issuer=manifest.clerk_issuer,
            clerk_subject=target.clerk_subject,
            doctor_id=target.doctor_id,
        )
        if lock:
            _lock_target(session, binding)
        history = _target_grants(
            session,
            manifest=manifest,
            target=target,
            lock=lock,
        )
        matching = [
            grant
            for grant in history
            if grant.clerk_issuer == manifest.clerk_issuer
            and grant.clerk_subject == target.clerk_subject
            and grant.doctor_id == target.doctor_id
            and hmac.compare_digest(
                grant.verified_primary_email_sha256,
                target.verified_primary_email_sha256,
            )
        ]
        active = [grant for grant in matching if grant.revoked_at is None]
        if len(active) == 1:
            active_targets.append(active[0])
            continue
        if len(active) > 1:
            raise SuperadminCapabilityError("capability state is ambiguous")
        retries = [
            grant
            for grant in matching
            if grant.revocation_plan_sha256 is not None
            and hmac.compare_digest(grant.revocation_plan_sha256, plan_sha256)
            and grant.revoked_by_ref == operator_ref
            and grant.revocation_reason_code == reason_code
        ]
        if len(retries) != 1:
            raise SuperadminCapabilityError(
                "capability revocation target is unavailable"
            )
        exact_revoked.append(retries[0])
    if active_targets and exact_revoked:
        raise SuperadminCapabilityError(
            "capability revocation is only partially applied"
        )
    return targets, active_targets, exact_revoked


def plan_superadmin_capability_revocations(
    manifest: SuperadminCapabilityManifest,
    *,
    source_ref: str,
    operator_ref: str,
    reason_code: str,
) -> SuperadminCapabilityPlan:
    """Validate an exact revocation without depending on Clerk availability."""

    source, operator, reason = _operation_fields(
        source_ref=source_ref,
        operator_ref=operator_ref,
        reason_code=reason_code,
    )
    validate_superadmin_manifest_runtime_scope(manifest)
    plan_sha256 = _plan_sha256(
        manifest,
        action="revoke",
        source_ref=source,
        operator_ref=operator,
        reason_code=reason,
    )
    with get_session() as session:
        targets, active, exact_revoked = _assess_revoke_plan(
            session,
            manifest=manifest,
            operator_ref=operator,
            reason_code=reason,
            plan_sha256=plan_sha256,
            lock=False,
        )
    return SuperadminCapabilityPlan(
        plan_sha256=plan_sha256,
        source_sha256=manifest.source_sha256,
        account_count=len(manifest.accounts),
        capability_count=len(targets),
        platform_admin_count=sum(
            target.capability_kind is SuperadminCapabilityKind.platform_admin
            for target in targets
        ),
        view_as_count=sum(
            target.capability_kind is SuperadminCapabilityKind.view_as
            for target in targets
        ),
        already_applied_count=len(exact_revoked),
        already_applied=not active and len(exact_revoked) == len(targets),
    )


def revoke_superadmin_capabilities(
    manifest: SuperadminCapabilityManifest,
    *,
    source_ref: str,
    operator_ref: str,
    reason_code: str,
    confirm_plan_sha256: str,
    now: datetime | None = None,
) -> SuperadminCapabilityApplyResult:
    """Atomically revoke the exact dry-run plan and retain every audit fact."""

    source, operator, reason = _operation_fields(
        source_ref=source_ref,
        operator_ref=operator_ref,
        reason_code=reason_code,
    )
    plan = plan_superadmin_capability_revocations(
        manifest,
        source_ref=source,
        operator_ref=operator,
        reason_code=reason,
    )
    confirmed = _normalize_digest(
        confirm_plan_sha256,
        field="confirm_plan_sha256",
    )
    if not hmac.compare_digest(confirmed, plan.plan_sha256):
        raise SuperadminCapabilityError("confirmed plan digest does not match")
    current = _aware(now)
    retain_until = _seven_calendar_years_after(current)

    with get_session() as session:
        targets, active, exact_revoked = _assess_revoke_plan(
            session,
            manifest=manifest,
            operator_ref=operator,
            reason_code=reason,
            plan_sha256=plan.plan_sha256,
            lock=True,
        )
        if len(exact_revoked) == len(targets):
            return SuperadminCapabilityApplyResult(
                plan_sha256=plan.plan_sha256,
                source_sha256=manifest.source_sha256,
                capability_count=len(targets),
                created_count=0,
                revoked_count=0,
                unchanged_count=len(targets),
            )
        for grant in active:
            grant.revoked_at = current
            grant.revoked_by_ref = operator
            grant.revocation_reason_code = reason
            grant.revocation_plan_sha256 = plan.plan_sha256
            grant.retain_until = retain_until
            session.add(grant)
            session.add(
                SuperadminCapabilityAudit(
                    grant_id=grant.id,
                    action=SuperadminCapabilityAuditAction.revoked,
                    environment=SuperadminCapabilityEnvironment(grant.environment),
                    capability_kind=SuperadminCapabilityKind(grant.capability_kind),
                    identity_binding_sha256=_identity_binding_sha256(
                        environment=SuperadminCapabilityEnvironment(grant.environment),
                        capability_kind=SuperadminCapabilityKind(grant.capability_kind),
                        clerk_issuer=grant.clerk_issuer,
                        clerk_subject=grant.clerk_subject,
                        doctor_id=grant.doctor_id,
                    ),
                    evidence_sha256=plan.plan_sha256,
                    operator_ref=operator,
                    reason_code=reason,
                    occurred_at=current,
                    recorded_at=current,
                )
            )
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise SuperadminCapabilityError(
                "a concurrent capability change prevented the batch"
            ) from exc
    return SuperadminCapabilityApplyResult(
        plan_sha256=plan.plan_sha256,
        source_sha256=manifest.source_sha256,
        capability_count=len(targets),
        created_count=0,
        revoked_count=len(active),
        unchanged_count=0,
    )


def get_active_superadmin_capability(
    session: Session,
    *,
    issuer: str,
    subject: str,
    doctor_id: TypeIDType,
    environment: SuperadminCapabilityEnvironment,
    capability_kind: SuperadminCapabilityKind,
    at: datetime | None = None,
) -> SuperadminCapabilityGrant | None:
    """Resolve one exact active grant without consulting email or DoctorRole."""

    try:
        normalized_issuer = _normalize_issuer(issuer)
        normalized_subject = _normalize_subject(subject)
        normalized_environment = _normalize_environment(environment)
        normalized_kind = _normalize_capability_kind(capability_kind)
        current = _aware(at)
    except SuperadminCapabilityError:
        return None
    grants = list(
        session.exec(
            select(SuperadminCapabilityGrant).where(
                SuperadminCapabilityGrant.environment == normalized_environment,
                SuperadminCapabilityGrant.capability_kind == normalized_kind,
                SuperadminCapabilityGrant.clerk_issuer == normalized_issuer,
                SuperadminCapabilityGrant.clerk_subject == normalized_subject,
                SuperadminCapabilityGrant.doctor_id == doctor_id,
                col(SuperadminCapabilityGrant.revoked_at).is_(None),
                SuperadminCapabilityGrant.granted_at <= current,
            )
        ).all()
    )
    if len(grants) != 1:
        return None
    grant = grants[0]
    if not hmac.compare_digest(
        grant.issuer_sha256, _sha256(normalized_issuer)
    ) or not hmac.compare_digest(grant.subject_sha256, _sha256(normalized_subject)):
        return None
    doctor = cast(Doctor | None, session.get(Doctor, doctor_id))
    if (
        doctor is None
        or doctor.deleted_at is not None
        or doctor.clerk_id != normalized_subject
    ):
        return None
    return grant


def get_runtime_superadmin_capability(
    session: Session,
    *,
    issuer: str,
    subject: str,
    doctor_id: TypeIDType,
    capability_kind: SuperadminCapabilityKind,
    at: datetime | None = None,
) -> SuperadminCapabilityGrant | None:
    """Resolve against this process's exact environment and Clerk issuer."""

    try:
        environment = configured_superadmin_capability_environment()
        configured_issuer = configured_superadmin_clerk_issuer()
    except SuperadminCapabilityError:
        return None
    if not isinstance(issuer, str) or not hmac.compare_digest(
        issuer, configured_issuer
    ):
        return None
    return get_active_superadmin_capability(
        session,
        issuer=issuer,
        subject=subject,
        doctor_id=doctor_id,
        environment=environment,
        capability_kind=capability_kind,
        at=at,
    )


def require_active_superadmin_capability(
    session: Session,
    *,
    issuer: str,
    subject: str,
    doctor_id: TypeIDType,
    environment: SuperadminCapabilityEnvironment,
    capability_kind: SuperadminCapabilityKind,
    at: datetime | None = None,
) -> SuperadminCapabilityGrant:
    grant = get_active_superadmin_capability(
        session,
        issuer=issuer,
        subject=subject,
        doctor_id=doctor_id,
        environment=environment,
        capability_kind=capability_kind,
        at=at,
    )
    if grant is None:
        raise SuperadminCapabilityError("required superadmin capability is unavailable")
    return grant


def list_superadmin_capability_summary(
    environment: SuperadminCapabilityEnvironment,
) -> SuperadminCapabilitySummary:
    """Return aggregate counts and one pseudonymous active-set digest."""

    normalized_environment = _normalize_environment(environment)
    with get_session() as session:
        grants = list(
            session.exec(
                select(SuperadminCapabilityGrant).where(
                    SuperadminCapabilityGrant.environment == normalized_environment
                )
            ).all()
        )
        audit_count = len(
            list(
                session.exec(
                    select(SuperadminCapabilityAudit).where(
                        SuperadminCapabilityAudit.environment == normalized_environment
                    )
                ).all()
            )
        )
    active = [grant for grant in grants if grant.revoked_at is None]
    active_bindings = sorted(
        _identity_binding_sha256(
            environment=normalized_environment,
            capability_kind=SuperadminCapabilityKind(grant.capability_kind),
            clerk_issuer=grant.clerk_issuer,
            clerk_subject=grant.clerk_subject,
            doctor_id=grant.doctor_id,
        )
        for grant in active
    )
    return SuperadminCapabilitySummary(
        environment=normalized_environment,
        total_count=len(grants),
        active_count=len(active),
        revoked_count=len(grants) - len(active),
        platform_admin_active_count=sum(
            grant.capability_kind == SuperadminCapabilityKind.platform_admin
            for grant in active
        ),
        view_as_active_count=sum(
            grant.capability_kind == SuperadminCapabilityKind.view_as
            for grant in active
        ),
        audit_count=audit_count,
        active_binding_sha256=_canonical_sha256(active_bindings),
    )
