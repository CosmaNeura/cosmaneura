#!/usr/bin/env python3
"""Authenticate one offline GitHub attestation; the public path remains disabled."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn, Protocol

from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID, ObjectIdentifier

try:
    from scripts import authenticate_staging_server_scope_receipt as server_auth
    from scripts import validate_staging_evidence_manifest as evidence_manifest
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import authenticate_staging_server_scope_receipt as server_auth
    import validate_staging_evidence_manifest as evidence_manifest


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT.joinpath(
    "contracts/staging-rehearsal/v1/github-attestation-authentication-policy.json"
)
POLICY_SHA256 = "df7c2a2fca20dc05b3814d0762a286d6a9ff695e1ee7527ad9bd995f39b3fc1d"
POLICY_TEMPLATE_SHA256 = (
    "a630a6b3050201b5a65e348ab2341ef52f568bff161a509a0017a29e6fad17ff"
)
VERIFIER_DIGEST_PLACEHOLDER = "CONFIGURE_EXACT_LINUX_GH_2_97_0_EXECUTABLE_SHA256"
ROOT_PIN = "CONFIGURE_EXACT_INDEPENDENTLY_DELIVERED_GITHUB_ONLY_TRUSTED_ROOT_SHA256"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_RE = re.compile(r"^[1-9][0-9]{0,19}$")
UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
TIMESTAMP_RE = re.compile(
    r"^(20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
    r"(?:\.([0-9]{1,9}))?Z$"
)
CERTIFICATE_OIDS = {
    "issuer": "1.3.6.1.4.1.57264.1.8",
    "buildSignerURI": "1.3.6.1.4.1.57264.1.9",
    "buildSignerDigest": "1.3.6.1.4.1.57264.1.10",
    "runnerEnvironment": "1.3.6.1.4.1.57264.1.11",
    "sourceRepositoryURI": "1.3.6.1.4.1.57264.1.12",
    "sourceRepositoryDigest": "1.3.6.1.4.1.57264.1.13",
    "sourceRepositoryRef": "1.3.6.1.4.1.57264.1.14",
    "sourceRepositoryIdentifier": "1.3.6.1.4.1.57264.1.15",
    "sourceRepositoryOwnerURI": "1.3.6.1.4.1.57264.1.16",
    "sourceRepositoryOwnerIdentifier": "1.3.6.1.4.1.57264.1.17",
    "buildConfigURI": "1.3.6.1.4.1.57264.1.18",
    "buildConfigDigest": "1.3.6.1.4.1.57264.1.19",
    "buildTrigger": "1.3.6.1.4.1.57264.1.20",
    "runInvocationURI": "1.3.6.1.4.1.57264.1.21",
    "sourceRepositoryVisibilityAtSigning": "1.3.6.1.4.1.57264.1.22",
}
RECEIPT_FIELDS = tuple(
    (
        "schema_version policy_id policy_sha256 base_binding_sha256 manifest_id "
        "manifest_file_sha256 evidence_set_sha256 source_sha candidate_sha repository "
        "protected_ref workflow_path workflow_sha run_id run_attempt event_name "
        "attestation_bundle_sha256 statement_sha256 subject_name subject_sha256 "
        "predicate_type certificate_sha256 certificate_claims_sha256 "
        "certificate_oidc_issuer certificate_subject_alternative_name "
        "certificate_deployment_environment certificate_token_subject "
        "certificate_runner_environment certificate_run_invocation_uri "
        "trusted_root_sha256 verifier_version verifier_sha256 "
        "verified_timestamps_sha256 verified_at expires_at pass"
    ).split()
)
OUTPUT_ACTIVATION_BLOCKERS = tuple(
    "composite_binding_unimplemented workflow_integration_unimplemented "
    "restore_activation_unimplemented".split()
)
REQUIRED_CERTIFICATE_FIELDS = frozenset(
    (
        "certificateIssuer subjectAlternativeName issuer buildSignerURI "
        "buildSignerDigest runnerEnvironment sourceRepositoryURI "
        "sourceRepositoryDigest sourceRepositoryRef sourceRepositoryIdentifier "
        "sourceRepositoryOwnerURI sourceRepositoryOwnerIdentifier buildConfigURI "
        "buildConfigDigest buildTrigger runInvocationURI "
        "sourceRepositoryVisibilityAtSigning"
    ).split()
)
OPTIONAL_CERTIFICATE_FIELDS = frozenset(
    "githubWorkflowTrigger githubWorkflowSHA githubWorkflowName "
    "githubWorkflowRepository githubWorkflowRef".split()
)


class AttestationFailure(RuntimeError):
    """Bounded attestation failure safe to report without evidence contents."""


def _fail(code: str) -> NoReturn:
    raise AttestationFailure(code)


@dataclass(frozen=True, slots=True)
class AttestationVerifierResponse:
    returncode: int
    stdout: bytes = dataclass_field(repr=False)
    stderr: bytes = dataclass_field(repr=False)
    verifier_version: str
    verifier_sha256: str


class OfflineAttestationVerifier(Protocol):
    def verify(
        self,
        *,
        arguments: tuple[str, ...],
        files: Mapping[str, bytes],
        timeout_seconds: int,
        maximum_output_bytes: int,
    ) -> AttestationVerifierResponse: ...


_VERIFIED_GITHUB_ATTESTATION_SEAL = object()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class VerifiedGitHubAttestation:
    """Opaque receipt minted only after exact offline verification succeeds."""

    _receipt_bytes: bytes = dataclass_field(repr=False)
    _receipt_sha256: str = dataclass_field(repr=False)
    _attestation_seal: object = dataclass_field(repr=False, compare=False)

    def _is_authenticator_minted(self) -> bool:
        raw = getattr(self, "_receipt_bytes", None)
        digest = getattr(self, "_receipt_sha256", None)
        return (
            type(self) is VerifiedGitHubAttestation
            and getattr(self, "_attestation_seal", None)
            is _VERIFIED_GITHUB_ATTESTATION_SEAL
            and type(raw) is bytes
            and isinstance(digest, str)
            and DIGEST_RE.fullmatch(digest) is not None
            and _digest(raw) == digest
        )

    @property
    def receipt_bytes(self) -> bytes:
        if not self._is_authenticator_minted():
            _fail("github_attestation.invalid")
        return self._receipt_bytes

    @property
    def receipt_sha256(self) -> str:
        if not self._is_authenticator_minted():
            _fail("github_attestation.invalid")
        return self._receipt_sha256

    @property
    def receipt(self) -> dict[str, Any]:
        value = _load_mapping(self.receipt_bytes, "github_attestation.invalid")
        if set(value) != set(RECEIPT_FIELDS) or _canonical(value) != self.receipt_bytes:
            _fail("github_attestation.invalid")
        return dict(value)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise AttestationFailure("github_attestation.non_canonical") from None


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _load_json(raw: bytes, code: str) -> Any:
    if type(raw) is not bytes:
        _fail(code)

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(code)
            result[key] = value
        return result

    def reject_nonfinite(_value: str) -> NoReturn:
        _fail(code)

    def parse_integer(value: str) -> int:
        if len(value.removeprefix("-")) > 20:
            _fail(code)
        return int(value)

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
            parse_float=reject_nonfinite,
            parse_int=parse_integer,
        )
    except AttestationFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise AttestationFailure(code) from None
    nodes = 0

    def bounded(item: Any, depth: int = 0) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 64 or nodes > 100_000:
            _fail(code)
        if isinstance(item, str) and len(item) > 4 * 1024 * 1024:
            _fail(code)
        if isinstance(item, Mapping):
            for key, child in item.items():
                bounded(key, depth + 1)
                bounded(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                bounded(child, depth + 1)

    try:
        bounded(value)
    except RecursionError:
        raise AttestationFailure(code) from None
    return value


def _load_mapping(raw: bytes, code: str) -> Mapping[str, Any]:
    value = _load_json(raw, code)
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _expect_mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _expect_exact_keys(
    value: Any, expected: Collection[str], code: str
) -> Mapping[str, Any]:
    mapping = _expect_mapping(value, code)
    if set(mapping) != set(expected):
        _fail(code)
    return mapping


def _parse_utc(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise AttestationFailure(code) from None
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail(code)
    return parsed


def _parse_timestamp(value: Any) -> tuple[datetime, int]:
    if not isinstance(value, str):
        _fail("verified_timestamp.invalid")
    match = TIMESTAMP_RE.fullmatch(value)
    if match is None:
        _fail("verified_timestamp.invalid")
    try:
        second = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=UTC
        )
    except ValueError:
        raise AttestationFailure("verified_timestamp.invalid") from None
    return second, int((match.group(2) or "0").ljust(9, "0"))


def _trusted_now(clock: Callable[[], datetime]) -> tuple[datetime, str]:
    try:
        value = clock()
    except Exception:
        raise AttestationFailure("attestation_clock.unavailable") from None
    try:
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
            or value.microsecond != 0
        ):
            _fail("attestation_clock.invalid")
        normalized = value.astimezone(UTC)
    except AttestationFailure:
        raise
    except Exception:
        raise AttestationFailure("attestation_clock.invalid") from None
    return normalized, normalized.strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _read_policy_bytes(path: Path) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError:
        raise AttestationFailure("attestation_policy.read_failed") from None
    if type(raw) is not bytes or not 0 < len(raw) <= 1024 * 1024:
        _fail("attestation_policy.invalid")
    return raw


def validate_attestation_policy(policy: Any, *, executable: bool) -> Mapping[str, Any]:
    policy = _expect_mapping(policy, "attestation_policy.invalid")
    activation = _expect_mapping(policy.get("activation"), "attestation_policy.invalid")
    verifier = _expect_mapping(policy.get("verifier"), "attestation_policy.invalid")
    trusted_root = _expect_mapping(
        policy.get("trusted_root"), "attestation_policy.invalid"
    )
    ready = policy.get("activation_ready")
    verifier_digest = verifier.get("executable_sha256")
    root_digest = trusted_root.get("sha256")
    valid_pins = (
        isinstance(verifier_digest, str)
        and (
            verifier_digest == VERIFIER_DIGEST_PLACEHOLDER
            or DIGEST_RE.fullmatch(verifier_digest) is not None
        )
        and isinstance(root_digest, str)
        and (root_digest == ROOT_PIN or DIGEST_RE.fullmatch(root_digest) is not None)
    )
    if (
        type(ready) is not bool
        or any(type(value) is not bool for value in activation.values())
        or not valid_pins
    ):
        _fail("attestation_policy.invalid")

    normalized = dict(policy)
    normalized["activation_ready"] = False
    normalized["activation"] = dict.fromkeys(activation, False)
    normalized["verifier"] = {
        **verifier,
        "executable_sha256": VERIFIER_DIGEST_PLACEHOLDER,
    }
    normalized["trusted_root"] = {
        **trusted_root,
        "sha256": ROOT_PIN,
    }
    try:
        normalized_digest = hashlib.sha256(_canonical(normalized)).hexdigest()
    except AttestationFailure:
        _fail("attestation_policy.invalid")
    if normalized_digest != POLICY_TEMPLATE_SHA256:
        _fail("attestation_policy.invalid")
    if executable and (
        ready is not True
        or any(value is not True for value in activation.values())
        or not isinstance(verifier_digest, str)
        or not isinstance(root_digest, str)
        or DIGEST_RE.fullmatch(verifier_digest) is None
        or DIGEST_RE.fullmatch(root_digest) is None
    ):
        _fail("attestation_policy.disabled")
    return policy


def _parse_policy_document(
    raw: bytes, claimed_sha256: str, *, executable: bool
) -> Mapping[str, Any]:
    if (
        type(raw) is not bytes
        or not 0 < len(raw) <= 1024 * 1024
        or not isinstance(claimed_sha256, str)
        or HEX_RE.fullmatch(claimed_sha256) is None
        or hashlib.sha256(raw).hexdigest() != claimed_sha256
    ):
        _fail("attestation_policy.hash_mismatch")
    return validate_attestation_policy(
        _load_mapping(raw, "attestation_policy.invalid"), executable=executable
    )


def load_attestation_policy_document(
    path: Path = POLICY_PATH,
) -> tuple[Mapping[str, Any], str, bytes]:
    raw = _read_policy_bytes(path)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != POLICY_SHA256:
        _fail("attestation_policy.hash_mismatch")
    return _parse_policy_document(raw, digest, executable=False), digest, raw


def _validate_binding_and_snapshot(
    *,
    binding: server_auth.ServerAuthenticatedSafeSubmitBinding,
    snapshot: evidence_manifest.ValidatedEvidenceBundle,
    policy: Mapping[str, Any],
) -> tuple[Mapping[str, Any], bytes, Mapping[str, Any], bytes, datetime, datetime]:
    if (
        type(binding) is not server_auth.ServerAuthenticatedSafeSubmitBinding
        or not binding._is_authenticator_minted()
    ):
        _fail("server_authenticated_binding.invalid")
    binding_bytes = binding.record_bytes
    record = _expect_exact_keys(
        _load_mapping(binding_bytes, "server_authenticated_binding.invalid"),
        server_auth.SERVER_AUTHENTICATED_SAFE_SUBMIT_FIELDS,
        "server_authenticated_binding.invalid",
    )
    if (
        _canonical(record) != binding_bytes
        or record.get("evidence_manifest_policy_id")
        != policy["evidence_manifest_policy"]["policy_id"]
        or record.get("evidence_manifest_policy_sha256")
        != policy["evidence_manifest_policy"]["sha256"]
        or record.get("repository") != policy["repository"]["name_with_owner"]
        or record.get("protected_ref") != policy["repository"]["protected_ref"]
        or record.get("workflow_path") != policy["repository"]["workflow_path"]
        or record.get("event_name") != policy["repository"]["event_name"]
        or record.get("signer_issuer") != policy["certificate"]["oidc_issuer"]
        or record.get("signer_subject")
        != policy["certificate"]["raw_token_subject_extension"]["value"]
        or not isinstance(record.get("workflow_sha"), str)
        or SHA_RE.fullmatch(record["workflow_sha"]) is None
        or not isinstance(record.get("run_id"), str)
        or RUN_RE.fullmatch(record["run_id"]) is None
        or not isinstance(record.get("run_attempt"), str)
        or RUN_RE.fullmatch(record["run_attempt"]) is None
    ):
        _fail("server_authenticated_binding.invalid")
    for field in (
        "manifest_file_sha256",
        "evidence_set_sha256",
        "server_scope_authentication_receipt_sha256",
    ):
        if (
            not isinstance(record.get(field), str)
            or DIGEST_RE.fullmatch(record[field]) is None
        ):
            _fail("server_authenticated_binding.invalid")
    if (
        type(snapshot) is not evidence_manifest.ValidatedEvidenceBundle
        or not snapshot._is_validated_snapshot()
        or not snapshot._has_validated_manifest_snapshot()
    ):
        _fail("validated_manifest_snapshot.invalid")
    result_bytes = snapshot.result_bytes
    manifest_bytes = snapshot.manifest_bytes
    if (
        type(result_bytes) is not bytes
        or not 0 < len(result_bytes) <= evidence_manifest.MAXIMUM_FILE_BYTES
        or type(manifest_bytes) is not bytes
        or not 0 < len(manifest_bytes) <= evidence_manifest.MAXIMUM_FILE_BYTES
    ):
        _fail("validated_manifest_snapshot.invalid")
    result = _expect_exact_keys(
        _load_mapping(result_bytes, "validated_manifest_snapshot.invalid"),
        {
            "schema_version",
            "policy_id",
            "manifest_id",
            "evidence_set_sha256",
            "evidence_binding",
            "outcome",
            "activation_state",
            "activation_blockers",
        },
        "validated_manifest_snapshot.invalid",
    )
    manifest = _load_mapping(manifest_bytes, "validated_manifest_snapshot.invalid")
    base_record = {field: record[field] for field in server_auth.BASE_BINDING_FIELDS}
    provenance = _expect_exact_keys(
        manifest.get("provenance"),
        {
            "repository",
            "protected_ref",
            "workflow_path",
            "workflow_sha",
            "run_id",
            "run_attempt",
            "event_name",
        },
        "validated_manifest_snapshot.invalid",
    )
    signer = _expect_mapping(
        manifest.get("signer_claims"), "validated_manifest_snapshot.invalid"
    )
    if (
        _canonical(result) != result_bytes
        or result.get("schema_version") != 1
        or result.get("policy_id") != policy["evidence_manifest_policy"]["policy_id"]
        or result.get("manifest_id") != record.get("manifest_id")
        or result.get("evidence_set_sha256") != record.get("evidence_set_sha256")
        or result.get("outcome") != "contract_valid"
        or result.get("activation_state") != "blocked"
        or result.get("evidence_binding") != base_record
        or _digest(manifest_bytes) != record["manifest_file_sha256"]
        or manifest.get("manifest_id") != record.get("manifest_id")
        or manifest.get("source_sha") != record.get("source_sha")
        or manifest.get("candidate_sha") != record.get("candidate_sha")
        or manifest.get("evidence_set_sha256") != record.get("evidence_set_sha256")
        or manifest.get("approved_restore_time") != record.get("approved_restore_time")
        or manifest.get("observed_at") != record.get("observed_at")
        or manifest.get("expires_at") != record.get("expires_at")
        or provenance.get("repository") != record.get("repository")
        or provenance.get("protected_ref") != record.get("protected_ref")
        or provenance.get("workflow_path") != record.get("workflow_path")
        or provenance.get("workflow_sha") != record.get("workflow_sha")
        or provenance.get("run_id") != record.get("run_id")
        or provenance.get("run_attempt") != record.get("run_attempt")
        or provenance.get("event_name") != record.get("event_name")
        or signer.get("issuer") != record.get("signer_issuer")
        or signer.get("subject") != record.get("signer_subject")
        or signer.get("sha") != record.get("workflow_sha")
        or signer.get("workflow_sha") != record.get("workflow_sha")
        or signer.get("run_id") != record.get("run_id")
        or signer.get("run_attempt") != record.get("run_attempt")
    ):
        _fail("validated_manifest_snapshot.binding_mismatch")
    observed = _parse_utc(record.get("observed_at"), "binding_time.invalid")
    expires = _parse_utc(record.get("expires_at"), "binding_time.invalid")
    if expires <= observed or (expires - observed).total_seconds() > 900:
        _fail("server_authenticated_binding.stale")
    return record, binding_bytes, manifest, manifest_bytes, observed, expires


def _validate_trusted_root(raw: bytes, policy: Mapping[str, Any]) -> None:
    root_policy = policy["trusted_root"]
    if (
        type(raw) is not bytes
        or not 0 < len(raw) <= policy["verifier"]["maximum_trusted_root_bytes"]
        or not raw.endswith(b"\n")
        or raw.count(b"\n") != 1
        or _digest(raw) != root_policy["sha256"]
    ):
        _fail("trusted_root.invalid")
    root = _expect_exact_keys(
        _load_mapping(raw[:-1], "trusted_root.invalid"),
        {
            "mediaType",
            "certificateAuthorities",
            "timestampAuthorities",
            "tlogs",
            "ctlogs",
        },
        "trusted_root.invalid",
    )
    cas = root["certificateAuthorities"]
    tsas = root["timestampAuthorities"]
    if (
        root["mediaType"] != root_policy["media_type"]
        or not isinstance(cas, list)
        or len(cas) != 1
        or not isinstance(cas[0], Mapping)
        or cas[0].get("uri") != root_policy["fulcio_uri"]
        or not isinstance(tsas, list)
        or len(tsas) != 1
        or not isinstance(tsas[0], Mapping)
        or tsas[0].get("uri") != root_policy["timestamp_authority_uri"]
        or root["tlogs"] != []
        or root["ctlogs"] != []
    ):
        _fail("trusted_root.invalid")


def _validate_bundle(raw: bytes, policy: Mapping[str, Any]) -> Mapping[str, Any]:
    if (
        type(raw) is not bytes
        or not 0 < len(raw) <= policy["verifier"]["maximum_bundle_bytes"]
    ):
        _fail("attestation_bundle.invalid")
    bundle = _expect_exact_keys(
        _load_mapping(raw, "attestation_bundle.invalid"),
        {"mediaType", "verificationMaterial", "dsseEnvelope"},
        "attestation_bundle.invalid",
    )
    if bundle["mediaType"] != policy["statement"]["media_type"]:
        _fail("attestation_bundle.invalid")
    material = _expect_exact_keys(
        bundle["verificationMaterial"],
        {"certificate", "timestampVerificationData"},
        "attestation_bundle.invalid",
    )
    timestamp_data = _expect_exact_keys(
        material["timestampVerificationData"],
        {"rfc3161Timestamps"},
        "attestation_bundle.invalid",
    )
    embedded_timestamps = timestamp_data["rfc3161Timestamps"]
    if not isinstance(embedded_timestamps, list) or not embedded_timestamps:
        _fail("attestation_bundle.invalid")
    for timestamp in embedded_timestamps:
        timestamp = _expect_exact_keys(
            timestamp, {"signedTimestamp"}, "attestation_bundle.invalid"
        )
        _decode_base64(timestamp["signedTimestamp"], "attestation_bundle.invalid")
    envelope = _expect_exact_keys(
        bundle["dsseEnvelope"],
        {"payloadType", "payload", "signatures"},
        "attestation_bundle.invalid",
    )
    if (
        envelope["payloadType"] != "application/vnd.in-toto+json"
        or not isinstance(envelope["payload"], str)
        or not isinstance(envelope["signatures"], list)
        or len(envelope["signatures"]) != 1
    ):
        _fail("attestation_bundle.invalid")
    signature = _expect_mapping(envelope["signatures"][0], "attestation_bundle.invalid")
    if set(signature) not in ({"sig"}, {"keyid", "sig"}):
        _fail("attestation_bundle.invalid")
    _decode_base64(signature["sig"], "attestation_bundle.invalid")
    if "keyid" in signature and not isinstance(signature["keyid"], str):
        _fail("attestation_bundle.invalid")
    return bundle


def _decode_base64(value: Any, code: str) -> bytes:
    if not isinstance(value, str):
        _fail(code)
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise AttestationFailure(code) from None
    if not raw:
        _fail(code)
    return raw


def _extract_leaf_certificate(bundle: Mapping[str, Any]) -> bytes:
    material = _expect_mapping(
        bundle["verificationMaterial"], "attestation_certificate.invalid"
    )
    certificate = _expect_exact_keys(
        material.get("certificate"), {"rawBytes"}, "attestation_certificate.invalid"
    )
    return _decode_base64(certificate["rawBytes"], "attestation_certificate.invalid")


def _parse_der_utf8(value: bytes, expected_tag: int, code: str) -> str:
    if type(value) is not bytes or len(value) < 2 or value[0] != expected_tag:
        _fail(code)
    length = value[1]
    if length & 0x80 or length != len(value) - 2:
        _fail(code)
    try:
        decoded = value[2:].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise AttestationFailure(code) from None
    if "\x00" in decoded:
        _fail(code)
    return decoded


def _extension_utf8(
    certificate: x509.Certificate,
    *,
    oid: ObjectIdentifier,
    expected_tag: int,
    code: str,
) -> str:
    try:
        extension = certificate.extensions.get_extension_for_oid(oid)
    except (x509.ExtensionNotFound, x509.DuplicateExtension):
        raise AttestationFailure(code) from None
    if extension.critical or not isinstance(
        extension.value, x509.UnrecognizedExtension
    ):
        _fail(code)
    return _parse_der_utf8(extension.value.value, expected_tag, code)


def _validate_certificate(
    *,
    raw: bytes,
    summary: Mapping[str, Any],
    record: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[str, str, datetime, datetime]:
    try:
        certificate = x509.load_der_x509_certificate(raw)
        extensions = certificate.extensions
        san_extension = extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )
    except (ValueError, x509.ExtensionNotFound, x509.DuplicateExtension):
        raise AttestationFailure("attestation_certificate.invalid") from None
    certificate_policy = policy["certificate"]
    if not isinstance(san_extension.value, x509.SubjectAlternativeName):
        _fail("attestation_certificate.invalid")
    try:
        san_values = san_extension.value.get_values_for_type(
            x509.UniformResourceIdentifier
        )
        all_names = list(san_extension.value)
        issuer_name = certificate.issuer.rfc4514_string()
        issuer_organizations = certificate.issuer.get_attributes_for_oid(
            NameOID.ORGANIZATION_NAME
        )
    except (TypeError, ValueError):
        raise AttestationFailure("attestation_certificate.invalid") from None
    if (
        len(certificate.subject) != 0
        or san_extension.critical
        is not certificate_policy["subject_alternative_name_critical"]
        or certificate_policy["subject_distinguished_name_empty"] is not True
        or len(all_names) != 1
        or san_values != [certificate_policy["subject_alternative_name"]]
        or summary.get("subjectAlternativeName") != san_values[0]
        or summary.get("certificateIssuer") != issuer_name
        or [attribute.value for attribute in issuer_organizations]
        != [policy["trusted_root"]["certificate_authority_organization"]]
    ):
        _fail("attestation_certificate.binding_mismatch")
    deployment_policy = certificate_policy["deployment_environment_extension"]
    deployment = _extension_utf8(
        certificate,
        oid=ObjectIdentifier(deployment_policy["oid"]),
        expected_tag=deployment_policy["der_tag"],
        code="attestation_certificate.environment_invalid",
    )
    subject_policy = certificate_policy["raw_token_subject_extension"]
    token_subject = _extension_utf8(
        certificate,
        oid=ObjectIdentifier(subject_policy["oid"]),
        expected_tag=subject_policy["der_tag"],
        code="attestation_certificate.subject_invalid",
    )
    expected_run_uri = (
        f"https://github.com/{record['repository']}/actions/runs/"
        f"{record['run_id']}/attempts/{record['run_attempt']}"
    )
    expected = {
        "subjectAlternativeName": certificate_policy["subject_alternative_name"],
        "issuer": certificate_policy["oidc_issuer"],
        "buildSignerURI": certificate_policy["build_signer_uri"],
        "buildSignerDigest": record["workflow_sha"],
        "runnerEnvironment": certificate_policy["runner_environment"],
        "sourceRepositoryURI": certificate_policy["source_repository_uri"],
        "sourceRepositoryDigest": record["workflow_sha"],
        "sourceRepositoryRef": certificate_policy["source_repository_ref"],
        "sourceRepositoryIdentifier": certificate_policy[
            "source_repository_identifier"
        ],
        "sourceRepositoryOwnerURI": certificate_policy["source_repository_owner_uri"],
        "sourceRepositoryOwnerIdentifier": certificate_policy[
            "source_repository_owner_identifier"
        ],
        "buildConfigURI": certificate_policy["build_config_uri"],
        "buildConfigDigest": record["workflow_sha"],
        "buildTrigger": certificate_policy["workflow_trigger"],
        "runInvocationURI": expected_run_uri,
        "sourceRepositoryVisibilityAtSigning": certificate_policy[
            "source_repository_visibility"
        ],
    }
    raw_claims = {
        field: _extension_utf8(
            certificate,
            oid=ObjectIdentifier(oid),
            expected_tag=12,
            code="attestation_certificate.claim_invalid",
        )
        for field, oid in CERTIFICATE_OIDS.items()
    }
    keys = set(summary)
    if (
        not REQUIRED_CERTIFICATE_FIELDS <= keys
        or not keys <= REQUIRED_CERTIFICATE_FIELDS | OPTIONAL_CERTIFICATE_FIELDS
        or any(summary.get(key) != value for key, value in expected.items())
        or raw_claims != {field: expected[field] for field in CERTIFICATE_OIDS}
        or deployment != deployment_policy["value"]
        or token_subject != subject_policy["value"]
    ):
        _fail("attestation_certificate.binding_mismatch")
    optional_expected = {
        "githubWorkflowTrigger": certificate_policy["workflow_trigger"],
        "githubWorkflowSHA": record["workflow_sha"],
        "githubWorkflowName": certificate_policy["workflow_name"],
        "githubWorkflowRepository": record["repository"],
        "githubWorkflowRef": record["protected_ref"],
    }
    if any(
        key in summary and summary[key] != value
        for key, value in optional_expected.items()
    ):
        _fail("attestation_certificate.binding_mismatch")
    return (
        deployment,
        token_subject,
        certificate.not_valid_before_utc,
        certificate.not_valid_after_utc,
    )


def _build_verifier_arguments(
    policy: Mapping[str, Any], record: Mapping[str, Any]
) -> tuple[str, ...]:
    return (
        policy["verifier"]["executable"],
        "attestation",
        "verify",
        "evidence-manifest.json",
        "--bundle",
        "attestation.json",
        "--cert-identity",
        policy["certificate"]["subject_alternative_name"],
        "--cert-oidc-issuer",
        policy["certificate"]["oidc_issuer"],
        "--custom-trusted-root",
        "github-trusted-root.jsonl",
        "--deny-self-hosted-runners",
        "--digest-alg",
        "sha256",
        "--format=json",
        "--hostname",
        "github.com",
        "--no-public-good",
        "--predicate-type",
        policy["statement"]["predicate_type"],
        "--repo",
        policy["repository"]["name_with_owner"],
        "--signer-digest",
        record["workflow_sha"],
        "--source-digest",
        record["workflow_sha"],
        "--source-ref",
        record["protected_ref"],
    )


def _invoke_verifier(
    *,
    verifier: OfflineAttestationVerifier,
    arguments: tuple[str, ...],
    manifest_bytes: bytes,
    bundle_bytes: bytes,
    trusted_root_bytes: bytes,
    policy: Mapping[str, Any],
) -> bytes:
    files = MappingProxyType(
        {
            "evidence-manifest.json": manifest_bytes,
            "attestation.json": bundle_bytes,
            "github-trusted-root.jsonl": trusted_root_bytes,
        }
    )
    try:
        response = verifier.verify(
            arguments=arguments,
            files=files,
            timeout_seconds=policy["verifier"]["request_timeout_seconds"],
            maximum_output_bytes=policy["verifier"]["maximum_output_bytes"],
        )
    except Exception:
        raise AttestationFailure("trusted_verifier.unavailable") from None
    maximum = policy["verifier"]["maximum_output_bytes"]
    if (
        type(response) is not AttestationVerifierResponse
        or type(response.returncode) is not int
        or response.returncode != 0
        or type(response.stdout) is not bytes
        or not 0 < len(response.stdout) <= maximum
        or type(response.stderr) is not bytes
        or response.stderr != b""
        or response.verifier_version != policy["verifier"]["version"]
        or response.verifier_sha256 != policy["verifier"]["executable_sha256"]
    ):
        _fail("trusted_verifier.invalid_result")
    return response.stdout


def _validate_verification_output(
    *,
    raw: bytes,
    bundle: Mapping[str, Any],
    manifest_bytes: bytes,
    record: Mapping[str, Any],
    observed: datetime,
    expires: datetime,
    now: datetime,
    policy: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any], bytes, list[Any], str, str]:
    output = _load_json(raw, "verification_result.invalid")
    if not isinstance(output, list) or len(output) != 1:
        _fail("verification_result.invalid")
    entry = _expect_exact_keys(
        output[0],
        policy["verification_result"]["top_level_fields"],
        "verification_result.invalid",
    )
    attestation = _expect_exact_keys(
        entry["attestation"],
        {"bundle", "bundle_url", "initiator"},
        "verification_result.invalid",
    )
    if (
        attestation["bundle"] != bundle
        or attestation["bundle_url"] != ""
        or attestation["initiator"] != ""
    ):
        _fail("verification_result.bundle_mismatch")
    verification = _expect_exact_keys(
        entry["verificationResult"],
        {
            "mediaType",
            "signature",
            "verifiedTimestamps",
            "verifiedIdentity",
            "statement",
        },
        "verification_result.invalid",
    )
    if verification["mediaType"] != policy["verification_result"]["media_type"]:
        _fail("verification_result.invalid")
    expected_identity = {
        "subjectAlternativeName": {
            "subjectAlternativeName": policy["certificate"]["subject_alternative_name"]
        },
        "issuer": {"issuer": "", "regexp": ".*"},
        "runnerEnvironment": policy["certificate"]["runner_environment"],
    }
    if verification["verifiedIdentity"] != expected_identity:
        _fail("verification_result.identity_mismatch")
    signature = _expect_exact_keys(
        verification["signature"], {"certificate"}, "verification_result.invalid"
    )
    certificate_summary = _expect_mapping(
        signature["certificate"], "verification_result.invalid"
    )
    statement = _expect_exact_keys(
        verification["statement"],
        {"_type", "subject", "predicateType", "predicate"},
        "attestation_statement.invalid",
    )
    subjects = statement["subject"]
    if not isinstance(subjects, list) or len(subjects) != 1:
        _fail("attestation_statement.invalid")
    subject = _expect_exact_keys(
        subjects[0], {"name", "digest"}, "attestation_statement.invalid"
    )
    subject_digest = _expect_exact_keys(
        subject["digest"], {"sha256"}, "attestation_statement.invalid"
    )
    manifest_hex = hashlib.sha256(manifest_bytes).hexdigest()
    predicate = _expect_mapping(statement["predicate"], "attestation_statement.invalid")
    build_definition = _expect_mapping(
        predicate.get("buildDefinition"), "attestation_statement.invalid"
    )
    if (
        statement["_type"] != policy["statement"]["type"]
        or statement["predicateType"] != policy["statement"]["predicate_type"]
        or build_definition.get("buildType") != policy["statement"]["build_type"]
        or subject["name"] != policy["statement"]["subject_name"]
        or subject_digest["sha256"] != manifest_hex
    ):
        _fail("attestation_statement.binding_mismatch")
    envelope = _expect_mapping(bundle["dsseEnvelope"], "attestation_bundle.invalid")
    payload = _decode_base64(envelope["payload"], "attestation_bundle.invalid")
    payload_statement = _load_mapping(payload, "attestation_bundle.invalid")
    if payload_statement != statement:
        _fail("verification_result.statement_mismatch")
    certificate_raw = _extract_leaf_certificate(bundle)
    (
        deployment,
        token_subject,
        certificate_not_before,
        certificate_not_after,
    ) = _validate_certificate(
        raw=certificate_raw,
        summary=certificate_summary,
        record=record,
        policy=policy,
    )
    timestamps = verification["verifiedTimestamps"]
    if (
        not isinstance(timestamps, list)
        or len(timestamps)
        < policy["verification_result"]["minimum_verified_timestamps"]
    ):
        _fail("verified_timestamp.invalid")
    for timestamp in timestamps:
        timestamp = _expect_exact_keys(
            timestamp, {"type", "uri", "timestamp"}, "verified_timestamp.invalid"
        )
        second, nanosecond = _parse_timestamp(timestamp["timestamp"])
        if (
            timestamp["type"] != "TimestampAuthority"
            or timestamp["uri"] != policy["trusted_root"]["timestamp_authority_uri"]
            or second < observed
            or second > now
            or second >= expires
            or (second == now and nanosecond > 0)
            or second < certificate_not_before
            or second >= certificate_not_after
        ):
            _fail("verified_timestamp.invalid")
    return (
        statement,
        certificate_summary,
        certificate_raw,
        timestamps,
        deployment,
        token_subject,
    )


def _authenticate_github_attestation_with_document(
    *,
    policy_bytes: bytes,
    policy_sha256: str,
    binding: server_auth.ServerAuthenticatedSafeSubmitBinding,
    snapshot: evidence_manifest.ValidatedEvidenceBundle,
    attestation_bundle_bytes: bytes,
    trusted_root_bytes: bytes,
    verifier: OfflineAttestationVerifier,
    clock: Callable[[], datetime] = _utc_now,
) -> tuple[dict[str, Any], VerifiedGitHubAttestation]:
    policy = _parse_policy_document(policy_bytes, policy_sha256, executable=True)
    record, binding_bytes, _manifest, manifest_bytes, observed, base_expires = (
        _validate_binding_and_snapshot(
            binding=binding,
            snapshot=snapshot,
            policy=policy,
        )
    )
    _validate_trusted_root(trusted_root_bytes, policy)
    bundle = _validate_bundle(attestation_bundle_bytes, policy)
    arguments = _build_verifier_arguments(policy, record)
    output = _invoke_verifier(
        verifier=verifier,
        arguments=arguments,
        manifest_bytes=manifest_bytes,
        bundle_bytes=attestation_bundle_bytes,
        trusted_root_bytes=trusted_root_bytes,
        policy=policy,
    )
    now, now_text = _trusted_now(clock)
    if now < observed or now >= base_expires:
        _fail("server_authenticated_binding.stale")
    (
        statement,
        certificate_summary,
        certificate_raw,
        timestamps,
        deployment,
        token_subject,
    ) = _validate_verification_output(
        raw=output,
        bundle=bundle,
        manifest_bytes=manifest_bytes,
        record=record,
        observed=observed,
        expires=base_expires,
        now=now,
        policy=policy,
    )
    expires = min(
        base_expires,
        now + timedelta(seconds=policy["maximum_receipt_lifetime_seconds"]),
    )
    certificate_policy = policy["certificate"]
    receipt = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "policy_sha256": f"sha256:{policy_sha256}",
        "base_binding_sha256": _digest(binding_bytes),
        "manifest_id": record["manifest_id"],
        "manifest_file_sha256": record["manifest_file_sha256"],
        "evidence_set_sha256": record["evidence_set_sha256"],
        "source_sha": record["source_sha"],
        "candidate_sha": record["candidate_sha"],
        "repository": record["repository"],
        "protected_ref": record["protected_ref"],
        "workflow_path": record["workflow_path"],
        "workflow_sha": record["workflow_sha"],
        "run_id": record["run_id"],
        "run_attempt": record["run_attempt"],
        "event_name": record["event_name"],
        "attestation_bundle_sha256": _digest(attestation_bundle_bytes),
        "statement_sha256": _digest(_canonical(statement)),
        "subject_name": policy["statement"]["subject_name"],
        "subject_sha256": _digest(manifest_bytes),
        "predicate_type": policy["statement"]["predicate_type"],
        "certificate_sha256": _digest(certificate_raw),
        "certificate_claims_sha256": _digest(_canonical(certificate_summary)),
        "certificate_oidc_issuer": certificate_policy["oidc_issuer"],
        "certificate_subject_alternative_name": certificate_policy[
            "subject_alternative_name"
        ],
        "certificate_deployment_environment": deployment,
        "certificate_token_subject": token_subject,
        "certificate_runner_environment": certificate_policy["runner_environment"],
        "certificate_run_invocation_uri": certificate_summary["runInvocationURI"],
        "trusted_root_sha256": _digest(trusted_root_bytes),
        "verifier_version": policy["verifier"]["version"],
        "verifier_sha256": policy["verifier"]["executable_sha256"],
        "verified_timestamps_sha256": _digest(_canonical(timestamps)),
        "verified_at": now_text,
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pass": True,
    }
    if set(receipt) != set(RECEIPT_FIELDS):
        _fail("github_attestation_receipt.invalid")
    receipt_bytes = _canonical(receipt)
    receipt_sha256 = _digest(receipt_bytes)
    verified = object.__new__(VerifiedGitHubAttestation)
    object.__setattr__(verified, "_receipt_bytes", receipt_bytes)
    object.__setattr__(verified, "_receipt_sha256", receipt_sha256)
    object.__setattr__(verified, "_attestation_seal", _VERIFIED_GITHUB_ATTESTATION_SEAL)
    result = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "github_attestation_receipt": receipt,
        "github_attestation_receipt_sha256": receipt_sha256,
        "activation_state": "blocked",
        "activation_blockers": policy["output_activation_blockers"],
    }
    return result, verified


def authenticate_github_attestation() -> NoReturn:
    """Refuse until exact policy pins and trusted execution are implemented."""

    policy, _digest_value, _raw = load_attestation_policy_document()
    validate_attestation_policy(policy, executable=True)
    _fail("trusted_verifier.unimplemented")


if __name__ == "__main__":
    authenticate_github_attestation()
