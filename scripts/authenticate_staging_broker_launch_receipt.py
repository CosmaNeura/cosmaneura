#!/usr/bin/env python3
"""Offline authentication for one canonical staging broker launch receipt.

The sealed proof prevents accidental API misuse. It is not intended to resist
hostile code executing in the same Python process.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, NoReturn

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

try:
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # pragma: no cover - direct script import fallback
    import validate_staging_restore_runtime_binding as runtime_binding


_FAILURE = "broker_receipt_signature.invalid"
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_P256_ORDER = int(
    "ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16
)
_VERIFIED_SIGNATURE_SEAL = object()


class BrokerLaunchReceiptAuthenticationFailure(RuntimeError):
    """A bounded authentication failure safe to report without input contents."""


def _fail() -> NoReturn:
    raise BrokerLaunchReceiptAuthenticationFailure(_FAILURE)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        _fail()


def _parse_canonical_object(raw: Any, *, maximum: int) -> dict[str, Any]:
    if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
        _fail()

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                _fail()
            result[key] = value
        return result

    def nonfinite(_value: str) -> NoReturn:
        _fail()

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=nonfinite,
            parse_float=lambda value: (
                number if math.isfinite(number := float(value)) else nonfinite(value)
            ),
        )
    except BrokerLaunchReceiptAuthenticationFailure:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        _fail()
    if type(value) is not dict or raw != _canonical(value):
        _fail()
    return value


def _base64url(value: Any, *, decoded_bytes: int) -> bytes:
    if type(value) is not str or "=" in value or _BASE64URL_RE.fullmatch(value) is None:
        _fail()
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, binascii.Error):
        _fail()
    if (
        len(decoded) != decoded_bytes
        or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value
    ):
        _fail()
    return decoded


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


@dataclass(frozen=True, slots=True, init=False)
class VerifiedBrokerLaunchReceiptSignature:
    """Sealed proof that one exact receipt has the policy-pinned ES256 signature."""

    receipt_bytes: bytes = dataclass_field(repr=False)
    receipt_sha256: str
    signature_envelope_bytes: bytes = dataclass_field(repr=False)
    signature_envelope_sha256: str
    policy_id: str
    policy_binding_sha256: str
    algorithm: str
    signing_key_resource_id: str
    signing_key_version: str
    public_key_sha256: str
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail()

    def _matches(
        self,
        *,
        policy: Any,
        receipt_bytes: Any,
        signature_envelope_bytes: Any,
    ) -> bool:
        try:
            document = runtime_binding.validate_runtime_binding_policy(
                policy, executable=True
            )
            signing = document["launch_binding"]["receipt_signing"]
            return (
                type(self) is VerifiedBrokerLaunchReceiptSignature
                and getattr(self, "_seal", None) is _VERIFIED_SIGNATURE_SEAL
                and type(receipt_bytes) is bytes
                and type(signature_envelope_bytes) is bytes
                and self.receipt_bytes == receipt_bytes
                and self.receipt_sha256 == _digest(receipt_bytes)
                and self.signature_envelope_bytes == signature_envelope_bytes
                and self.signature_envelope_sha256 == _digest(signature_envelope_bytes)
                and self.policy_id == document["policy_id"]
                and self.policy_binding_sha256
                == runtime_binding._policy_binding_digest(document)
                and self.algorithm == signing["algorithm"]
                and self.signing_key_resource_id == signing["key_resource_id"]
                and self.signing_key_version == signing["key_version"]
                and self.public_key_sha256 == signing["public_key_sha256"]
            )
        except (KeyError, TypeError, runtime_binding.RuntimeBindingFailure):
            return False


def authenticate_broker_launch_receipt_signature(
    policy: Any,
    *,
    receipt_bytes: bytes,
    signature_envelope_bytes: bytes,
) -> VerifiedBrokerLaunchReceiptSignature:
    """Verify one exact canonical receipt with the exact policy-pinned P-256 key."""

    try:
        document = runtime_binding.validate_runtime_binding_policy(
            policy, executable=True
        )
        signing = document["launch_binding"]["receipt_signing"]
        receipt = _parse_canonical_object(
            receipt_bytes,
            maximum=document["launch_binding"]["launch_receipt"]["maximum_bytes"],
        )
        del receipt
        envelope_policy = signing["signature_envelope"]
        envelope = _parse_canonical_object(
            signature_envelope_bytes, maximum=envelope_policy["maximum_bytes"]
        )
        if (
            set(envelope) != set(envelope_policy["required_fields"])
            or list(envelope) != sorted(envelope)
            or type(envelope["schema_version"]) is not int
            or envelope["schema_version"] != envelope_policy["schema_version"]
        ):
            _fail()

        policy_binding_sha256 = runtime_binding._policy_binding_digest(document)
        receipt_sha256 = _digest(receipt_bytes)
        expected = {
            "algorithm": signing["algorithm"],
            "policy_binding_sha256": policy_binding_sha256,
            "policy_id": document["policy_id"],
            "public_key_sha256": signing["public_key_sha256"],
            "receipt_sha256": receipt_sha256,
            "schema_version": envelope_policy["schema_version"],
            "signing_key_resource_id": signing["key_resource_id"],
            "signing_key_version": signing["key_version"],
        }
        if any(envelope.get(name) != value for name, value in expected.items()):
            _fail()

        jwk = signing["public_key_jwk"]
        if (
            type(jwk) is not dict
            or set(jwk) != {"crv", "kty", "x", "y"}
            or jwk["kty"] != "EC"
            or jwk["crv"] != "P-256"
            or signing["public_key_sha256"] != _digest(_canonical(jwk))
        ):
            _fail()
        x = _base64url(jwk["x"], decoded_bytes=32)
        y = _base64url(jwk["y"], decoded_bytes=32)
        signature = _base64url(envelope.get("signature"), decoded_bytes=64)
        r = int.from_bytes(signature[:32], "big")
        s = int.from_bytes(signature[32:], "big")
        if not 1 <= r < _P256_ORDER or not 1 <= s <= _P256_ORDER // 2:
            _fail()
        public_key = ec.EllipticCurvePublicNumbers(
            int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
        ).public_key()
        digest = hashlib.sha256(receipt_bytes).digest()
        public_key.verify(
            utils.encode_dss_signature(r, s),
            digest,
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )
    except BrokerLaunchReceiptAuthenticationFailure:
        raise
    except (
        InvalidSignature,
        KeyError,
        TypeError,
        ValueError,
        runtime_binding.RuntimeBindingFailure,
    ):
        _fail()

    result = object.__new__(VerifiedBrokerLaunchReceiptSignature)
    values = {
        "receipt_bytes": bytes(receipt_bytes),
        "receipt_sha256": receipt_sha256,
        "signature_envelope_bytes": bytes(signature_envelope_bytes),
        "signature_envelope_sha256": _digest(signature_envelope_bytes),
        "policy_id": document["policy_id"],
        "policy_binding_sha256": policy_binding_sha256,
        "algorithm": signing["algorithm"],
        "signing_key_resource_id": signing["key_resource_id"],
        "signing_key_version": signing["key_version"],
        "public_key_sha256": signing["public_key_sha256"],
    }
    for name, value in values.items():
        object.__setattr__(result, name, value)
    object.__setattr__(result, "_seal", _VERIFIED_SIGNATURE_SEAL)
    return result
