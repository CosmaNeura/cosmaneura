import base64
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.services.sovereign_record_grant import (
    RECORD_GRANT_AUDIENCE,
    RECORD_GRANT_ISSUER,
    RECORD_GRANT_TTL_SECONDS,
    RecordGrantConfigurationError,
    RecordGrantScope,
    configured_record_grant_key,
    issue_record_grant,
)


def test_record_grant_key_requires_one_exact_canonical_fernet_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = base64.urlsafe_b64encode(b"\xfb\xff" + (b"\x00" * 30)).decode()
    assert "-" in canonical and "_" in canonical

    monkeypatch.setenv("SOVEREIGN_RECORD_GRANT_SECRET", canonical)
    assert configured_record_grant_key() == canonical.encode()

    # Standard-base64 spelling decodes to the same bytes but is not Fernet's
    # canonical urlsafe spelling.
    standard_alphabet = base64.b64encode(b"\xfb\xff" + (b"\x00" * 30)).decode()

    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    final_index = alphabet.index(canonical[-2])
    # A 32-byte value leaves two discarded pad bits. Changing only those bits
    # creates a decoder-equivalent but non-canonical encoding.
    nonzero_pad_bits = canonical[:-2] + alphabet[final_index + 1] + "="
    assert base64.urlsafe_b64decode(nonzero_pad_bits) == base64.urlsafe_b64decode(
        canonical
    )

    for malformed in (
        "",
        f" {canonical}",
        f"{canonical}\n",
        standard_alphabet,
        canonical.rstrip("="),
        canonical[:-1] + "==",
        nonzero_pad_bits,
        "é" * 44,
    ):
        monkeypatch.setenv("SOVEREIGN_RECORD_GRANT_SECRET", malformed)
        with pytest.raises(RecordGrantConfigurationError):
            configured_record_grant_key()


def test_record_grant_is_actor_and_full_open_record_scope_bound() -> None:
    key = Fernet.generate_key()
    now = datetime(2026, 8, 17, 14, 30, tzinfo=UTC)
    grant = issue_record_grant(
        RecordGrantScope(
            actor_ref="user_provider_123",
            doctor_ref="doctor_01k00000000000000000000000",
            appointment_ref="appointment_01k00000000000000000000000",
            patient_ref="patient_01k00000000000000000000000",
            vendor_record_ref="a-195900.E-60178",
        ),
        key=key,
        now=now,
    )

    claims = json.loads(
        Fernet(key).decrypt_at_time(
            grant.encode(),
            ttl=RECORD_GRANT_TTL_SECONDS,
            current_time=int(now.timestamp()) + RECORD_GRANT_TTL_SECONDS,
        )
    )
    assert claims == {
        "actor_ref": "user_provider_123",
        "adapter": "athena",
        "appointment_ref": "appointment_01k00000000000000000000000",
        "audience": RECORD_GRANT_AUDIENCE,
        "doctor_ref": "doctor_01k00000000000000000000000",
        "environment": "preview",
        "expires_at": "2026-08-17T14:31:00Z",
        "issued_at": "2026-08-17T14:30:00Z",
        "issuer": RECORD_GRANT_ISSUER,
        "nonce": claims["nonce"],
        "patient_ref": "patient_01k00000000000000000000000",
        "practice_ref": "195900",
        "synthetic_only": True,
        "tier": "sandbox",
        "vendor_record_ref": "a-195900.E-60178",
        "version": 1,
    }
    assert str(UUID(claims["nonce"])) == claims["nonce"]

    with pytest.raises(InvalidToken):
        Fernet(key).decrypt_at_time(
            grant.encode(),
            ttl=RECORD_GRANT_TTL_SECONDS,
            current_time=int(now.timestamp()) + RECORD_GRANT_TTL_SECONDS + 1,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("actor_ref", "actor with spaces"),
        ("doctor_ref", "../doctor"),
        ("appointment_ref", "x"),
        ("patient_ref", "patient/ref"),
        ("vendor_record_ref", "Jane Smith"),
    ],
)
def test_record_grant_rejects_nonopaque_scope(field: str, value: str) -> None:
    values = {
        "actor_ref": "user_provider_123",
        "doctor_ref": "doctor_01k00000000000000000000000",
        "appointment_ref": "appointment_01k00000000000000000000000",
        "patient_ref": "patient_01k00000000000000000000000",
        "vendor_record_ref": "a-195900.E-60178",
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        issue_record_grant(
            RecordGrantScope(**values),  # type: ignore[arg-type]
            key=Fernet.generate_key(),
        )
