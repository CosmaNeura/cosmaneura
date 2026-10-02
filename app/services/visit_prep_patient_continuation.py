"""Encrypted, invitation-bound remembered confirmation; never an account login.

The cookie contains no bearer, contact, patient name, answer, or network signal.
Every use must still pass the interview's current locked authority checks.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from app.utils.encryption import decrypt_value, encrypt_value

COOKIE_NAME = "__Host-cosmas_visit_prep_confirmation"
REQUEST_HEADER = "X-Visit-Prep-Continuation"
MAX_COOKIE_BYTES = 2048
_PURPOSE = "visit-prep.patient-continuation.v1"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{64}$")


class PatientContinuationInvalid(ValueError):
    def __init__(self) -> None:
        super().__init__("patient_continuation_invalid")


@dataclass(frozen=True)
class ConfirmedVisitPrepAccess:
    token_hash: str = field(repr=False)
    actor_role: Literal["patient", "caregiver"]
    confirmed_at: datetime
    expires_at: datetime


def valid_confirmation_name(
    first_name: str, access: str | ConfirmedVisitPrepAccess
) -> bool:
    """An empty internal marker is allowed only with a sealed confirmation.

    Public adapters never accept an empty name from a request; they create
    this marker after verifying the encrypted invitation-bound cookie.
    Current role and authority are still checked under the interview lock.
    """
    return type(first_name) is str and (
        1 <= len(first_name) <= 120
        or (first_name == "" and isinstance(access, ConfirmedVisitPrepAccess))
    )


def _validate(access: ConfirmedVisitPrepAccess) -> None:
    if (
        type(access.token_hash) is not str
        or _HASH.fullmatch(access.token_hash) is None
        or access.actor_role not in {"patient", "caregiver"}
        or any(
            not isinstance(value, datetime) or value.utcoffset() is None
            for value in (access.confirmed_at, access.expires_at)
        )
        or access.confirmed_at >= access.expires_at
    ):
        raise PatientContinuationInvalid


def seal_confirmation(access: ConfirmedVisitPrepAccess) -> str:
    """Issue only after the initial recipient confirmation commits successfully."""

    try:
        _validate(access)
        cookie = encrypt_value(
            json.dumps(
                {
                    "purpose": _PURPOSE,
                    "token_hash": access.token_hash,
                    "actor_role": access.actor_role,
                    "confirmed_at": access.confirmed_at.astimezone(UTC).isoformat(),
                    "expires_at": access.expires_at.astimezone(UTC).isoformat(),
                    "nonce": secrets.token_hex(32),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if len(cookie.encode("ascii")) > MAX_COOKIE_BYTES:
            raise PatientContinuationInvalid
        return cookie
    except Exception:
        raise PatientContinuationInvalid from None


def open_confirmation(cookie: str) -> ConfirmedVisitPrepAccess:
    """Validate cryptographic purpose/shape, not live authorization or expiry."""

    try:
        if not isinstance(cookie, str) or not 1 <= len(cookie) <= MAX_COOKIE_BYTES:
            raise PatientContinuationInvalid
        payload = json.loads(decrypt_value(cookie))
        if (
            type(payload) is not dict
            or set(payload)
            != {
                "purpose",
                "token_hash",
                "actor_role",
                "confirmed_at",
                "expires_at",
                "nonce",
            }
            or payload["purpose"] != _PURPOSE
            or type(payload["nonce"]) is not str
            or _NONCE.fullmatch(payload["nonce"]) is None
            or type(payload["confirmed_at"]) is not str
            or type(payload["expires_at"]) is not str
        ):
            raise PatientContinuationInvalid
        access = ConfirmedVisitPrepAccess(
            token_hash=payload["token_hash"],
            actor_role=payload["actor_role"],
            confirmed_at=datetime.fromisoformat(payload["confirmed_at"]),
            expires_at=datetime.fromisoformat(payload["expires_at"]),
        )
        _validate(access)
        return access
    except Exception:
        raise PatientContinuationInvalid from None
