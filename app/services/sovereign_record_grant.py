from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryptography.fernet import Fernet
from decouple import config

RECORD_GRANT_AUDIENCE = "cosmaneura-sovereign"
RECORD_GRANT_ISSUER = "cosmaneura-platform"
RECORD_GRANT_TTL_SECONDS = 60
RECORD_GRANT_PATTERN = r"^[A-Za-z0-9_-]{80,4096}={0,2}$"

_OPAQUE_REF = re.compile(r"^[A-Za-z0-9._~-]+$")
CANONICAL_FERNET_KEY_PATTERN = r"^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]=$"
_CANONICAL_FERNET_KEY = re.compile(CANONICAL_FERNET_KEY_PATTERN)


class RecordGrantConfigurationError(RuntimeError):
    """The provider-only record-grant key is missing or malformed."""


@dataclass(frozen=True)
class RecordGrantScope:
    actor_ref: str
    doctor_ref: str
    appointment_ref: str
    patient_ref: str
    vendor_record_ref: str


def configured_record_grant_key() -> bytes:
    """Load one canonical urlsafe-base64 Fernet key without logging its value.

    Python's permissive base64 decoders accept whitespace, the standard base64
    alphabet, and encodings with non-zero discarded pad bits. Secret material
    must have one exact representation so deployment validation cannot disagree
    with the runtime about which key was configured.
    """

    raw = config("SOVEREIGN_RECORD_GRANT_SECRET", default="", cast=str)
    try:
        encoded = raw.encode("ascii")
        decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (ValueError, UnicodeError):
        encoded = b""
        decoded = b""
    if (
        _CANONICAL_FERNET_KEY.fullmatch(raw) is None
        or len(decoded) != 32
        or base64.urlsafe_b64encode(decoded) != encoded
    ):
        raise RecordGrantConfigurationError(
            "SOVEREIGN_RECORD_GRANT_SECRET must be a Fernet key"
        )
    return encoded


def _opaque(value: str, *, field: str, maximum: int = 160) -> str:
    if not 3 <= len(value) <= maximum or not _OPAQUE_REF.fullmatch(value):
        raise ValueError(f"{field} must be an opaque reference")
    return value


def issue_record_grant(
    scope: RecordGrantScope,
    *,
    key: bytes,
    now: datetime | None = None,
) -> str:
    """Encrypt and authenticate one actor-bound synthetic Preview record grant."""

    issued_at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    expires_at = issued_at + timedelta(seconds=RECORD_GRANT_TTL_SECONDS)
    payload = {
        "actor_ref": _opaque(scope.actor_ref, field="actor_ref"),
        "adapter": "athena",
        "appointment_ref": _opaque(scope.appointment_ref, field="appointment_ref"),
        "audience": RECORD_GRANT_AUDIENCE,
        "doctor_ref": _opaque(scope.doctor_ref, field="doctor_ref"),
        "environment": "preview",
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "issuer": RECORD_GRANT_ISSUER,
        "nonce": str(uuid4()),
        "patient_ref": _opaque(scope.patient_ref, field="patient_ref"),
        "practice_ref": "195900",
        "synthetic_only": True,
        "tier": "sandbox",
        "vendor_record_ref": _opaque(
            scope.vendor_record_ref, field="vendor_record_ref"
        ),
        "version": 1,
    }
    plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return (
        Fernet(key)
        .encrypt_at_time(
            plaintext,
            current_time=int(issued_at.timestamp()),
        )
        .decode("ascii")
    )
