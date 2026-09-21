from __future__ import annotations

import base64
import hashlib
from copy import deepcopy
from typing import Any

import pytest

from scripts import authenticate_staging_broker_launch_receipt as authentication
from scripts import validate_staging_restore_runtime_binding as binding

from tests.infrastructure import test_staging_restore_runtime_binding as helpers

P256_ORDER = int("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16)
KNOWN_RECEIPT = b'{"schema_version":1}'
KNOWN_RECEIPT_DIGEST = (
    "sha256:a9d5f6d002d956b8af5787a05e0ca000d45c03977ffa54ee8fbed719fed5fd23"
)
KNOWN_SIGNATURE = (
    "dUD-NhfnVScyJD37xOikmQKke6WWhtjLGkp2cRyypZEvjLHJMEOeo8yUNx5Lte9F"
    "Bvp1Sw_jkdKSQNIgZZYRdA"
)
KNOWN_HIGH_S_SIGNATURE = (
    "dUD-NhfnVScyJD37xOikmQKke6WWhtjLGkp2cRyypZHQc041z7xhXTNryOG0ShC6t"
    "eyFYpc0DLJhePiils0T3Q"
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(receipt_bytes: bytes, *, nonce: int = 7) -> str:
    """Produce deterministic low-S test signatures for the pinned d=1 key."""

    from cryptography.hazmat.primitives.asymmetric import ec

    r = ec.derive_private_key(nonce, ec.SECP256R1()).public_key().public_numbers().x
    z = int.from_bytes(hashlib.sha256(receipt_bytes).digest(), "big")
    s = (pow(nonce, -1, P256_ORDER) * (z + r)) % P256_ORDER
    s = min(s, P256_ORDER - s)
    return _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _envelope(
    policy: dict[str, Any],
    receipt_bytes: bytes,
    *,
    signature: str | None = None,
    changes: dict[str, Any] | None = None,
) -> bytes:
    signing = policy["launch_binding"]["receipt_signing"]
    value = {
        "algorithm": signing["algorithm"],
        "policy_binding_sha256": binding._policy_binding_digest(policy),
        "policy_id": policy["policy_id"],
        "public_key_sha256": signing["public_key_sha256"],
        "receipt_sha256": binding._digest(receipt_bytes),
        "schema_version": 1,
        "signature": signature or _sign(receipt_bytes),
        "signing_key_resource_id": signing["key_resource_id"],
        "signing_key_version": signing["key_version"],
    }
    value.update(changes or {})
    return binding._canonical(value)


def _authenticate(
    policy: dict[str, Any], receipt_bytes: bytes
) -> tuple[bytes, authentication.VerifiedBrokerLaunchReceiptSignature]:
    envelope = _envelope(policy, receipt_bytes)
    proof = authentication.authenticate_broker_launch_receipt_signature(
        policy,
        receipt_bytes=receipt_bytes,
        signature_envelope_bytes=envelope,
    )
    return envelope, proof


def _assert_refused(
    policy: dict[str, Any], receipt_bytes: bytes, envelope_bytes: bytes
) -> None:
    with pytest.raises(
        authentication.BrokerLaunchReceiptAuthenticationFailure,
        match="^broker_receipt_signature.invalid$",
    ) as error:
        authentication.authenticate_broker_launch_receipt_signature(
            policy,
            receipt_bytes=receipt_bytes,
            signature_envelope_bytes=envelope_bytes,
        )
    assert str(error.value) == "broker_receipt_signature.invalid"
    assert len(str(error.value)) < 64


def test_fixed_azure_key_vault_es256_known_answer_and_sealed_proof() -> None:
    policy = helpers._configured_policy()
    signing = policy["launch_binding"]["receipt_signing"]
    assert hashlib.sha256(KNOWN_RECEIPT).hexdigest() == KNOWN_RECEIPT_DIGEST[7:]
    assert signing["public_key_jwk"] == {
        "crv": "P-256",
        "kty": "EC",
        "x": "axfR8uEsQkf4vOblY6RA8ncDfYEt6zOg9KE5RdiYwpY",
        "y": "T-NC4v4af5uO5-tKfA-eFivOM1drMV7Oy7ZAaDe_UfU",
    }
    assert signing["public_key_sha256"] == (
        "sha256:c71d01700fb0328870f1ab580c939eea9786328764946db04470655c732f9743"
    )
    assert _sign(KNOWN_RECEIPT, nonce=7) != KNOWN_SIGNATURE

    envelope = _envelope(policy, KNOWN_RECEIPT, signature=KNOWN_SIGNATURE)
    proof = authentication.authenticate_broker_launch_receipt_signature(
        policy,
        receipt_bytes=KNOWN_RECEIPT,
        signature_envelope_bytes=envelope,
    )
    assert proof.receipt_bytes == KNOWN_RECEIPT
    assert proof.receipt_sha256 == KNOWN_RECEIPT_DIGEST
    assert proof.signature_envelope_bytes == envelope
    assert proof.signature_envelope_sha256 == binding._digest(envelope)
    assert proof._matches(
        policy=policy,
        receipt_bytes=KNOWN_RECEIPT,
        signature_envelope_bytes=envelope,
    )
    assert not hasattr(proof, "__dict__")
    with pytest.raises(
        authentication.BrokerLaunchReceiptAuthenticationFailure,
        match="broker_receipt_signature.invalid",
    ):
        authentication.VerifiedBrokerLaunchReceiptSignature()
    unsealed = object.__new__(authentication.VerifiedBrokerLaunchReceiptSignature)
    assert not unsealed._matches(
        policy=policy,
        receipt_bytes=KNOWN_RECEIPT,
        signature_envelope_bytes=envelope,
    )


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("policy_id", "other-policy"),
        ("policy_binding_sha256", f"sha256:{'0' * 64}"),
        ("receipt_sha256", f"sha256:{'0' * 64}"),
        ("algorithm", "ES384"),
        ("signing_key_resource_id", "/subscriptions/wrong"),
        ("signing_key_version", "wrong-version"),
        ("public_key_sha256", f"sha256:{'0' * 64}"),
        ("schema_version", 2),
    ],
)
def test_rejects_wrong_envelope_bindings(field: str, wrong: Any) -> None:
    policy = helpers._configured_policy()
    _assert_refused(
        policy,
        KNOWN_RECEIPT,
        _envelope(
            policy, KNOWN_RECEIPT, signature=KNOWN_SIGNATURE, changes={field: wrong}
        ),
    )


def test_rejects_tampered_receipt_signature_and_proof_reuse() -> None:
    policy = helpers._configured_policy()
    envelope = _envelope(policy, KNOWN_RECEIPT, signature=KNOWN_SIGNATURE)
    proof = authentication.authenticate_broker_launch_receipt_signature(
        policy,
        receipt_bytes=KNOWN_RECEIPT,
        signature_envelope_bytes=envelope,
    )
    changed_receipt = b'{"schema_version":2}'
    _assert_refused(policy, changed_receipt, envelope)
    changed = bytearray(envelope)
    changed[-2] ^= 1
    _assert_refused(policy, KNOWN_RECEIPT, bytes(changed))
    changed_envelope = _envelope(policy, changed_receipt)
    assert not proof._matches(
        policy=policy,
        receipt_bytes=changed_receipt,
        signature_envelope_bytes=changed_envelope,
    )
    assert not proof._matches(
        policy=policy,
        receipt_bytes=KNOWN_RECEIPT,
        signature_envelope_bytes=changed_envelope,
    )


@pytest.mark.parametrize(
    "receipt",
    [
        b'{"schema_version":1}\n',
        b'{ "schema_version":1}',
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":NaN}',
        b"[]",
        b"\xff",
    ],
)
def test_rejects_noncanonical_or_malformed_receipt_json(receipt: bytes) -> None:
    policy = helpers._configured_policy()
    _assert_refused(policy, receipt, _envelope(policy, receipt))


def test_rejects_oversized_receipt_and_envelope_before_parsing() -> None:
    policy = helpers._configured_policy()
    maximum = policy["launch_binding"]["launch_receipt"]["maximum_bytes"]
    receipt = b"{" + b"x" * maximum + b"}"
    _assert_refused(policy, receipt, b"{}")
    envelope_maximum = policy["launch_binding"]["receipt_signing"][
        "signature_envelope"
    ]["maximum_bytes"]
    _assert_refused(policy, KNOWN_RECEIPT, b"{" + b"x" * envelope_maximum + b"}")


@pytest.mark.parametrize(
    "envelope_mutation",
    [
        lambda raw: raw + b"\n",
        lambda raw: raw.replace(b"{", b"{ ", 1),
        lambda raw: raw.replace(
            b'"schema_version":1', b'"schema_version":1,"schema_version":1'
        ),
        lambda raw: raw.replace(b"{", b'{"unknown":"refuse",', 1),
        lambda raw: raw.replace(b'"policy_id":', b'"removed_policy_id":', 1),
    ],
)
def test_rejects_noncanonical_duplicate_unknown_or_missing_envelope(
    envelope_mutation: Any,
) -> None:
    policy = helpers._configured_policy()
    envelope = _envelope(policy, KNOWN_RECEIPT, signature=KNOWN_SIGNATURE)
    _assert_refused(policy, KNOWN_RECEIPT, envelope_mutation(envelope))


@pytest.mark.parametrize(
    "signature",
    [
        "A" + KNOWN_SIGNATURE[1:],
        KNOWN_SIGNATURE + "=",
        KNOWN_SIGNATURE[:-1] + "+",
        "AA",
        _b64url(b"\x00" * 64),
        _b64url(P256_ORDER.to_bytes(32, "big") + (1).to_bytes(32, "big")),
        KNOWN_HIGH_S_SIGNATURE,
    ],
)
def test_rejects_malformed_non_p1363_or_noncanonical_signature(
    signature: str,
) -> None:
    policy = helpers._configured_policy()
    _assert_refused(
        policy,
        KNOWN_RECEIPT,
        _envelope(policy, KNOWN_RECEIPT, signature=signature),
    )


def test_rejects_der_signature_wire_encoding() -> None:
    from cryptography.hazmat.primitives.asymmetric import utils

    policy = helpers._configured_policy()
    raw = base64.urlsafe_b64decode(KNOWN_SIGNATURE + "==")
    der = utils.encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    _assert_refused(
        policy,
        KNOWN_RECEIPT,
        _envelope(policy, KNOWN_RECEIPT, signature=_b64url(der)),
    )


def test_rejects_wrong_curve_coordinates_and_policy_metadata() -> None:
    baseline = helpers._configured_policy()
    for mutate in (
        lambda policy: policy["launch_binding"]["receipt_signing"].update(
            {"algorithm": "ES384"}
        ),
        lambda policy: policy["launch_binding"]["receipt_signing"].update(
            {"key_version": "other-version"}
        ),
        lambda policy: policy["launch_binding"]["receipt_signing"][
            "public_key_jwk"
        ].update({"crv": "P-384"}),
        lambda policy: policy["launch_binding"]["receipt_signing"][
            "public_key_jwk"
        ].update({"x": "AA"}),
    ):
        policy = deepcopy(baseline)
        mutate(policy)
        _assert_refused(
            policy,
            KNOWN_RECEIPT,
            _envelope(baseline, KNOWN_RECEIPT, signature=KNOWN_SIGNATURE),
        )

    off_curve = deepcopy(baseline)
    signing = off_curve["launch_binding"]["receipt_signing"]
    signing["public_key_jwk"]["x"] = _b64url(b"\x00" * 32)
    signing["public_key_jwk"]["y"] = _b64url(b"\x00" * 32)
    signing["public_key_sha256"] = binding._digest(
        binding._canonical(signing["public_key_jwk"])
    )
    _assert_refused(
        off_curve,
        KNOWN_RECEIPT,
        _envelope(off_curve, KNOWN_RECEIPT, signature=KNOWN_SIGNATURE),
    )

    from cryptography.hazmat.primitives.asymmetric import ec

    wrong_key = deepcopy(baseline)
    wrong_signing = wrong_key["launch_binding"]["receipt_signing"]
    numbers = ec.derive_private_key(2, ec.SECP256R1()).public_key().public_numbers()
    wrong_signing["public_key_jwk"]["x"] = _b64url(numbers.x.to_bytes(32, "big"))
    wrong_signing["public_key_jwk"]["y"] = _b64url(numbers.y.to_bytes(32, "big"))
    wrong_signing["public_key_sha256"] = binding._digest(
        binding._canonical(wrong_signing["public_key_jwk"])
    )
    _assert_refused(
        wrong_key,
        KNOWN_RECEIPT,
        _envelope(wrong_key, KNOWN_RECEIPT, signature=KNOWN_SIGNATURE),
    )
