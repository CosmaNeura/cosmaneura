from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ObjectIdentifier

from scripts import authenticate_staging_github_attestation as github_auth
from scripts import authenticate_staging_runtime_record_signature as authentication
from scripts import validate_staging_restore_runtime_binding as binding

from tests.infrastructure import test_staging_restore_runtime_binding as helpers

NOW = datetime(2026, 9, 4, 12, 5, tzinfo=UTC)
SOURCE_SHA = "c" * 40
RUN_INVOCATION_URI = (
    "https://github.com/CosmaNeura/cosmaneura/actions/runs/901/attempts/1"
)
VERIFIER_DIGEST = f"sha256:{'d' * 64}"
ISSUER = x509.Name(
    [
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "GitHub, Inc."),
        x509.NameAttribute(NameOID.COMMON_NAME, "GitHub Actions OIDC"),
    ]
)


class FakeVerifier:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.output: Any = None
        self.error: Exception | None = None
        self.returncode = 0
        self.stderr = b""
        self.version = "2.97.0"
        self.digest = VERIFIER_DIGEST

    def verify(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return github_auth.AttestationVerifierResponse(
            returncode=self.returncode,
            stdout=(
                authentication._canonical(self.output)
                if self.output is not None
                else b"[]"
            ),
            stderr=self.stderr,
            verifier_version=self.version,
            verifier_sha256=self.digest,
        )


def _der_utf8(value: str) -> bytes:
    raw = value.encode()
    if len(raw) < 128:
        return bytes((12, len(raw))) + raw
    encoded_length = len(raw).to_bytes((len(raw).bit_length() + 7) // 8, "big")
    return bytes((12, 0x80 | len(encoded_length))) + encoded_length + raw


def _root() -> tuple[dict[str, Any], bytes]:
    value = {
        "mediaType": "application/vnd.dev.sigstore.trustedroot+json;version=0.1",
        "certificateAuthorities": [{"uri": authentication.FULCIO_URI}],
        "timestampAuthorities": [{"uri": authentication.TIMESTAMP_AUTHORITY_URI}],
        "tlogs": [],
        "ctlogs": [],
    }
    return value, authentication._canonical(value) + b"\n"


def _claims(policy: dict[str, Any], **changes: str) -> dict[str, str]:
    identity = policy["external_runtime_record"]["signature"]["certificate_identity"]
    repository = policy["external_runtime_record"]["binding"]["repository"]
    values = {
        "issuer": "https://token.actions.githubusercontent.com",
        "buildSignerURI": identity,
        "buildSignerDigest": SOURCE_SHA,
        "runnerEnvironment": "github-hosted",
        "sourceRepositoryURI": f"https://github.com/{repository}",
        "sourceRepositoryDigest": SOURCE_SHA,
        "sourceRepositoryRef": "refs/heads/master",
        "sourceRepositoryIdentifier": "881573489",
        "sourceRepositoryOwnerURI": "https://github.com/CosmaNeura",
        "sourceRepositoryOwnerIdentifier": "186940615",
        "buildConfigURI": identity,
        "buildConfigDigest": SOURCE_SHA,
        "buildTrigger": "workflow_dispatch",
        "runInvocationURI": RUN_INVOCATION_URI,
        "sourceRepositoryVisibilityAtSigning": "private",
    }
    values.update(changes)
    return values


def _certificate(
    policy: dict[str, Any],
    claims: dict[str, str],
    *,
    san: str | None = None,
    omitted_oid: str | None = None,
    not_after: datetime = NOW + timedelta(hours=1),
) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    identity = policy["external_runtime_record"]["signature"]["certificate_identity"]
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([]))
        .issuer_name(ISSUER)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(hours=1))
        .not_valid_after(not_after)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(san or identity)]
            ),
            critical=True,
        )
    )
    for field, oid in authentication.CERTIFICATE_OIDS.items():
        if field != omitted_oid:
            builder = builder.add_extension(
                x509.UnrecognizedExtension(
                    ObjectIdentifier(oid), _der_utf8(claims[field])
                ),
                critical=False,
            )
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


def _summary(
    policy: dict[str, Any], claims: dict[str, str], certificate: bytes
) -> dict[str, str]:
    identity = policy["external_runtime_record"]["signature"]["certificate_identity"]
    repository = policy["external_runtime_record"]["binding"]["repository"]
    result = {
        "certificateIssuer": x509.load_der_x509_certificate(
            certificate
        ).issuer.rfc4514_string(),
        "subjectAlternativeName": identity,
        **claims,
        "githubWorkflowTrigger": "workflow_dispatch",
        "githubWorkflowSHA": claims["sourceRepositoryDigest"],
        "githubWorkflowName": "Build and Publish",
        "githubWorkflowRepository": repository,
        "githubWorkflowRef": "refs/heads/master",
    }
    return result


def _statement(record_bytes: bytes) -> dict[str, Any]:
    return {
        "_type": authentication.STATEMENT_TYPE,
        "subject": [
            {
                "name": authentication.RUNTIME_RECORD_FILE,
                "digest": {"sha256": hashlib.sha256(record_bytes).hexdigest()},
            }
        ],
        "predicateType": authentication.PREDICATE_TYPE,
        "predicate": {
            "buildDefinition": {
                "buildType": authentication.BUILD_TYPE,
                "externalParameters": {},
                "internalParameters": {},
                "resolvedDependencies": [],
            },
            "runDetails": {"builder": {}, "metadata": {}, "byproducts": []},
        },
    }


def _references(
    policy: dict[str, Any], record_bytes: bytes, bundle_bytes: bytes
) -> tuple[
    binding.ExternalRuntimeBindingReference,
    binding.ExternalRuntimeBindingReference,
]:
    record_digest = authentication._digest(record_bytes)
    bundle_digest = authentication._digest(bundle_bytes)
    host = policy["runtime_record_ingress"]["account_host"]
    container = policy["runtime_record_ingress"]["container_name"]
    pointers = {
        "record_uri": (f"https://{host}/{container}/{record_digest[7:]}.json"),
        "record_sha256": record_digest,
        "signature_bundle_uri": (
            f"https://{host}/{container}/{bundle_digest[7:]}.sigstore.json"
        ),
        "signature_bundle_sha256": bundle_digest,
    }
    observations = (
        helpers._job_observation(policy, action, pointers)
        for action in ("provision", "cleanup")
    )
    result = tuple(
        binding.ExternalRuntimeBindingReference._from_authenticated_job_observation(
            observation
        )
        for observation in observations
    )
    return result[0], result[1]


def _case(
    *,
    claim_changes: dict[str, str] | None = None,
    san: str | None = None,
    omitted_oid: str | None = None,
    not_after: datetime = NOW + timedelta(hours=1),
) -> dict[str, Any]:
    policy = helpers._configured_policy()
    policy["offline_github_verifier"]["executable_sha256"] = VERIFIER_DIGEST
    root, root_bytes = _root()
    policy["evidence_ingress"]["private_versioned_secret_source"]["secrets"][
        "github_trusted_root"
    ]["sha256"] = authentication._digest(root_bytes)
    record_bytes = helpers._external_binding(policy)[0]
    statement = _statement(record_bytes)
    claims = _claims(policy, **(claim_changes or {}))
    certificate = _certificate(
        policy,
        claims,
        san=san,
        omitted_oid=omitted_oid,
        not_after=not_after,
    )
    summary = _summary(policy, claims, certificate)
    bundle = {
        "mediaType": authentication.BUNDLE_MEDIA_TYPE,
        "verificationMaterial": {
            "certificate": {"rawBytes": base64.b64encode(certificate).decode()},
            "timestampVerificationData": {
                "rfc3161Timestamps": [{"signedTimestamp": "AA=="}]
            },
        },
        "dsseEnvelope": {
            "payloadType": "application/vnd.in-toto+json",
            "payload": base64.b64encode(authentication._canonical(statement)).decode(),
            "signatures": [{"keyid": "", "sig": "AA=="}],
        },
    }
    bundle_bytes = authentication._canonical(bundle)
    references = _references(policy, record_bytes, bundle_bytes)
    output = [
        {
            "attestation": {
                "bundle": deepcopy(bundle),
                "bundle_url": "",
                "initiator": "",
            },
            "verificationResult": {
                "mediaType": authentication.VERIFICATION_MEDIA_TYPE,
                "signature": {"certificate": summary},
                "verifiedTimestamps": [
                    {
                        "type": "TimestampAuthority",
                        "uri": authentication.TIMESTAMP_AUTHORITY_URI,
                        "timestamp": "2026-09-04T12:04:59.123456789Z",
                    }
                ],
                "verifiedIdentity": {
                    "subjectAlternativeName": {
                        "subjectAlternativeName": policy["external_runtime_record"][
                            "signature"
                        ]["certificate_identity"]
                    },
                    "issuer": {"issuer": "", "regexp": ".*"},
                    "runnerEnvironment": "github-hosted",
                },
                "statement": deepcopy(statement),
            },
        }
    ]
    verifier = FakeVerifier()
    verifier.output = output
    return {
        "policy": policy,
        "root": root,
        "root_bytes": root_bytes,
        "record_bytes": record_bytes,
        "bundle": bundle,
        "bundle_bytes": bundle_bytes,
        "references": references,
        "statement": statement,
        "output": output,
        "verifier": verifier,
    }


def _authenticate(case: dict[str, Any]) -> Any:
    provision, cleanup = case["references"]
    return authentication.authenticate_runtime_record_signature(
        case["policy"],
        provision_reference=provision,
        cleanup_reference=cleanup,
        record_bytes=case["record_bytes"],
        signature_bundle_bytes=case["bundle_bytes"],
        trusted_root_bytes=case["root_bytes"],
        verifier=case["verifier"],
        clock=lambda: NOW,
    )


def _refresh_bundle(case: dict[str, Any]) -> None:
    case["bundle_bytes"] = authentication._canonical(case["bundle"])
    case["output"][0]["attestation"]["bundle"] = deepcopy(case["bundle"])
    case["references"] = _references(
        case["policy"], case["record_bytes"], case["bundle_bytes"]
    )


def _refresh_record(case: dict[str, Any], record: dict[str, Any]) -> None:
    projected = {name: value for name, value in record.items() if name != "record_id"}
    record["record_id"] = binding._derive_runtime_record_id(projected)
    case["record_bytes"] = authentication._canonical(record)
    case["statement"] = _statement(case["record_bytes"])
    case["bundle"]["dsseEnvelope"]["payload"] = base64.b64encode(
        authentication._canonical(case["statement"])
    ).decode()
    case["output"][0]["verificationResult"]["statement"] = deepcopy(case["statement"])
    _refresh_bundle(case)


def test_happy_path_uses_exact_offline_profile_and_mints_bound_proof() -> None:
    case = _case()
    proof = _authenticate(case)
    policy = case["policy"]
    provision, cleanup = case["references"]
    assert type(proof) is binding.VerifiedExternalRuntimeRecordSignature
    assert proof._is_verified()
    assert proof.record_sha256 == authentication._digest(case["record_bytes"])
    assert proof.bundle_sha256 == authentication._digest(case["bundle_bytes"])
    assert proof.trusted_root_sha256 == authentication._digest(case["root_bytes"])
    assert set(proof.__slots__) == {
        "record_sha256",
        "bundle_sha256",
        "trusted_root_sha256",
        "certificate_oidc_issuer",
        "certificate_identity",
        "_seal",
    }
    assert all(
        raw.decode() not in repr(proof)
        for raw in (case["record_bytes"], case["bundle_bytes"], case["root_bytes"])
    )
    validated = binding.validate_external_runtime_binding(
        policy,
        case["record_bytes"],
        provision_reference=provision,
        cleanup_reference=cleanup,
        signature_verification=proof,
    )
    assert validated.record_bytes == case["record_bytes"]
    call = case["verifier"].calls[0]
    record = json.loads(case["record_bytes"])
    assert call["arguments"] == authentication._arguments(policy, record)
    assert call["arguments"][-6:] == (
        "--signer-digest",
        SOURCE_SHA,
        "--source-digest",
        SOURCE_SHA,
        "--source-ref",
        "refs/heads/master",
    )
    assert type(call["files"]) is MappingProxyType
    assert call["files"] == {
        "runtime-record.json": case["record_bytes"],
        "runtime-record.sigstore.json": case["bundle_bytes"],
        "github-trusted-root.jsonl": case["root_bytes"],
    }
    assert (call["timeout_seconds"], call["maximum_output_bytes"]) == (
        30,
        4_194_304,
    )


def test_checked_in_policy_refuses_before_references_or_verifier() -> None:
    case = _case()
    checked_in = json.loads(binding.POLICY_PATH.read_text())
    with pytest.raises(
        authentication.RuntimeRecordSignatureFailure,
        match="^runtime_signature.policy_invalid$",
    ):
        authentication.authenticate_runtime_record_signature(
            checked_in,
            provision_reference=object(),  # type: ignore[arg-type]
            cleanup_reference=object(),  # type: ignore[arg-type]
            record_bytes=b"secret",
            signature_bundle_bytes=b"secret",
            trusted_root_bytes=b"secret",
            verifier=case["verifier"],
        )
    assert case["verifier"].calls == []


def test_stale_runtime_record_id_refuses_before_offline_verifier() -> None:
    case = _case()
    record = json.loads(case["record_bytes"])
    record["record_id"] = f"runtime-{'f' * 64}"
    case["record_bytes"] = authentication._canonical(record)
    case["statement"] = _statement(case["record_bytes"])
    case["bundle"]["dsseEnvelope"]["payload"] = base64.b64encode(
        authentication._canonical(case["statement"])
    ).decode()
    case["output"][0]["verificationResult"]["statement"] = deepcopy(case["statement"])
    _refresh_bundle(case)

    with pytest.raises(
        authentication.RuntimeRecordSignatureFailure,
        match="^runtime_signature.input_invalid$",
    ):
        _authenticate(case)

    assert case["verifier"].calls == []


def test_non_github_runtime_signer_identity_refuses_before_verifier() -> None:
    case = _case()
    case["policy"]["external_runtime_record"]["signature"]["certificate_identity"] = (
        "https://attacker.invalid/workflow.yml@refs/heads/master"
    )
    with pytest.raises(
        authentication.RuntimeRecordSignatureFailure,
        match="^runtime_signature.policy_invalid$",
    ):
        _authenticate(case)
    assert case["verifier"].calls == []


@pytest.mark.parametrize(
    "failure", ["unsealed", "divergent", "record", "bundle", "root"]
)
def test_reference_and_digest_substitutions_refuse_before_verifier(
    failure: str,
) -> None:
    case = _case()
    provision, cleanup = case["references"]
    if failure == "unsealed":
        object.__setattr__(provision, "_seal", object())
        expected = "reference_invalid"
    elif failure == "divergent":
        object.__setattr__(cleanup, "record_sha256", f"sha256:{'e' * 64}")
        expected = "reference_invalid"
    else:
        name = f"{failure}_bytes"
        case[name] += b"x"
        expected = "input_invalid"
    with pytest.raises(
        authentication.RuntimeRecordSignatureFailure,
        match=rf"^runtime_signature\.{expected}$",
    ):
        _authenticate(case)
    assert case["verifier"].calls == []


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("record_uri", "http://restoreevidence.blob.core.windows.net/record.json"),
        ("record_uri", "https://attacker.invalid/record.json"),
        ("record_uri", "https://restoreevidence.blob.core.windows.net:443/record.json"),
        (
            "signature_bundle_uri",
            "https://restoreevidence.blob.core.windows.net/runtime.sigstore.json?x=1",
        ),
        (
            "signature_bundle_uri",
            "https://restoreevidence.blob.core.windows.net/%2e%2e/runtime.sigstore.json",
        ),
    ],
)
def test_attacker_runtime_uris_refuse_before_verifier(
    field: str, replacement: str
) -> None:
    case = _case()
    provision, cleanup = case["references"]
    object.__setattr__(provision, field, replacement)
    object.__setattr__(cleanup, field, replacement)
    with pytest.raises(
        authentication.RuntimeRecordSignatureFailure,
        match="^runtime_signature.reference_invalid$",
    ):
        _authenticate(case)
    assert case["verifier"].calls == []


@pytest.mark.parametrize(
    "failure", ["root-log", "root-framing", "bundle-log", "bundle-media"]
)
def test_root_and_bundle_are_closed_world_offline_inputs(failure: str) -> None:
    case = _case()
    if failure == "root-log":
        case["root"]["tlogs"] = [{"uri": "rekor.sigstore.dev"}]
        case["root_bytes"] = authentication._canonical(case["root"]) + b"\n"
        case["policy"]["evidence_ingress"]["private_versioned_secret_source"][
            "secrets"
        ]["github_trusted_root"]["sha256"] = authentication._digest(case["root_bytes"])
        record = json.loads(case["record_bytes"])
        record["policy_binding_sha256"] = binding._policy_binding_digest(case["policy"])
        _refresh_record(case, record)
        expected = "trusted_root_invalid"
    elif failure == "root-framing":
        case["root_bytes"] = case["root_bytes"].removesuffix(b"\n")
        case["policy"]["evidence_ingress"]["private_versioned_secret_source"][
            "secrets"
        ]["github_trusted_root"]["sha256"] = authentication._digest(case["root_bytes"])
        record = json.loads(case["record_bytes"])
        record["policy_binding_sha256"] = binding._policy_binding_digest(case["policy"])
        _refresh_record(case, record)
        expected = "trusted_root_invalid"
    else:
        if failure == "bundle-log":
            case["bundle"]["verificationMaterial"]["tlogEntries"] = [{}]
        else:
            case["bundle"]["mediaType"] = "application/json"
        _refresh_bundle(case)
        expected = "bundle_invalid"
    with pytest.raises(
        authentication.RuntimeRecordSignatureFailure,
        match=rf"^runtime_signature\.{expected}$",
    ):
        _authenticate(case)
    assert case["verifier"].calls == []


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("identity", "verifier_invalid"),
        ("subject", "statement_invalid"),
        ("payload", "statement_invalid"),
        ("predicate", "statement_invalid"),
        ("certificate-summary", "certificate_invalid"),
        ("timestamp-authority", "timestamp_invalid"),
        ("timestamp-before-record", "timestamp_invalid"),
    ],
)
def test_verifier_output_statement_certificate_and_time_are_exact(
    failure: str, expected: str
) -> None:
    case = _case()
    result = case["output"][0]["verificationResult"]
    if failure == "identity":
        result["verifiedIdentity"]["runnerEnvironment"] = "self-hosted"
    elif failure == "subject":
        result["statement"]["subject"][0]["name"] = "other.json"
    elif failure == "payload":
        result["statement"]["predicate"]["runDetails"]["metadata"]["extra"] = True
    elif failure == "predicate":
        result["statement"]["predicate"]["buildDefinition"]["buildType"] = "other"
        case["bundle"]["dsseEnvelope"]["payload"] = base64.b64encode(
            authentication._canonical(result["statement"])
        ).decode()
        _refresh_bundle(case)
    elif failure == "certificate-summary":
        result["signature"]["certificate"]["runnerEnvironment"] = "self-hosted"
    else:
        timestamp = result["verifiedTimestamps"][0]
        if failure == "timestamp-authority":
            timestamp["uri"] = "timestamp.sigstore.dev"
        else:
            timestamp["timestamp"] = "2026-09-04T12:00:59Z"
    with pytest.raises(
        authentication.RuntimeRecordSignatureFailure,
        match=rf"^runtime_signature\.{expected}$",
    ):
        _authenticate(case)


@pytest.mark.parametrize(
    ("case_kwargs", "summary_change", "expected"),
    [
        (
            {"san": "https://github.com/attacker/workflow.yml@refs/heads/main"},
            None,
            "certificate_invalid",
        ),
        ({"omitted_oid": "issuer"}, None, "certificate_invalid"),
        (
            {"claim_changes": {"runnerEnvironment": "self-hosted"}},
            None,
            "certificate_invalid",
        ),
        (
            {"claim_changes": {"sourceRepositoryRef": "refs/heads/attacker"}},
            None,
            "certificate_invalid",
        ),
        (
            {"claim_changes": {"sourceRepositoryDigest": "e" * 40}},
            None,
            "certificate_invalid",
        ),
        (
            {"claim_changes": {"sourceRepositoryIdentifier": "1"}},
            None,
            "certificate_invalid",
        ),
        ({"not_after": NOW - timedelta(minutes=1)}, None, "timestamp_invalid"),
        ({}, ("githubWorkflowRef", "refs/heads/attacker"), "certificate_invalid"),
    ],
)
def test_certificate_der_summary_and_github_claims_are_cross_checked(
    case_kwargs: dict[str, Any],
    summary_change: tuple[str, str] | None,
    expected: str,
) -> None:
    case = _case(**case_kwargs)
    if summary_change:
        case["output"][0]["verificationResult"]["signature"]["certificate"][
            summary_change[0]
        ] = summary_change[1]
    with pytest.raises(
        authentication.RuntimeRecordSignatureFailure,
        match=rf"^runtime_signature\.{expected}$",
    ):
        _authenticate(case)


@pytest.mark.parametrize(
    "claim_changes",
    [
        {
            "buildSignerDigest": "e" * 40,
            "sourceRepositoryDigest": "e" * 40,
            "buildConfigDigest": "e" * 40,
        },
        {
            "runInvocationURI": (
                "https://github.com/CosmaNeura/cosmaneura/actions/runs/902/attempts/1"
            )
        },
    ],
)
def test_coordinated_certificate_authority_cannot_replace_signed_record_authority(
    claim_changes: dict[str, str],
) -> None:
    case = _case(claim_changes=claim_changes)

    with pytest.raises(
        authentication.RuntimeRecordSignatureFailure,
        match="^runtime_signature.certificate_invalid$",
    ):
        _authenticate(case)


def test_verifier_callback_cannot_mutate_policy_or_reference_authority() -> None:
    case = _case()
    verifier = case["verifier"]
    original_verify = verifier.verify

    def mutate_then_verify(**kwargs: Any) -> Any:
        case["policy"]["external_runtime_record"]["signature"][
            "certificate_identity"
        ] = "https://attacker.invalid/workflow.yml@refs/heads/main"
        for reference in case["references"]:
            object.__setattr__(reference, "record_sha256", f"sha256:{'e' * 64}")
        case["record_bytes"] = b'{"attacker":true}'
        return original_verify(**kwargs)

    verifier.verify = mutate_then_verify  # type: ignore[method-assign]
    proof = _authenticate(case)

    assert proof.record_sha256 != authentication._digest(case["record_bytes"])
    assert proof.certificate_identity.endswith(
        "build_and_publish_local.yml@refs/heads/master"
    )


@pytest.mark.parametrize(
    ("attribute", "value", "expected"),
    [
        ("error", RuntimeError("secret verifier detail"), "verifier_unavailable"),
        ("returncode", 1, "verifier_invalid"),
        ("stderr", b"secret diagnostic", "verifier_invalid"),
        ("version", "latest", "verifier_invalid"),
        ("digest", f"sha256:{'e' * 64}", "verifier_invalid"),
    ],
)
def test_verifier_failures_are_bounded(
    attribute: str, value: Any, expected: str
) -> None:
    case = _case()
    setattr(case["verifier"], attribute, value)
    with pytest.raises(
        authentication.RuntimeRecordSignatureFailure,
        match=rf"^runtime_signature\.{expected}$",
    ) as failure:
        _authenticate(case)
    assert "secret" not in str(failure.value)
