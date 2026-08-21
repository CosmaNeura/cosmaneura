"""Versioned application encryption and pseudonymisation primitives.

``ENCRYPTION_KEY`` accepts either the historical bare Fernet key or a JSON key
ring. The latter keeps data-encryption rotation independent from the stable
root used to pseudonymise provider identifiers::

    {
      "version": 1,
      "active_key_id": "enc-2026-08",
      "encryption_keys": {
        "enc-2026-08": "<urlsafe-base64 Fernet key>",
        "enc-2026-01": "<retired-but-readable Fernet key>"
      },
      "pseudonym_root_key_id": "provider-ref-v1",
      "pseudonym_root_key": "<urlsafe-base64 32-byte key>"
    }

The bare-key form remains read/write compatible with old application replicas
and gets a deterministic, domain-separated pseudonym root. Explicit key-ring
configurations write ciphertext tagged with their format and key ID; untagged
historical Fernet tokens are tried against every configured key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Literal, Never, TypeGuard

from cryptography.fernet import Fernet, InvalidToken

from app.configuration.encryption import ENCRYPTION_KEY

from sqlalchemy import types

_CIPHERTEXT_PREFIX = "enc"
_CIPHERTEXT_VERSION = "v1"
_LEGACY_KEY_ID = "legacy"
_LEGACY_PSEUDONYM_KEY_ID = "legacy-derived"
_PSEUDONYM_DOMAIN = b"cosmaneura:pseudonym:v1\x00"
_PSEUDONYM_ROOT_DOMAIN = b"cosmaneura:pseudonym-root:v1"
_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MAX_KEYS = 32
_MAX_CONTEXT_BYTES = 128

ConfigurationErrorCode = Literal[
    "invalid_configuration",
    "invalid_key",
    "invalid_key_id",
    "missing_active_key",
]
DecryptionErrorCode = Literal[
    "invalid_ciphertext",
    "unknown_key",
    "unsupported_version",
]


class EncryptionConfigurationError(ValueError):
    """A bounded, non-secret-bearing key-ring configuration failure."""

    def __init__(self, code: ConfigurationErrorCode = "invalid_configuration") -> None:
        self.code = code
        super().__init__(code)


class EncryptionError(RuntimeError):
    """A bounded encryption failure that never embeds plaintext or key data."""

    code = "encryption_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


class DecryptionError(RuntimeError):
    """A bounded decryption failure that never embeds ciphertext or key data."""

    def __init__(self, code: DecryptionErrorCode = "invalid_ciphertext") -> None:
        self.code = code
        super().__init__(code)


def _configuration_error(code: ConfigurationErrorCode) -> Never:
    raise EncryptionConfigurationError(code) from None


def _valid_key_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _KEY_ID_PATTERN.fullmatch(value) is not None


def _decode_32_byte_key(value: object) -> bytes:
    if not isinstance(value, str):
        _configuration_error("invalid_key")
    try:
        decoded = base64.b64decode(value, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        _configuration_error("invalid_key")
    if len(decoded) != 32:
        _configuration_error("invalid_key")
    return decoded


def _fernet(value: object) -> Fernet:
    if not isinstance(value, str):
        _configuration_error("invalid_key")
    try:
        return Fernet(value.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        _configuration_error("invalid_key")


@dataclass(frozen=True)
class EncryptionKeyRing:
    """Immutable encryption-key ring with a separate pseudonym root."""

    active_key_id: str
    pseudonym_root_key_id: str
    _keys: tuple[tuple[str, Fernet], ...] = field(repr=False)
    _pseudonym_root_key: bytes = field(repr=False)
    _legacy_write_format: bool = field(default=False, repr=False)

    @classmethod
    def from_config(cls, config_value: str) -> EncryptionKeyRing:
        """Parse a bare Fernet key or the explicit version-one JSON format."""

        if not isinstance(config_value, str) or not config_value.strip():
            _configuration_error("invalid_configuration")
        value = config_value.strip()
        if not value.startswith("{"):
            fernet = _fernet(value)
            raw_key = _decode_32_byte_key(value)
            pseudonym_root = hmac.new(
                raw_key,
                _PSEUDONYM_ROOT_DOMAIN,
                hashlib.sha256,
            ).digest()
            return cls(
                active_key_id=_LEGACY_KEY_ID,
                pseudonym_root_key_id=_LEGACY_PSEUDONYM_KEY_ID,
                _keys=((_LEGACY_KEY_ID, fernet),),
                _pseudonym_root_key=pseudonym_root,
                _legacy_write_format=True,
            )

        try:
            config = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError):
            _configuration_error("invalid_configuration")
        if not isinstance(config, dict) or config.get("version") != 1:
            _configuration_error("invalid_configuration")

        active_key_id = config.get("active_key_id")
        pseudonym_root_key_id = config.get("pseudonym_root_key_id")
        if not _valid_key_id(active_key_id) or not _valid_key_id(pseudonym_root_key_id):
            _configuration_error("invalid_key_id")

        configured_keys = config.get("encryption_keys")
        if (
            not isinstance(configured_keys, dict)
            or not configured_keys
            or len(configured_keys) > _MAX_KEYS
        ):
            _configuration_error("invalid_configuration")

        keys: list[tuple[str, Fernet]] = []
        for key_id, key_value in configured_keys.items():
            if not _valid_key_id(key_id):
                _configuration_error("invalid_key_id")
            keys.append((key_id, _fernet(key_value)))
        if active_key_id not in configured_keys:
            _configuration_error("missing_active_key")

        pseudonym_root = _decode_32_byte_key(config.get("pseudonym_root_key"))
        return cls(
            active_key_id=active_key_id,
            pseudonym_root_key_id=pseudonym_root_key_id,
            _keys=tuple(keys),
            _pseudonym_root_key=pseudonym_root,
        )

    def _key(self, key_id: str) -> Fernet | None:
        return next((key for candidate, key in self._keys if candidate == key_id), None)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt text into a versioned envelope using the active key."""

        try:
            key = self._key(self.active_key_id)
            if key is None:  # Construction prevents this; retain a bounded failure.
                raise EncryptionError
            token = key.encrypt(plaintext.encode("utf-8")).decode("ascii")
        except EncryptionError:
            raise
        except Exception:
            raise EncryptionError from None
        if self._legacy_write_format:
            # During a rolling deployment an older replica must still be able
            # to read OAuth tokens refreshed by this code. Versioned envelopes
            # begin only after all replicas understand the key-ring format.
            return token
        return (
            f"{_CIPHERTEXT_PREFIX}:{_CIPHERTEXT_VERSION}:{self.active_key_id}:{token}"
        )

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt tagged ciphertext or an untagged historical Fernet token."""

        if not isinstance(ciphertext, str) or not ciphertext:
            raise DecryptionError from None

        if ciphertext.startswith(f"{_CIPHERTEXT_PREFIX}:"):
            parts = ciphertext.split(":", 3)
            if len(parts) != 4 or not parts[2] or not parts[3]:
                raise DecryptionError from None
            _, version, key_id, token = parts
            if version != _CIPHERTEXT_VERSION:
                raise DecryptionError("unsupported_version") from None
            if not _valid_key_id(key_id):
                raise DecryptionError from None
            if key_id == _LEGACY_KEY_ID:
                # Bare-key deployments cannot assign a durable key ID. Trying
                # the configured ring preserves ciphertext written during the
                # transition from the legacy setting to the explicit format.
                for _, candidate in self._keys:
                    try:
                        return _decrypt_token(candidate, token)
                    except DecryptionError:
                        continue
                raise DecryptionError from None
            key = self._key(key_id)
            if key is None:
                raise DecryptionError("unknown_key") from None
            return _decrypt_token(key, token)

        for _, key in self._keys:
            try:
                return key.decrypt(ciphertext.encode("ascii")).decode("utf-8")
            except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError):
                continue
        raise DecryptionError from None

    def pseudonymous_digest(self, value: str, *, context: str) -> str:
        """Return a deterministic, context-separated keyed SHA-256 digest."""

        if not isinstance(value, str) or not isinstance(context, str):
            raise EncryptionError from None
        context_bytes = context.encode("utf-8")
        if not context_bytes or len(context_bytes) > _MAX_CONTEXT_BYTES:
            raise EncryptionError from None
        value_bytes = value.encode("utf-8")
        message = (
            _PSEUDONYM_DOMAIN
            + len(context_bytes).to_bytes(2, "big")
            + context_bytes
            + len(value_bytes).to_bytes(8, "big")
            + value_bytes
        )
        return hmac.new(
            self._pseudonym_root_key,
            message,
            hashlib.sha256,
        ).hexdigest()


def _decrypt_token(key: Fernet, token: str) -> str:
    try:
        return key.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError):
        raise DecryptionError from None


_key_ring = EncryptionKeyRing.from_config(ENCRYPTION_KEY)


def encrypt_value(value: str) -> str:
    """Encrypt an application string with the configured key ring."""

    return _key_ring.encrypt(value)


def decrypt_value(value: str) -> str:
    """Decrypt an application string with bounded, non-sensitive failures."""

    return _key_ring.decrypt(value)


def encrypt_inbox_payload(value: str) -> str:
    """Explicit encryption boundary for durable external-webhook payloads."""

    return encrypt_value(value)


def decrypt_inbox_payload(value: str) -> str:
    """Explicit decryption boundary for durable external-webhook payloads."""

    return decrypt_value(value)


def pseudonymous_digest(value: str, *, context: str) -> str:
    """Digest a private identifier using the configured stable root key."""

    return _key_ring.pseudonymous_digest(value, context=context)


class EncryptedType(types.TypeDecorator[str]):
    """SQLAlchemy type that transparently applies authenticated encryption."""

    impl = types.Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return encrypt_value(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return decrypt_value(value)
