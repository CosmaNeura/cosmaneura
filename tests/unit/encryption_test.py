import json

import pytest
from cryptography.fernet import Fernet

from app.utils import encryption

from sqlalchemy.engine.default import DefaultDialect


def _key() -> str:
    return Fernet.generate_key().decode("ascii")


def _config(
    *,
    active_key_id: str,
    keys: dict[str, str],
    pseudonym_root_key: str,
) -> str:
    return json.dumps(
        {
            "version": 1,
            "active_key_id": active_key_id,
            "encryption_keys": keys,
            "pseudonym_root_key_id": "provider-ref-v1",
            "pseudonym_root_key": pseudonym_root_key,
        }
    )


def test_bare_key_remains_rolling_deploy_compatible() -> None:
    key = _key()
    ring = encryption.EncryptionKeyRing.from_config(key)
    legacy = Fernet(key.encode("ascii")).encrypt(b"legacy oauth token").decode("ascii")

    assert ring.decrypt(legacy) == "legacy oauth token"
    new_ciphertext = ring.encrypt("new oauth token")
    assert ring.decrypt(new_ciphertext) == "new oauth token"
    assert not new_ciphertext.startswith("enc:")


def test_json_key_ring_versioned_round_trip() -> None:
    key = _key()
    ring = encryption.EncryptionKeyRing.from_config(
        _config(
            active_key_id="active-2026-08",
            keys={"active-2026-08": key},
            pseudonym_root_key=_key(),
        )
    )

    ciphertext = ring.encrypt("clinical payload")

    assert ciphertext.startswith("enc:v1:active-2026-08:")
    assert "clinical payload" not in ciphertext
    assert ring.decrypt(ciphertext) == "clinical payload"


def test_active_key_rotation_reads_tagged_and_legacy_old_ciphertext() -> None:
    old_key = _key()
    new_key = _key()
    pseudonym_root = _key()
    old_ring = encryption.EncryptionKeyRing.from_config(
        _config(
            active_key_id="old",
            keys={"old": old_key},
            pseudonym_root_key=pseudonym_root,
        )
    )
    transitional_bare_ring = encryption.EncryptionKeyRing.from_config(old_key)
    rotated_ring = encryption.EncryptionKeyRing.from_config(
        _config(
            active_key_id="new",
            keys={"new": new_key, "old": old_key},
            pseudonym_root_key=pseudonym_root,
        )
    )
    tagged_old = old_ring.encrypt("tagged old value")
    transitional_legacy = transitional_bare_ring.encrypt("bare transition value")
    legacy_old = Fernet(old_key.encode("ascii")).encrypt(b"legacy old value").decode()

    assert rotated_ring.decrypt(tagged_old) == "tagged old value"
    assert rotated_ring.decrypt(transitional_legacy) == "bare transition value"
    assert rotated_ring.decrypt(legacy_old) == "legacy old value"
    assert rotated_ring.encrypt("new value").startswith("enc:v1:new:")


def test_pseudonym_digest_is_stable_across_active_key_rotation() -> None:
    pseudonym_root = _key()
    first_ring = encryption.EncryptionKeyRing.from_config(
        _config(
            active_key_id="first",
            keys={"first": _key()},
            pseudonym_root_key=pseudonym_root,
        )
    )
    rotated_ring = encryption.EncryptionKeyRing.from_config(
        _config(
            active_key_id="second",
            keys={"second": _key(), "first": _key()},
            pseudonym_root_key=pseudonym_root,
        )
    )

    assert first_ring.pseudonymous_digest(
        "provider-id", context="provider-reference"
    ) == rotated_ring.pseudonymous_digest("provider-id", context="provider-reference")


def test_invalid_configuration_and_ciphertext_have_bounded_failures() -> None:
    with pytest.raises(encryption.EncryptionConfigurationError) as invalid_config:
        encryption.EncryptionKeyRing.from_config("not-a-fernet-key")
    assert invalid_config.value.code == "invalid_key"
    assert str(invalid_config.value) == "invalid_key"

    key = _key()
    ring = encryption.EncryptionKeyRing.from_config(
        _config(
            active_key_id="known",
            keys={"known": key},
            pseudonym_root_key=_key(),
        )
    )
    sentinel = "private-ciphertext-sentinel"
    with pytest.raises(encryption.DecryptionError) as corrupt:
        ring.decrypt(f"enc:v1:known:{sentinel}")
    assert corrupt.value.code == "invalid_ciphertext"
    assert sentinel not in str(corrupt.value)

    with pytest.raises(encryption.DecryptionError) as unknown:
        ring.decrypt(
            f"enc:v1:retired:{Fernet(key.encode()).encrypt(b'value').decode()}"
        )
    assert unknown.value.code == "unknown_key"
    assert "retired" not in str(unknown.value)


def test_keyed_digest_is_deterministic_context_separated_and_omits_raw_value() -> None:
    ring = encryption.EncryptionKeyRing.from_config(
        _config(
            active_key_id="active",
            keys={"active": _key()},
            pseudonym_root_key=_key(),
        )
    )
    raw_value = "patient-and-provider-private-id"

    digest = ring.pseudonymous_digest(raw_value, context="provider-reference")

    assert digest == ring.pseudonymous_digest(raw_value, context="provider-reference")
    assert digest != ring.pseudonymous_digest(raw_value, context="delivery-id")
    assert raw_value not in digest
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


def test_explicit_helpers_and_encrypted_type_share_the_compatible_boundary(
    monkeypatch,
) -> None:
    key = _key()
    ring = encryption.EncryptionKeyRing.from_config(key)
    monkeypatch.setattr(encryption, "_key_ring", ring)
    encrypted_type = encryption.EncryptedType()
    dialect = DefaultDialect()

    inbox_ciphertext = encryption.encrypt_inbox_payload("inbox payload")
    oauth_ciphertext = encrypted_type.process_bind_param("oauth token", dialect)

    assert encryption.decrypt_inbox_payload(inbox_ciphertext) == "inbox payload"
    assert (
        encrypted_type.process_result_value(oauth_ciphertext, dialect) == "oauth token"
    )
    assert encrypted_type.process_bind_param(None, dialect) is None
    assert encrypted_type.process_result_value(None, dialect) is None
