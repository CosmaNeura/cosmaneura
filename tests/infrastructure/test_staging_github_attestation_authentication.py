from __future__ import annotations

import base64
import hashlib
import importlib
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ObjectIdentifier

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
attestation = importlib.import_module("scripts.authenticate_staging_github_attestation")
safe_submit = importlib.import_module("scripts.staging_restore_safe_submit")
sys.path.pop(0)
POLICY = (
    ROOT
    / "contracts/staging-rehearsal/v1/github-attestation-authentication-policy.json"
)
OBSERVED = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 3, 12, 5, tzinfo=UTC)
WORKFLOW_SHA = "6" * 40
VERIFIER_DIGEST = f"sha256:{'d' * 64}"
OID_PREFIX = "1.3.6.1.4.1.57264.1."
SAN = (
    "https://github.com/CosmaNeura/cosmaneura/.github/workflows/"
    "staging_restore_rehearsal.yml@refs/heads/master"
)
TOKEN_SUBJECT = "repo:CosmaNeura/cosmaneura:environment:staging-restore-provision"
CERTIFICATE_ISSUER = x509.Name(
    [
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "GitHub, Inc."),
        x509.NameAttribute(NameOID.COMMON_NAME, "GitHub Actions OIDC"),
    ]
)
POLICY_SUMMARY_FIELDS = dict(
    zip(
        "subjectAlternativeName issuer buildSignerURI runnerEnvironment sourceRepositoryURI sourceRepositoryRef sourceRepositoryIdentifier sourceRepositoryOwnerURI sourceRepositoryOwnerIdentifier buildConfigURI buildTrigger sourceRepositoryVisibilityAtSigning githubWorkflowTrigger githubWorkflowName".split(),
        "subject_alternative_name oidc_issuer build_signer_uri runner_environment source_repository_uri source_repository_ref source_repository_identifier source_repository_owner_uri source_repository_owner_identifier build_config_uri workflow_trigger source_repository_visibility workflow_trigger workflow_name".split(),
        strict=True,
    )
)
# fmt: off
OID_SUMMARY_FIELDS = dict(zip(range(8, 23), "issuer buildSignerURI buildSignerDigest runnerEnvironment sourceRepositoryURI sourceRepositoryDigest sourceRepositoryRef sourceRepositoryIdentifier sourceRepositoryOwnerURI sourceRepositoryOwnerIdentifier buildConfigURI buildConfigDigest buildTrigger runInvocationURI sourceRepositoryVisibilityAtSigning".split(), strict=True))
# fmt: on
MANIFEST_TEMPLATE = json.loads(
    """{"manifest_id":"rehearsal-20260903","source_sha":"4444444444444444444444444444444444444444","candidate_sha":"5555555555555555555555555555555555555555","evidence_set_sha256":"sha256:3333333333333333333333333333333333333333333333333333333333333333","approved_restore_time":"2026-09-03T12:00:00Z","observed_at":"2026-09-03T12:00:00Z","expires_at":"2026-09-03T12:15:00Z","provenance":{"repository":"CosmaNeura/cosmaneura","protected_ref":"refs/heads/master","workflow_path":".github/workflows/staging_restore_rehearsal.yml","workflow_sha":"6666666666666666666666666666666666666666","run_id":"901","run_attempt":"1","event_name":"workflow_dispatch"},"signer_claims":{"issuer":"https://token.actions.githubusercontent.com","subject":"repo:CosmaNeura/cosmaneura:environment:staging-restore-provision","sha":"6666666666666666666666666666666666666666","workflow_sha":"6666666666666666666666666666666666666666","run_id":"901","run_attempt":"1"}}"""
)
RECORD_TEMPLATE = json.loads(
    """{"schema_version":1,"binding_policy_id":"cosmaneura-staging-restore-evidence-binding-v1","evidence_manifest_policy_id":"cosmaneura-staging-evidence-manifest-v2","evidence_manifest_policy_sha256":"","manifest_id":"rehearsal-20260903","manifest_file_sha256":"","bundle_index_sha256":"sha256:2222222222222222222222222222222222222222222222222222222222222222","evidence_set_sha256":"sha256:3333333333333333333333333333333333333333333333333333333333333333","source_sha":"4444444444444444444444444444444444444444","candidate_sha":"5555555555555555555555555555555555555555","source_server_resource_id":"/subscriptions/test/source","source_database_name":"cosmaneura","approved_restore_time":"2026-09-03T12:00:00Z","observed_at":"2026-09-03T12:00:00Z","expires_at":"2026-09-03T12:15:00Z","candidate_approval_pr_number":278,"candidate_protected_master_sha":"6666666666666666666666666666666666666666","candidate_schema_revision":"777777777777","candidate_receipt_sha256":"sha256:8888888888888888888888888888888888888888888888888888888888888888","inventory_receipt_sha256":"sha256:9999999999999999999999999999999999999999999999999999999999999999","quiescence_receipt_sha256":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","server_scope_receipt_sha256":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","repository":"CosmaNeura/cosmaneura","protected_ref":"refs/heads/master","workflow_path":".github/workflows/staging_restore_rehearsal.yml","workflow_sha":"6666666666666666666666666666666666666666","run_id":"901","run_attempt":"1","event_name":"workflow_dispatch","signer_issuer":"https://token.actions.githubusercontent.com","signer_subject":"repo:CosmaNeura/cosmaneura:environment:staging-restore-provision","server_scope_authentication_receipt_sha256":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}"""
)


@pytest.fixture
def module() -> ModuleType:
    return attestation


# Dense fixture builders keep this security matrix inside the change budget.
# fmt: off
def _der_utf8(value: str) -> bytes:
    raw = value.encode()
    assert len(raw) < 128
    return bytes((12, len(raw))) + raw

def _record(module: ModuleType, manifest: bytes, **changes: Any) -> dict[str, Any]:
    value = deepcopy(RECORD_TEMPLATE)
    value["evidence_manifest_policy_sha256"] = f"sha256:{module.evidence_manifest.POLICY_SHA256}"
    value["manifest_file_sha256"] = module._digest(manifest)
    value.update(changes)
    return value

def _binding(module: ModuleType, record: dict[str, Any], cls: type[Any] | None = None) -> Any:
    value = object.__new__(cls or module.server_auth.ServerAuthenticatedSafeSubmitBinding)
    object.__setattr__(value, "_record_bytes", module._canonical(record))
    object.__setattr__(value, "_authentication_seal", module.server_auth._SERVER_AUTHENTICATED_SAFE_SUBMIT_SEAL)
    return value

def _snapshot(module: ModuleType, record: dict[str, Any], manifest: bytes) -> Any:
    result = {
        "schema_version": 1, "policy_id": record["evidence_manifest_policy_id"],
        "manifest_id": record["manifest_id"], "evidence_set_sha256": record["evidence_set_sha256"],
        "evidence_binding": {name: record[name] for name in module.server_auth.BASE_BINDING_FIELDS},
        "outcome": "contract_valid", "activation_state": "blocked",
        "activation_blockers": ["server_scope_policy_unconfigured"],
    }
    return module.evidence_manifest.ValidatedEvidenceBundle._from_validated_manifest_bytes(
        result_bytes=module._canonical(result), server_scope_bytes=b"{}\n", manifest_bytes=manifest,
    )

def _summary(policy: dict[str, Any], **changes: Any) -> dict[str, Any]:
    certificate = policy["certificate"]
    value = {name: certificate[field] for name, field in POLICY_SUMMARY_FIELDS.items()}
    value.update({
        "certificateIssuer": CERTIFICATE_ISSUER.rfc4514_string(),
        "buildSignerDigest": WORKFLOW_SHA, "sourceRepositoryDigest": WORKFLOW_SHA,
        "buildConfigDigest": WORKFLOW_SHA,
        "runInvocationURI": "https://github.com/CosmaNeura/cosmaneura/actions/runs/901/attempts/1",
        "githubWorkflowSHA": WORKFLOW_SHA, "githubWorkflowRepository": "CosmaNeura/cosmaneura",
        "githubWorkflowRef": "refs/heads/master",
    })
    value.update(changes)
    return value

def _certificate(policy: dict[str, Any], summary: dict[str, Any], *, oid_changes: dict[int, str | bytes | None] | None = None, san: str = SAN, san_critical: bool = True, subject: x509.Name | None = None, not_before: datetime = OBSERVED - timedelta(minutes=1), not_after: datetime = OBSERVED + timedelta(hours=1)) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    builder = (x509.CertificateBuilder().subject_name(subject or x509.Name([]))
        .issuer_name(CERTIFICATE_ISSUER).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(not_before)
        .not_valid_after(not_after).add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(san)]), critical=san_critical))
    oid_values: dict[int, str | bytes | None] = {
        number: summary[field] for number, field in OID_SUMMARY_FIELDS.items()
    }
    oid_values.update({
        23: policy["certificate"]["deployment_environment_extension"]["value"],
        24: policy["certificate"]["raw_token_subject_extension"]["value"], **(oid_changes or {}),
    })
    for number, value in oid_values.items():
        if value is not None:
            raw = value if isinstance(value, bytes) else _der_utf8(value)
            builder = builder.add_extension(x509.UnrecognizedExtension(ObjectIdentifier(OID_PREFIX + str(number)), raw), critical=False)
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)

def _statement(policy: dict[str, Any], manifest: bytes) -> dict[str, Any]:
    return {
        "_type": policy["statement"]["type"],
        "subject": [{"name": policy["statement"]["subject_name"], "digest": {"sha256": hashlib.sha256(manifest).hexdigest()}}],
        "predicateType": policy["statement"]["predicate_type"],
        "predicate": {"buildDefinition": {"buildType": policy["statement"]["build_type"]}},
    }

def _root(policy: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    value = {
        "mediaType": policy["trusted_root"]["media_type"],
        "certificateAuthorities": [{"uri": policy["trusted_root"]["fulcio_uri"]}],
        "timestampAuthorities": [{"uri": policy["trusted_root"]["timestamp_authority_uri"]}],
        "tlogs": [], "ctlogs": [],
    }
    return value, json.dumps(value, separators=(",", ":")).encode() + b"\n"

class FakeVerifier:
    def __init__(self, module: ModuleType, policy: dict[str, Any], output: Any) -> None:
        self.module, self.output = module, output
        self.returncode, self.stdout, self.stderr = 0, None, b""
        self.version, self.digest = policy["verifier"]["version"], policy["verifier"]["executable_sha256"]
        self.error: Exception | None = None
        self.calls: list[dict[str, Any]] = []

    def verify(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        stdout = self.module._canonical(self.output) if self.stdout is None else self.stdout
        return self.module.AttestationVerifierResponse(self.returncode, stdout, self.stderr, self.version, self.digest)
# fmt: on


def _case(
    module: ModuleType,
    *,
    manifest: bytes | None = None,
    binding_changes: dict[str, Any] | None = None,
    summary_changes: dict[str, Any] | None = None,
    oid_changes: dict[int, str | bytes | None] | None = None,
    san: str = SAN,
    san_critical: bool = True,
    subject: x509.Name | None = None,
    not_before: datetime = OBSERVED - timedelta(minutes=1),
    not_after: datetime = OBSERVED + timedelta(hours=1),
) -> dict[str, Any]:
    policy = json.loads(POLICY.read_text())
    policy["activation_ready"] = True
    policy["activation"] = dict.fromkeys(policy["activation"], True)
    policy["verifier"]["executable_sha256"] = VERIFIER_DIGEST
    root, root_bytes = _root(policy)
    policy["trusted_root"]["sha256"] = (
        f"sha256:{hashlib.sha256(root_bytes).hexdigest()}"
    )
    manifest = manifest or json.dumps(MANIFEST_TEMPLATE, indent=2).encode() + b"\n"
    record = _record(module, manifest, **(binding_changes or {}))
    summary = _summary(policy, **(summary_changes or {}))
    certificate = _certificate(
        policy,
        summary,
        oid_changes=oid_changes,
        san=san,
        san_critical=san_critical,
        subject=subject,
        not_before=not_before,
        not_after=not_after,
    )
    statement = _statement(policy, manifest)
    # These compact literals mirror the wire documents and are kept together for audit.
    # fmt: off
    bundle = {
        "mediaType": policy["statement"]["media_type"],
        "verificationMaterial": {
            "certificate": {"rawBytes": base64.b64encode(certificate).decode()},
            "timestampVerificationData": {"rfc3161Timestamps": [{"signedTimestamp": "AA=="}]},
        },
        "dsseEnvelope": {
            "payloadType": "application/vnd.in-toto+json",
            "payload": base64.b64encode(module._canonical(statement)).decode(),
            "signatures": [{"keyid": "", "sig": "AA=="}],
        },
    }
    identity = {
        "subjectAlternativeName": {"subjectAlternativeName": SAN},
        "issuer": {"issuer": "", "regexp": ".*"},
        "runnerEnvironment": "github-hosted",
    }
    output = [
        {
            "attestation": {"bundle": deepcopy(bundle), "bundle_url": "", "initiator": ""},
            "verificationResult": {
                "mediaType": policy["verification_result"]["media_type"],
                "signature": {"certificate": summary},
                "verifiedTimestamps": [{"type": "TimestampAuthority", "uri": policy["trusted_root"]["timestamp_authority_uri"], "timestamp": "2026-09-03T12:04:59.123456789Z"}],
                "verifiedIdentity": identity,
                "statement": deepcopy(statement),
            },
        }
    ]
    policy_bytes = module._canonical(policy)
    result = {
        "policy": policy, "policy_bytes": policy_bytes,
        "policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "record": record, "binding": _binding(module, record),
        "snapshot": _snapshot(module, record, manifest), "manifest_bytes": manifest,
        "root": root, "trusted_root_bytes": root_bytes, "bundle": bundle,
        "attestation_bundle_bytes": module._canonical(bundle), "certificate": certificate,
        "output": output, "verifier": FakeVerifier(module, policy, output),
        "clock": lambda: NOW,
    }
    # fmt: on
    return result


def _refresh_policy(module: ModuleType, case: dict[str, Any]) -> None:
    case["policy_bytes"] = module._canonical(case["policy"])
    case["policy_sha256"] = hashlib.sha256(case["policy_bytes"]).hexdigest()


def _refresh_bundle(module: ModuleType, case: dict[str, Any]) -> None:
    case["attestation_bundle_bytes"] = module._canonical(case["bundle"])


def _authenticate(
    module: ModuleType, case: dict[str, Any]
) -> tuple[dict[str, Any], Any]:
    names = (
        "policy_bytes policy_sha256 binding snapshot attestation_bundle_bytes "
        "trusted_root_bytes verifier clock"
    ).split()
    return module._authenticate_github_attestation_with_document(
        **{name: case[name] for name in names}
    )


def _refuses(module: ModuleType, case: dict[str, Any], code: str) -> None:
    with pytest.raises(module.AttestationFailure, match=f"^{code}$"):
        _authenticate(module, case)


def _replace_signed_statement(module: ModuleType, case: dict[str, Any]) -> None:
    statement = case["output"][0]["verificationResult"]["statement"]
    case["bundle"]["dsseEnvelope"]["payload"] = base64.b64encode(
        module._canonical(statement)
    ).decode()
    case["output"][0]["attestation"]["bundle"] = deepcopy(case["bundle"])
    _refresh_bundle(module, case)


def test_checked_in_policy_is_pinned_dormant_and_refuses_before_verifier(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, digest, raw = module.load_attestation_policy_document()
    # fmt: off
    assert digest == module.POLICY_SHA256 == hashlib.sha256(raw).hexdigest()
    assert (policy["activation_ready"], set(policy["activation"].values())) == (False, {False})
    oversized = raw.ljust(1024 * 1024 + 1, b" ")
    with pytest.raises(module.AttestationFailure, match="^attestation_policy.hash_mismatch$"):
        module._parse_policy_document(oversized, hashlib.sha256(oversized).hexdigest(), executable=True)
    monkeypatch.setattr(module, "_invoke_verifier", lambda **_: pytest.fail("verifier was reached"))
    with pytest.raises(module.AttestationFailure, match="^attestation_policy.disabled$"):
        module.authenticate_github_attestation()
    # fmt: on


def test_happy_path_receipt_seal_exact_command_and_files(module: ModuleType) -> None:
    case = _case(module)
    result, verified = _authenticate(module, case)
    receipt = result["github_attestation_receipt"]
    expected = tuple(
        f"""gh attestation verify evidence-manifest.json --bundle attestation.json
        --cert-identity {SAN} --cert-oidc-issuer https://token.actions.githubusercontent.com
        --custom-trusted-root github-trusted-root.jsonl --deny-self-hosted-runners
        --digest-alg sha256 --format=json --hostname github.com --no-public-good
        --predicate-type https://slsa.dev/provenance/v1 --repo CosmaNeura/cosmaneura
        --signer-digest {WORKFLOW_SHA} --source-digest {WORKFLOW_SHA}
        --source-ref refs/heads/master""".split()
    )
    call = case["verifier"].calls[0]
    assert call["arguments"] == expected
    assert type(call["files"]) is MappingProxyType
    assert call["files"] == {
        "evidence-manifest.json": case["manifest_bytes"],
        "attestation.json": case["attestation_bundle_bytes"],
        "github-trusted-root.jsonl": case["trusted_root_bytes"],
    }
    assert (call["timeout_seconds"], call["maximum_output_bytes"]) == (30, 4_194_304)
    assert set(receipt) == set(module.RECEIPT_FIELDS)
    assert receipt["base_binding_sha256"] == module._digest(
        case["binding"].record_bytes
    )
    assert receipt["subject_sha256"] == module._digest(case["manifest_bytes"])
    assert receipt["attestation_bundle_sha256"] == module._digest(
        case["attestation_bundle_bytes"]
    )
    assert (receipt["verified_at"], receipt["expires_at"], receipt["pass"]) == (
        "2026-09-03T12:05:00Z",
        "2026-09-03T12:06:00Z",
        True,
    )
    assert verified.receipt_bytes == module._canonical(receipt)
    assert verified.receipt_sha256 == result["github_attestation_receipt_sha256"]


def test_exact_manifest_bytes_and_complete_base_binding_are_hashed(
    module: ModuleType,
) -> None:
    compact = module._canonical(MANIFEST_TEMPLATE)
    expanded = json.dumps(MANIFEST_TEMPLATE, indent=2).encode() + b"\n"
    first, second = _case(module, manifest=compact), _case(module, manifest=expanded)
    first_result, _ = _authenticate(module, first)
    second_result, _ = _authenticate(module, second)
    assert json.loads(compact) == json.loads(expanded)
    assert (
        first_result["github_attestation_receipt"]["subject_sha256"]
        != (second_result["github_attestation_receipt"]["subject_sha256"])
    )
    assert first["verifier"].calls[0]["files"]["evidence-manifest.json"] is compact
    changed = _case(
        module,
        manifest=compact,
        binding_changes={
            "server_scope_authentication_receipt_sha256": f"sha256:{'e' * 64}"
        },
    )
    changed_result, _ = _authenticate(module, changed)
    assert (
        changed_result["github_attestation_receipt"]["subject_sha256"]
        == (first_result["github_attestation_receipt"]["subject_sha256"])
    )
    assert (
        changed_result["github_attestation_receipt"]["base_binding_sha256"]
        != (first_result["github_attestation_receipt"]["base_binding_sha256"])
    )


def test_missing_invalid_or_tampered_manifest_snapshot_refuses_early(
    module: ModuleType,
) -> None:
    cases = [_case(module) for _ in range(3)]
    cases[0]["snapshot"] = (
        module.evidence_manifest.ValidatedEvidenceBundle._from_validated_bytes(
            result_bytes=cases[0]["snapshot"].result_bytes, server_scope_bytes=b"{}\n"
        )
    )
    object.__setattr__(cases[1]["snapshot"], "_manifest_validation_seal", object())
    object.__setattr__(
        cases[2]["snapshot"], "manifest_bytes", cases[2]["manifest_bytes"] + b" "
    )
    assert cases, (
        "there is nothing to check, so the assertions below would pass vacuously"
    )
    for index, case in enumerate(cases):
        code = (
            "validated_manifest_snapshot.invalid"
            if index < 2
            else ("validated_manifest_snapshot.binding_mismatch")
        )
        _refuses(module, case, code)
        assert case["verifier"].calls == []


@pytest.mark.parametrize("failure", ["digest", "newline", "tlog", "ctlog"])
def test_root_digest_jsonl_framing_and_github_only_log_policy(
    module: ModuleType, failure: str
) -> None:
    case = _case(module)
    if failure == "digest":
        case["trusted_root_bytes"] += b"x"
    elif failure == "newline":
        case["trusted_root_bytes"] = case["trusted_root_bytes"].removesuffix(b"\n")
        case["policy"]["trusted_root"]["sha256"] = module._digest(
            case["trusted_root_bytes"]
        )
        _refresh_policy(module, case)
    else:
        case["root"][f"{failure}s"] = [{"uri": "rekor.sigstore.dev"}]
        case["trusted_root_bytes"] = module._canonical(case["root"]) + b"\n"
        case["policy"]["trusted_root"]["sha256"] = module._digest(
            case["trusted_root_bytes"]
        )
        _refresh_policy(module, case)
    _refuses(module, case, "trusted_root.invalid")
    assert case["verifier"].calls == []


def test_wrong_bundle_and_verifier_result_fail_at_their_boundaries(
    module: ModuleType,
) -> None:
    bundle, chain, tlog, result, mismatch = (_case(module) for _ in range(5))
    bundle["bundle"]["mediaType"] = "application/json"
    _refresh_bundle(module, bundle)
    _refuses(module, bundle, "attestation_bundle.invalid")
    assert bundle["verifier"].calls == []
    leaf = chain["bundle"]["verificationMaterial"].pop("certificate")
    chain["bundle"]["verificationMaterial"]["x509CertificateChain"] = {
        "certificates": [leaf]
    }
    _refresh_bundle(module, chain)
    _refuses(module, chain, "attestation_bundle.invalid")
    assert chain["verifier"].calls == []
    tlog["bundle"]["verificationMaterial"]["tlogEntries"] = [{}]
    _refresh_bundle(module, tlog)
    _refuses(module, tlog, "attestation_bundle.invalid")
    result["output"][:] = []
    _refuses(module, result, "verification_result.invalid")
    mismatch["output"][0]["attestation"]["bundle_url"] = "https://example.invalid"
    _refuses(module, mismatch, "verification_result.bundle_mismatch")


@pytest.mark.parametrize(
    "failure", ["payload", "identity", "type", "predicate", "name", "digest"]
)
def test_statement_and_subject_are_exactly_bound(
    module: ModuleType, failure: str
) -> None:
    case = _case(module)
    statement = case["output"][0]["verificationResult"]["statement"]
    if failure == "payload":
        statement["predicate"]["unbound"] = True
        code = "verification_result.statement_mismatch"
    elif failure == "identity":
        case["output"][0]["verificationResult"]["verifiedIdentity"] = {}
        code = "verification_result.identity_mismatch"
    else:
        if failure == "type":
            statement["_type"] = "https://in-toto.io/Statement/v0.1"
        elif failure == "predicate":
            statement["predicate"]["buildDefinition"]["buildType"] = "attacker"
        elif failure == "name":
            statement["subject"][0]["name"] = "other.json"
        else:
            statement["subject"][0]["digest"]["sha256"] = "f" * 64
        _replace_signed_statement(module, case)
        code = "attestation_statement.binding_mismatch"
    _refuses(module, case, code)


# fmt: off
@pytest.mark.parametrize(("kwargs", "code"), [
    ({"summary_changes": {"runnerEnvironment": "self-hosted"}}, "binding_mismatch"),
    ({"san": "https://github.com/attacker/workflow.yml@refs/heads/main"}, "binding_mismatch"),
    ({"san_critical": False}, "binding_mismatch"),
    ({"subject": x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "attacker")])}, "binding_mismatch"),
    ({"oid_changes": {8: None, 1: "https://token.actions.githubusercontent.com"}}, "claim_invalid"),
    ({"oid_changes": {11: "self-hosted"}}, "binding_mismatch"),
    ({"oid_changes": {23: None}}, "environment_invalid"),
    ({"oid_changes": {24: None}}, "subject_invalid"),
    ({"oid_changes": {23: b"\x16\x03bad"}}, "environment_invalid"),
])
# fmt: on
def test_certificate_fields_san_and_github_oid_claims_are_exact(
    module: ModuleType, kwargs: dict[str, Any], code: str
) -> None:
    _refuses(module, _case(module, **kwargs), f"attestation_certificate.{code}")


# fmt: off
@pytest.mark.parametrize("changes", [
    {"type": "TransparencyLog"}, {"uri": "timestamp.sigstore.dev"},
    {"timestamp": "2026-09-03T11:59:59Z"}, {"timestamp": "2026-09-03T12:05:01Z"},
    {"timestamp": "2026-09-03T12:05:00.000000001Z"}, {"expired_certificate": True},
])
# fmt: on
def test_only_fresh_github_tsa_timestamps_are_accepted(
    module: ModuleType, changes: dict[str, Any]
) -> None:
    changes = dict(changes)
    expired = changes.pop("expired_certificate", False)
    not_after = datetime(2026, 9, 3, 12, 4, tzinfo=UTC) if expired else (
        OBSERVED + timedelta(hours=1)
    )
    case = _case(module, not_after=not_after)
    timestamps = case["output"][0]["verificationResult"]["verifiedTimestamps"]
    timestamps[0].update(changes)
    _refuses(module, case, "verified_timestamp.invalid")


# fmt: off
@pytest.mark.parametrize(("attribute", "value"), [
    ("error", RuntimeError("secret verifier detail")),
    ("returncode", 1), ("stdout", b""), ("stderr", b"diagnostic"),
    ("version", "2.96.0"), ("digest", f"sha256:{'e' * 64}"),
])
# fmt: on
def test_verifier_status_output_version_and_digest_are_exact(
    module: ModuleType, attribute: str, value: Any
) -> None:
    case = _case(module)
    setattr(case["verifier"], attribute, value)
    code = "trusted_verifier.unavailable" if attribute == "error" else (
        "trusted_verifier.invalid_result"
    )
    _refuses(module, case, code)


def test_verified_attestation_rejects_wrong_seal_subclass_and_tamper(
    module: ModuleType,
) -> None:
    _, trusted = _authenticate(module, _case(module))
    unsealed = module.VerifiedGitHubAttestation()
    _sealed_failures = [unsealed]
    forged = object.__new__(module.VerifiedGitHubAttestation)
    object.__setattr__(forged, "_receipt_bytes", trusted.receipt_bytes)
    object.__setattr__(forged, "_receipt_sha256", trusted.receipt_sha256)
    object.__setattr__(forged, "_attestation_seal", object())
    _sealed_failures.append(forged)

    class AttestationSubclass(module.VerifiedGitHubAttestation):
        pass

    subclass = object.__new__(AttestationSubclass)
    for name in module.VerifiedGitHubAttestation.__slots__:
        object.__setattr__(subclass, name, getattr(trusted, name))
    _sealed_failures.append(subclass)
    for candidate in _sealed_failures:
        with pytest.raises(
            module.AttestationFailure, match="^github_attestation.invalid$"
        ):
            _ = candidate.receipt_bytes
    object.__setattr__(trusted, "_receipt_bytes", trusted.receipt_bytes + b" ")
    with pytest.raises(module.AttestationFailure, match="^github_attestation.invalid$"):
        _ = trusted.receipt


def test_upstream_binding_subclass_and_tampered_bytes_refuse_before_verifier(
    module: ModuleType,
) -> None:
    class BindingSubclass(module.server_auth.ServerAuthenticatedSafeSubmitBinding):
        pass

    cases = [_case(module), _case(module)]
    cases[0]["binding"] = _binding(module, cases[0]["record"], BindingSubclass)
    object.__setattr__(
        cases[1]["binding"], "_record_bytes", cases[1]["binding"].record_bytes + b" "
    )
    assert cases, 'there is nothing to check, so the assertions below would pass vacuously'
    for case in cases:
        _refuses(module, case, "server_authenticated_binding.invalid")
        assert case["verifier"].calls == []


def test_attestation_partial_cannot_cross_safe_submit_final_binding(
    module: ModuleType,
) -> None:
    _, partial = _authenticate(module, _case(module))
    with pytest.raises(
        safe_submit.SafeSubmitError, match="^evidence_binding_type_invalid$"
    ):
        safe_submit.SafeRestoreSubmitter._binding_record(partial)
