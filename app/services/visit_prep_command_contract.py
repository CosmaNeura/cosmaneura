"""Visit Prep command inputs, validation and verifier-only token format.

Adapted from the pinned Visit Prep command service. No database writes or
patient-contact provider calls occur here. Sensitive values are excluded from
representations; recipient normalization never performs deliverability lookups.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from email_validator import EmailNotValidError, validate_email

from app.utils.encryption import EncryptionError, pseudonymous_digest

from activemodel.types import TypeIDType

RecipientRole = Literal["patient", "caregiver"]
MutationDisposition = Literal[
    "created",
    "replayed",
    "rotated",
    "revoked",
    "rescheduled",
    "reconciled",
]
RevocationReason = Literal[
    "physician_revoked",
    "wrong_recipient",
    "appointment_cancelled",
]

_CREATION_SOURCE = "physician_diy"
_CREATION_HMAC_CONTEXT = "visit-prep-creation-payload-v1"
_ACTOR_HMAC_CONTEXT = "visit-prep-physician-actor-v1"
_TOKEN_DOMAIN = b"cosmaneura:visit-prep-token:v1\x00"
_TOKEN_PREFIX = "vp1_"
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
_TIMEZONE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._+-]*(/[A-Za-z0-9._+-]+)*$")
_TOKEN_BODY = re.compile(r"^[A-Za-z0-9_-]{43}$")
_UNUSED_PURGE_AFTER = timedelta(days=30)
_UNUSED_HARD_PURGE_AFTER = timedelta(days=90)
_CREDENTIAL_AFTER_APPOINTMENT = timedelta(hours=72)


class VisitPrepError(Exception):
    """Base class for bounded, safe Visit Prep command failures."""


class VisitPrepNotFound(VisitPrepError):
    """The owner-scoped aggregate or related record does not exist."""


class VisitPrepValidation(VisitPrepError, ValueError):
    """The command does not satisfy the public Visit Prep contract."""


class VisitPrepConflict(VisitPrepError):
    """The command conflicts with the current aggregate generation or state."""


class VisitPrepUnavailable(VisitPrepError):
    """A required cryptographic or persistence prerequisite is unavailable."""


@dataclass(frozen=True)
class RecipientInput:
    first_name: str = field(repr=False)
    role: RecipientRole = "patient"
    email: str | None = field(default=None, repr=False)
    phone: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class VisitPrepResult:
    id: TypeIDType
    question_set_version_id: TypeIDType
    patient_id: TypeIDType | None
    appointment_id: TypeIDType | None
    lifecycle_state: str
    has_email_contact: bool
    has_sms_contact: bool
    physician_display_name: str
    practice_display_name: str
    scheduled_at: datetime
    timezone_name: str
    schedule_generation: int
    credential_generation: int
    credential_expires_at: datetime
    credential_active: bool
    revoked_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class VisitPrepMutationResult:
    visit_prep: VisitPrepResult
    disposition: MutationDisposition
    access_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _NormalizedRecipient:
    first_name: str = field(repr=False)
    role: RecipientRole
    email: str | None = field(repr=False)
    phone: str | None = field(repr=False)


def _normalized_text(value: object, *, maximum: int, message: str) -> str:
    if not isinstance(value, str):
        raise VisitPrepValidation(message)
    normalized = unicodedata.normalize("NFC", value).strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(
            unicodedata.category(character).startswith("C") for character in normalized
        )
    ):
        raise VisitPrepValidation(message)
    return normalized


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise VisitPrepValidation("idempotency key is invalid")
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise VisitPrepValidation("idempotency key is invalid") from exc
    if value != canonical:
        raise VisitPrepValidation("idempotency key is not canonical")
    return canonical


def _aware_utc(value: datetime | None, *, message: str) -> datetime:
    current = value or datetime.now(UTC)
    if (
        not isinstance(current, datetime)
        or current.tzinfo is None
        or current.utcoffset() is None
    ):
        raise VisitPrepValidation(message)
    return current.astimezone(UTC)


def _normalized_schedule(
    scheduled_at: datetime,
    timezone_name: object,
    *,
    now: datetime,
    require_future: bool = True,
) -> tuple[datetime, str]:
    if (
        not isinstance(scheduled_at, datetime)
        or scheduled_at.tzinfo is None
        or scheduled_at.utcoffset() is None
    ):
        raise VisitPrepValidation("scheduled time is invalid")
    timezone_value = _normalized_text(
        timezone_name,
        maximum=64,
        message="timezone is invalid",
    )
    if _TIMEZONE_NAME.fullmatch(timezone_value) is None:
        raise VisitPrepValidation("timezone is invalid")
    try:
        zone = ZoneInfo(timezone_value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise VisitPrepValidation("timezone is invalid") from exc

    try:
        scheduled_utc = scheduled_at.astimezone(UTC)
        represented = scheduled_utc.astimezone(zone)
    except (OverflowError, OSError, ValueError) as exc:
        raise VisitPrepValidation("scheduled time is invalid") from exc
    # Requiring the supplied wall clock and UTC offset to round-trip through
    # the named zone rejects mismatched offsets plus DST gaps/ambiguities.
    if (
        represented.replace(tzinfo=None) != scheduled_at.replace(tzinfo=None)
        or represented.utcoffset() != scheduled_at.utcoffset()
    ):
        raise VisitPrepValidation("scheduled time does not match timezone")
    if require_future and scheduled_utc <= now:
        raise VisitPrepValidation("scheduled time must be in the future")
    _schedule_deadlines(scheduled_utc)
    return scheduled_utc, timezone_value


def _schedule_deadlines(
    scheduled_at: datetime,
) -> tuple[datetime, datetime, datetime]:
    """Derive bounded retention and credential deadlines for one schedule."""

    try:
        return (
            scheduled_at + _UNUSED_PURGE_AFTER,
            scheduled_at + _UNUSED_HARD_PURGE_AFTER,
            scheduled_at + _CREDENTIAL_AFTER_APPOINTMENT,
        )
    except OverflowError as exc:
        raise VisitPrepValidation("scheduled time is invalid") from exc


def _normalize_recipient(recipient: RecipientInput) -> _NormalizedRecipient:
    if not isinstance(recipient, RecipientInput):
        raise VisitPrepValidation("recipient is invalid")
    first_name = _normalized_text(
        recipient.first_name,
        maximum=120,
        message="recipient first name is invalid",
    )
    if recipient.role not in {"patient", "caregiver"}:
        raise VisitPrepValidation("recipient role is invalid")

    email: str | None = None
    if recipient.email is not None:
        if not isinstance(recipient.email, str):
            raise VisitPrepValidation("recipient email is invalid")
        raw_email = recipient.email.strip()
        if raw_email:
            try:
                email = validate_email(
                    raw_email,
                    check_deliverability=False,
                ).normalized.lower()
            except EmailNotValidError as exc:
                raise VisitPrepValidation("recipient email is invalid") from exc
            if len(email) > 320:
                raise VisitPrepValidation("recipient email is invalid")

    phone: str | None = None
    if recipient.phone is not None:
        if not isinstance(recipient.phone, str):
            raise VisitPrepValidation("recipient phone is invalid")
        raw_phone = recipient.phone.strip()
        if raw_phone:
            if _E164.fullmatch(raw_phone) is None:
                raise VisitPrepValidation("recipient phone is invalid")
            phone = raw_phone

    if email is None and phone is None:
        raise VisitPrepValidation("recipient contact is required")
    return _NormalizedRecipient(
        first_name=first_name,
        role=cast(RecipientRole, recipient.role),
        email=email,
        phone=phone,
    )


def _recipient_plaintext(recipient: _NormalizedRecipient) -> str:
    return json.dumps(
        {
            "email": recipient.email,
            "first_name": recipient.first_name,
            "phone": recipient.phone,
            "role": recipient.role,
            "version": 1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _creation_payload_hmac(
    *,
    owner_practice_member_id: TypeIDType,
    question_set_version_id: TypeIDType,
    recipient: _NormalizedRecipient,
    physician_display_name: str,
    practice_display_name: str,
    scheduled_at: datetime,
    timezone_name: str,
    patient_id: TypeIDType | None,
    appointment_id: TypeIDType | None,
) -> str:
    payload = json.dumps(
        {
            "appointment_id": (
                str(appointment_id) if appointment_id is not None else None
            ),
            "creation_source": _CREATION_SOURCE,
            "owner_practice_member_id": str(owner_practice_member_id),
            "patient_id": str(patient_id) if patient_id is not None else None,
            "physician_display_name": physician_display_name,
            "practice_display_name": practice_display_name,
            "question_set_version_id": str(question_set_version_id),
            "recipient": asdict(recipient),
            "scheduled_at": scheduled_at.isoformat(),
            "timezone_name": timezone_name,
            "version": 1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        return pseudonymous_digest(payload, context=_CREATION_HMAC_CONTEXT)
    except EncryptionError as exc:
        raise VisitPrepUnavailable("creation fingerprint unavailable") from exc


def _actor_ref(owner_doctor_id: TypeIDType) -> str:
    try:
        digest = pseudonymous_digest(
            str(owner_doctor_id),
            context=_ACTOR_HMAC_CONTEXT,
        )
    except EncryptionError as exc:
        raise VisitPrepUnavailable("actor reference unavailable") from exc
    return f"physician:{digest}"


def _new_access_token() -> str:
    token_body = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=")
    token = _TOKEN_PREFIX + token_body.decode("ascii")
    if _TOKEN_BODY.fullmatch(token.removeprefix(_TOKEN_PREFIX)) is None:
        raise VisitPrepUnavailable("credential generation unavailable")
    return token


def access_token_hash(token: str) -> str:
    """Hash one canonical Visit Prep bearer for later public verification."""

    if not isinstance(token, str) or not token.startswith(_TOKEN_PREFIX):
        raise VisitPrepValidation("credential is invalid")
    body = token.removeprefix(_TOKEN_PREFIX)
    if _TOKEN_BODY.fullmatch(body) is None:
        raise VisitPrepValidation("credential is invalid")
    try:
        decoded = base64.urlsafe_b64decode(body + "=")
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise VisitPrepValidation("credential is invalid") from exc
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if len(decoded) != 32 or not hmac.compare_digest(canonical, body):
        raise VisitPrepValidation("credential is invalid")
    return hashlib.sha256(_TOKEN_DOMAIN + token.encode("ascii")).hexdigest()
