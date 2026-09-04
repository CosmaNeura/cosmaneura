#!/usr/bin/env python3
"""Authenticate one external runtime record with an injected offline verifier.

This module has no default reader, verifier, network, credential, workflow, or
executor wiring.  It mints the runtime-binding signature seal only after the
exact record, bundle, root, GitHub identity, statement, and timestamp agree.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, NoReturn

from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID, ObjectIdentifier

try:
    from scripts import authenticate_staging_github_attestation as attestation
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import authenticate_staging_github_attestation as attestation
    import validate_staging_restore_runtime_binding as runtime_binding


MAXIMUM_BUNDLE_BYTES = 2_097_152
MAXIMUM_OUTPUT_BYTES = 4_194_304
MAXIMUM_JSON_NODES = 100_000
VERIFIER_TIMEOUT_SECONDS = 30
BUNDLE_MEDIA_TYPE = "application/vnd.dev.sigstore.bundle.v0.3+json"
VERIFICATION_MEDIA_TYPE = (
    "application/vnd.dev.sigstore.verificationresult+json;version=0.1"
)
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
BUILD_TYPE = "https://actions.github.io/buildtypes/workflow/v1"
FULCIO_URI = "fulcio.githubapp.com"
TIMESTAMP_AUTHORITY_URI = "timestamp.githubapp.com"
CERTIFICATE_AUTHORITY_ORGANIZATION = "GitHub, Inc."
REPOSITORY_IDENTIFIER = "881573489"
REPOSITORY_OWNER_IDENTIFIER = "186940615"
RUNTIME_RECORD_FILE = "runtime-record.json"
SIGNATURE_BUNDLE_FILE = "runtime-record.sigstore.json"
TRUSTED_ROOT_FILE = "github-trusted-root.jsonl"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DECIMAL_RE = re.compile(r"^[1-9][0-9]{0,19}$")
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
REQUIRED_CERTIFICATE_FIELDS = frozenset(
    {
        "certificateIssuer",
        "subjectAlternativeName",
        *CERTIFICATE_OIDS,
    }
)
OPTIONAL_CERTIFICATE_FIELDS = frozenset(
    {
        "githubWorkflowTrigger",
        "githubWorkflowSHA",
        "githubWorkflowName",
        "githubWorkflowRepository",
        "githubWorkflowRef",
    }
)


class RuntimeRecordSignatureFailure(RuntimeError):
    """Bounded authentication failure safe to report without signed bytes."""


def _fail(code: str) -> NoReturn:
    raise RuntimeRecordSignatureFailure(code)


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise RuntimeRecordSignatureFailure("runtime_signature.input_invalid") from None


def _load_json(raw: bytes, code: str) -> Any:
    if type(raw) is not bytes:
        _fail(code)

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                _fail(code)
            result[key] = value
        return result

    def invalid_number(_value: str) -> NoReturn:
        _fail(code)

    def integer(value: str) -> int:
        if len(value.removeprefix("-")) > 20:
            _fail(code)
        return int(value)

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=invalid_number,
            parse_float=invalid_number,
            parse_int=integer,
        )
    except RuntimeRecordSignatureFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise RuntimeRecordSignatureFailure(code) from None
    nodes = 0

    def bounded(item: Any, depth: int = 0) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 64 or nodes > MAXIMUM_JSON_NODES:
            _fail(code)
        if isinstance(item, str) and len(item) > MAXIMUM_OUTPUT_BYTES:
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
        raise RuntimeRecordSignatureFailure(code) from None
    return value


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _exact_keys(value: Any, expected: Collection[str], code: str) -> Mapping[str, Any]:
    result = _mapping(value, code)
    if set(result) != set(expected):
        _fail(code)
    return result


def _base64(value: Any, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise RuntimeRecordSignatureFailure(code) from None
    if not result:
        _fail(code)
    return result


def _utc(value: Any, code: str) -> datetime:
    if type(value) is not str or runtime_binding.UTC_RE.fullmatch(value) is None:
        _fail(code)
    try:
        result = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise RuntimeRecordSignatureFailure(code) from None
    if result.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail(code)
    return result


def _timestamp(value: Any) -> tuple[datetime, int]:
    if type(value) is not str or (match := TIMESTAMP_RE.fullmatch(value)) is None:
        _fail("runtime_signature.timestamp_invalid")
    try:
        second = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=UTC
        )
    except ValueError:
        raise RuntimeRecordSignatureFailure(
            "runtime_signature.timestamp_invalid"
        ) from None
    return second, int((match.group(2) or "0").ljust(9, "0"))


def _trusted_now(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
        valid = (
            type(value) is datetime
            and value.tzinfo is not None
            and value.utcoffset() == timedelta(0)
            and value.microsecond == 0
        )
    except Exception:
        raise RuntimeRecordSignatureFailure("runtime_signature.clock_invalid") from None
    if not valid:
        _fail("runtime_signature.clock_invalid")
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _validate_references(
    document: Mapping[str, Any],
    provision: runtime_binding.ExternalRuntimeBindingReference,
    cleanup: runtime_binding.ExternalRuntimeBindingReference,
) -> runtime_binding.ExternalRuntimeBindingReference:
    jobs = document["runtime"]["jobs"]
    for job_kind, candidate in (("provision", provision), ("cleanup", cleanup)):
        if (
            type(candidate) is not runtime_binding.ExternalRuntimeBindingReference
            or not candidate._is_bound()
            or candidate.source_job_kind != job_kind
            or candidate.source_job_resource_id != jobs[job_kind]["resource_id"]
        ):
            _fail("runtime_signature.reference_invalid")
    if any(
        getattr(provision, field) != getattr(cleanup, field)
        for field in runtime_binding.RUNTIME_REFERENCE_FIELDS
    ):
        _fail("runtime_signature.reference_invalid")
    ingress = document["runtime_record_ingress"]
    expected_uris = {
        "record_uri": (
            f"https://{ingress['account_host']}/{ingress['container_name']}/"
            f"{provision.record_sha256[7:]}.json"
        ),
        "signature_bundle_uri": (
            f"https://{ingress['account_host']}/{ingress['container_name']}/"
            f"{provision.signature_bundle_sha256[7:]}.sigstore.json"
        ),
    }
    if any(
        getattr(provision, field) != value for field, value in expected_uris.items()
    ):
        _fail("runtime_signature.reference_invalid")
    return provision


def _validate_inputs(
    document: Mapping[str, Any],
    pointer: runtime_binding.ExternalRuntimeBindingReference,
    record_bytes: bytes,
    bundle_bytes: bytes,
    root_bytes: bytes,
) -> tuple[Mapping[str, Any], datetime]:
    root_policy = document["evidence_ingress"]["private_versioned_secret_source"][
        "secrets"
    ]["github_trusted_root"]
    if (
        type(record_bytes) is not bytes
        or not 0
        < len(record_bytes)
        <= document["external_runtime_record"]["record"]["maximum_bytes"]
        or _digest(record_bytes) != pointer.record_sha256
        or type(bundle_bytes) is not bytes
        or not 0 < len(bundle_bytes) <= MAXIMUM_BUNDLE_BYTES
        or _digest(bundle_bytes) != pointer.signature_bundle_sha256
        or type(root_bytes) is not bytes
        or not root_policy["minimum_bytes"]
        <= len(root_bytes)
        <= root_policy["maximum_bytes"]
        or _digest(root_bytes) != root_policy["sha256"]
    ):
        _fail("runtime_signature.input_invalid")
    record = _exact_keys(
        _load_json(record_bytes, "runtime_signature.input_invalid"),
        runtime_binding.RUNTIME_RECORD_FIELDS,
        "runtime_signature.input_invalid",
    )
    if _canonical(record) != record_bytes:
        _fail("runtime_signature.input_invalid")
    created_at = _utc(record.get("created_at"), "runtime_signature.input_invalid")
    if created_at <= _utc(
        record.get("image_published_at"), "runtime_signature.input_invalid"
    ):
        _fail("runtime_signature.input_invalid")
    return record, created_at


def _validate_root(raw: bytes) -> str:
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        _fail("runtime_signature.trusted_root_invalid")
    root = _exact_keys(
        _load_json(raw[:-1], "runtime_signature.trusted_root_invalid"),
        {
            "mediaType",
            "certificateAuthorities",
            "timestampAuthorities",
            "tlogs",
            "ctlogs",
        },
        "runtime_signature.trusted_root_invalid",
    )
    certificate_authorities = root["certificateAuthorities"]
    timestamp_authorities = root["timestampAuthorities"]
    if (
        root["mediaType"] != "application/vnd.dev.sigstore.trustedroot+json;version=0.1"
        or not isinstance(certificate_authorities, list)
        or len(certificate_authorities) != 1
        or not isinstance(certificate_authorities[0], Mapping)
        or certificate_authorities[0].get("uri") != FULCIO_URI
        or not isinstance(timestamp_authorities, list)
        or len(timestamp_authorities) != 1
        or not isinstance(timestamp_authorities[0], Mapping)
        or timestamp_authorities[0].get("uri") != TIMESTAMP_AUTHORITY_URI
        or root["tlogs"] != []
        or root["ctlogs"] != []
    ):
        _fail("runtime_signature.trusted_root_invalid")
    return TIMESTAMP_AUTHORITY_URI


def _validate_bundle(raw: bytes, expected_media_type: str) -> Mapping[str, Any]:
    bundle = _exact_keys(
        _load_json(raw, "runtime_signature.bundle_invalid"),
        {"mediaType", "verificationMaterial", "dsseEnvelope"},
        "runtime_signature.bundle_invalid",
    )
    material = _exact_keys(
        bundle.get("verificationMaterial"),
        {"certificate", "timestampVerificationData"},
        "runtime_signature.bundle_invalid",
    )
    certificate = _exact_keys(
        material.get("certificate"),
        {"rawBytes"},
        "runtime_signature.bundle_invalid",
    )
    timestamp_data = _exact_keys(
        material.get("timestampVerificationData"),
        {"rfc3161Timestamps"},
        "runtime_signature.bundle_invalid",
    )
    timestamps = timestamp_data["rfc3161Timestamps"]
    envelope = _exact_keys(
        bundle.get("dsseEnvelope"),
        {"payloadType", "payload", "signatures"},
        "runtime_signature.bundle_invalid",
    )
    signatures = envelope["signatures"]
    if (
        bundle.get("mediaType") != expected_media_type
        or expected_media_type != BUNDLE_MEDIA_TYPE
        or not isinstance(timestamps, list)
        or len(timestamps) != 1
        or envelope.get("payloadType") != "application/vnd.in-toto+json"
        or not isinstance(signatures, list)
        or len(signatures) != 1
    ):
        _fail("runtime_signature.bundle_invalid")
    timestamp = _exact_keys(
        timestamps[0], {"signedTimestamp"}, "runtime_signature.bundle_invalid"
    )
    signature = _mapping(signatures[0], "runtime_signature.bundle_invalid")
    if set(signature) not in ({"sig"}, {"keyid", "sig"}):
        _fail("runtime_signature.bundle_invalid")
    _base64(certificate["rawBytes"], "runtime_signature.bundle_invalid")
    _base64(timestamp["signedTimestamp"], "runtime_signature.bundle_invalid")
    _base64(envelope["payload"], "runtime_signature.bundle_invalid")
    _base64(signature["sig"], "runtime_signature.bundle_invalid")
    if "keyid" in signature and type(signature["keyid"]) is not str:
        _fail("runtime_signature.bundle_invalid")
    return bundle


def _arguments(document: Mapping[str, Any]) -> tuple[str, ...]:
    signature = document["external_runtime_record"]["signature"]
    record_binding = document["external_runtime_record"]["binding"]
    verifier = document["offline_github_verifier"]
    identity = signature["certificate_identity"]
    identity_prefix = (
        f"https://github.com/{record_binding['repository']}/.github/workflows/"
    )
    workflow = identity.removeprefix(identity_prefix).removesuffix(
        f"@{record_binding['protected_ref']}"
    )
    if (
        signature["format"] != BUNDLE_MEDIA_TYPE
        or signature["certificate_oidc_issuer"]
        != "https://token.actions.githubusercontent.com"
        or signature["offline_verification_required"] is not True
        or record_binding
        != {
            "repository": "CosmaNeura/cosmaneura",
            "protected_ref": "refs/heads/master",
        }
        or not identity.startswith(identity_prefix)
        or not identity.endswith(f"@{record_binding['protected_ref']}")
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.ya?ml", workflow) is None
        or verifier["version"] != "2.97.0"
        or verifier["network_access_permitted"] is not False
        or verifier["maximum_runtime_seconds"] != VERIFIER_TIMEOUT_SECONDS
        or verifier["maximum_output_bytes"] != MAXIMUM_OUTPUT_BYTES
    ):
        _fail("runtime_signature.policy_invalid")
    return (
        "gh",
        "attestation",
        "verify",
        RUNTIME_RECORD_FILE,
        "--bundle",
        SIGNATURE_BUNDLE_FILE,
        "--cert-identity",
        identity,
        "--cert-oidc-issuer",
        signature["certificate_oidc_issuer"],
        "--custom-trusted-root",
        TRUSTED_ROOT_FILE,
        "--deny-self-hosted-runners",
        "--digest-alg",
        "sha256",
        "--format=json",
        "--hostname",
        "github.com",
        "--no-public-good",
        "--predicate-type",
        PREDICATE_TYPE,
        "--repo",
        record_binding["repository"],
        "--source-ref",
        record_binding["protected_ref"],
    )


def _invoke_verifier(
    *,
    verifier: attestation.OfflineAttestationVerifier,
    arguments: tuple[str, ...],
    record_bytes: bytes,
    bundle_bytes: bytes,
    root_bytes: bytes,
    document: Mapping[str, Any],
) -> bytes:
    files = MappingProxyType(
        {
            RUNTIME_RECORD_FILE: record_bytes,
            SIGNATURE_BUNDLE_FILE: bundle_bytes,
            TRUSTED_ROOT_FILE: root_bytes,
        }
    )
    try:
        response = verifier.verify(
            arguments=arguments,
            files=files,
            timeout_seconds=VERIFIER_TIMEOUT_SECONDS,
            maximum_output_bytes=MAXIMUM_OUTPUT_BYTES,
        )
    except Exception:
        raise RuntimeRecordSignatureFailure(
            "runtime_signature.verifier_unavailable"
        ) from None
    verifier_policy = document["offline_github_verifier"]
    if (
        type(response) is not attestation.AttestationVerifierResponse
        or type(response.returncode) is not int
        or response.returncode != 0
        or type(response.stdout) is not bytes
        or not 0 < len(response.stdout) <= MAXIMUM_OUTPUT_BYTES
        or type(response.stderr) is not bytes
        or response.stderr != b""
        or response.verifier_version != verifier_policy["version"]
        or response.verifier_sha256 != verifier_policy["executable_sha256"]
        or verifier_policy["maximum_runtime_seconds"] != VERIFIER_TIMEOUT_SECONDS
        or verifier_policy["maximum_output_bytes"] != MAXIMUM_OUTPUT_BYTES
        or verifier_policy["network_access_permitted"] is not False
    ):
        _fail("runtime_signature.verifier_invalid")
    return response.stdout


def _statement_from_output(
    raw: bytes,
    *,
    bundle: Mapping[str, Any],
    record_bytes: bytes,
    document: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any], list[Any]]:
    output = _load_json(raw, "runtime_signature.verifier_invalid")
    if not isinstance(output, list) or len(output) != 1:
        _fail("runtime_signature.verifier_invalid")
    entry = _exact_keys(
        output[0],
        {"attestation", "verificationResult"},
        "runtime_signature.verifier_invalid",
    )
    embedded = _exact_keys(
        entry["attestation"],
        {"bundle", "bundle_url", "initiator"},
        "runtime_signature.verifier_invalid",
    )
    if (
        embedded["bundle"] != bundle
        or embedded["bundle_url"] != ""
        or embedded["initiator"] != ""
    ):
        _fail("runtime_signature.verifier_invalid")
    verification = _exact_keys(
        entry["verificationResult"],
        {
            "mediaType",
            "signature",
            "verifiedTimestamps",
            "verifiedIdentity",
            "statement",
        },
        "runtime_signature.verifier_invalid",
    )
    identity = document["external_runtime_record"]["signature"]
    expected_identity = {
        "subjectAlternativeName": {
            "subjectAlternativeName": identity["certificate_identity"]
        },
        "issuer": {"issuer": "", "regexp": ".*"},
        "runnerEnvironment": "github-hosted",
    }
    if (
        verification["mediaType"] != VERIFICATION_MEDIA_TYPE
        or verification["verifiedIdentity"] != expected_identity
    ):
        _fail("runtime_signature.verifier_invalid")
    signature = _exact_keys(
        verification["signature"],
        {"certificate"},
        "runtime_signature.certificate_invalid",
    )
    summary = _mapping(
        signature["certificate"], "runtime_signature.certificate_invalid"
    )
    statement = _exact_keys(
        verification["statement"],
        {"_type", "subject", "predicateType", "predicate"},
        "runtime_signature.statement_invalid",
    )
    subjects = statement["subject"]
    if not isinstance(subjects, list) or len(subjects) != 1:
        _fail("runtime_signature.statement_invalid")
    subject = _exact_keys(
        subjects[0], {"name", "digest"}, "runtime_signature.statement_invalid"
    )
    subject_digest = _exact_keys(
        subject["digest"], {"sha256"}, "runtime_signature.statement_invalid"
    )
    predicate = _exact_keys(
        statement["predicate"],
        {"buildDefinition", "runDetails"},
        "runtime_signature.statement_invalid",
    )
    build_definition = _exact_keys(
        predicate["buildDefinition"],
        {
            "buildType",
            "externalParameters",
            "internalParameters",
            "resolvedDependencies",
        },
        "runtime_signature.statement_invalid",
    )
    run_details = _exact_keys(
        predicate["runDetails"],
        {"builder", "metadata", "byproducts"},
        "runtime_signature.statement_invalid",
    )
    if (
        statement["_type"] != STATEMENT_TYPE
        or statement["predicateType"] != PREDICATE_TYPE
        or subject["name"] != RUNTIME_RECORD_FILE
        or subject_digest["sha256"] != hashlib.sha256(record_bytes).hexdigest()
        or build_definition["buildType"] != BUILD_TYPE
        or not isinstance(build_definition["externalParameters"], Mapping)
        or not isinstance(build_definition["internalParameters"], Mapping)
        or not isinstance(build_definition["resolvedDependencies"], list)
        or not isinstance(run_details["builder"], Mapping)
        or not isinstance(run_details["metadata"], Mapping)
        or not isinstance(run_details["byproducts"], list)
    ):
        _fail("runtime_signature.statement_invalid")
    envelope = _mapping(bundle["dsseEnvelope"], "runtime_signature.bundle_invalid")
    payload = _load_json(
        _base64(envelope["payload"], "runtime_signature.bundle_invalid"),
        "runtime_signature.bundle_invalid",
    )
    if payload != statement:
        _fail("runtime_signature.statement_invalid")
    timestamps = verification["verifiedTimestamps"]
    if not isinstance(timestamps, list) or len(timestamps) != 1:
        _fail("runtime_signature.timestamp_invalid")
    return summary, statement, timestamps


def _der_utf8(raw: bytes, code: str) -> str:
    if type(raw) is not bytes or len(raw) < 2 or raw[0] != 12:
        _fail(code)
    first = raw[1]
    if first < 0x80:
        content_start, content_length = 2, first
    else:
        length_bytes = first & 0x7F
        if not 1 <= length_bytes <= 4 or len(raw) < 2 + length_bytes:
            _fail(code)
        encoded_length = raw[2 : 2 + length_bytes]
        if encoded_length[0] == 0:
            _fail(code)
        content_start = 2 + length_bytes
        content_length = int.from_bytes(encoded_length, "big")
        if content_length < 128:
            _fail(code)
    if content_start + content_length != len(raw):
        _fail(code)
    try:
        value = raw[content_start:].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise RuntimeRecordSignatureFailure(code) from None
    if not value or "\x00" in value:
        _fail(code)
    return value


def _certificate_claim(certificate: x509.Certificate, field: str) -> str:
    try:
        extension = certificate.extensions.get_extension_for_oid(
            ObjectIdentifier(CERTIFICATE_OIDS[field])
        )
    except (x509.ExtensionNotFound, x509.DuplicateExtension):
        raise RuntimeRecordSignatureFailure(
            "runtime_signature.certificate_invalid"
        ) from None
    if extension.critical or not isinstance(
        extension.value, x509.UnrecognizedExtension
    ):
        _fail("runtime_signature.certificate_invalid")
    return _der_utf8(extension.value.value, "runtime_signature.certificate_invalid")


def _validate_certificate(
    *,
    bundle: Mapping[str, Any],
    summary: Mapping[str, Any],
    document: Mapping[str, Any],
) -> tuple[datetime, datetime]:
    try:
        material = _mapping(
            bundle["verificationMaterial"], "runtime_signature.bundle_invalid"
        )
        encoded = _mapping(material["certificate"], "runtime_signature.bundle_invalid")[
            "rawBytes"
        ]
        certificate = x509.load_der_x509_certificate(
            _base64(encoded, "runtime_signature.certificate_invalid")
        )
        san_extension = certificate.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )
        if not isinstance(san_extension.value, x509.SubjectAlternativeName):
            _fail("runtime_signature.certificate_invalid")
        san_values = san_extension.value.get_values_for_type(
            x509.UniformResourceIdentifier
        )
        all_names = list(san_extension.value)
        issuer_organizations = certificate.issuer.get_attributes_for_oid(
            NameOID.ORGANIZATION_NAME
        )
        issuer_name = certificate.issuer.rfc4514_string()
    except RuntimeRecordSignatureFailure:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        x509.ExtensionNotFound,
        x509.DuplicateExtension,
    ):
        raise RuntimeRecordSignatureFailure(
            "runtime_signature.certificate_invalid"
        ) from None
    signature = document["external_runtime_record"]["signature"]
    binding = document["external_runtime_record"]["binding"]
    identity = signature["certificate_identity"]
    repository = binding["repository"]
    protected_ref = binding["protected_ref"]
    identity_prefix = f"https://github.com/{repository}/.github/workflows/"
    identity_suffix = f"@{protected_ref}"
    workflow = identity.removeprefix(identity_prefix).removesuffix(identity_suffix)
    if (
        not identity.startswith(identity_prefix)
        or not identity.endswith(identity_suffix)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.ya?ml", workflow) is None
        or len(certificate.subject) != 0
        or san_extension.critical is not True
        or len(all_names) != 1
        or san_values != [identity]
        or [item.value for item in issuer_organizations]
        != [CERTIFICATE_AUTHORITY_ORGANIZATION]
        or set(summary) - OPTIONAL_CERTIFICATE_FIELDS != REQUIRED_CERTIFICATE_FIELDS
        or summary.get("certificateIssuer") != issuer_name
        or summary.get("subjectAlternativeName") != identity
    ):
        _fail("runtime_signature.certificate_invalid")
    claims = {
        field: _certificate_claim(certificate, field) for field in CERTIFICATE_OIDS
    }
    source_digest = claims["sourceRepositoryDigest"]
    expected = {
        "issuer": signature["certificate_oidc_issuer"],
        "buildSignerURI": identity,
        "buildSignerDigest": source_digest,
        "runnerEnvironment": "github-hosted",
        "sourceRepositoryURI": f"https://github.com/{repository}",
        "sourceRepositoryDigest": source_digest,
        "sourceRepositoryRef": protected_ref,
        "sourceRepositoryIdentifier": REPOSITORY_IDENTIFIER,
        "sourceRepositoryOwnerURI": (
            f"https://github.com/{repository.partition('/')[0]}"
        ),
        "sourceRepositoryOwnerIdentifier": REPOSITORY_OWNER_IDENTIFIER,
        "buildConfigURI": identity,
        "buildConfigDigest": source_digest,
        "buildTrigger": "workflow_dispatch",
        "runInvocationURI": claims["runInvocationURI"],
        "sourceRepositoryVisibilityAtSigning": "private",
    }
    run_prefix = f"https://github.com/{repository}/actions/runs/"
    run_suffix = claims["runInvocationURI"].removeprefix(run_prefix)
    run_parts = run_suffix.split("/attempts/")
    if (
        claims != expected
        or any(summary.get(field) != value for field, value in expected.items())
        or SHA_RE.fullmatch(source_digest) is None
        or DECIMAL_RE.fullmatch(claims["sourceRepositoryIdentifier"]) is None
        or DECIMAL_RE.fullmatch(claims["sourceRepositoryOwnerIdentifier"]) is None
        or not claims["runInvocationURI"].startswith(run_prefix)
        or len(run_parts) != 2
        or any(DECIMAL_RE.fullmatch(value) is None for value in run_parts)
    ):
        _fail("runtime_signature.certificate_invalid")
    optional_expected = {
        "githubWorkflowTrigger": "workflow_dispatch",
        "githubWorkflowSHA": source_digest,
        "githubWorkflowRepository": repository,
        "githubWorkflowRef": protected_ref,
    }
    if any(
        field in summary and summary[field] != value
        for field, value in optional_expected.items()
    ) or (
        "githubWorkflowName" in summary
        and (
            type(summary["githubWorkflowName"]) is not str
            or not 1 <= len(summary["githubWorkflowName"]) <= 256
            or "\x00" in summary["githubWorkflowName"]
        )
    ):
        _fail("runtime_signature.certificate_invalid")
    return certificate.not_valid_before_utc, certificate.not_valid_after_utc


def _validate_timestamps(
    timestamps: list[Any],
    *,
    authority_uri: str,
    created_at: datetime,
    now: datetime,
    certificate_not_before: datetime,
    certificate_not_after: datetime,
) -> None:
    timestamp = _exact_keys(
        timestamps[0],
        {"type", "uri", "timestamp"},
        "runtime_signature.timestamp_invalid",
    )
    second, nanosecond = _timestamp(timestamp["timestamp"])
    if (
        timestamp["type"] != "TimestampAuthority"
        or timestamp["uri"] != authority_uri
        or second < created_at
        or second > now
        or (second == now and nanosecond > 0)
        or second < certificate_not_before
        or second >= certificate_not_after
    ):
        _fail("runtime_signature.timestamp_invalid")


def _authenticate_runtime_record_signature(
    policy: Any,
    *,
    provision_reference: runtime_binding.ExternalRuntimeBindingReference,
    cleanup_reference: runtime_binding.ExternalRuntimeBindingReference,
    record_bytes: bytes,
    signature_bundle_bytes: bytes,
    trusted_root_bytes: bytes,
    verifier: attestation.OfflineAttestationVerifier,
    clock: Callable[[], datetime] = _utc_now,
) -> runtime_binding.VerifiedExternalRuntimeRecordSignature:
    """Verify exact offline Sigstore material and mint one sealed proof."""

    try:
        document = runtime_binding.validate_runtime_binding_policy(
            policy, executable=True
        )
    except Exception:
        raise RuntimeRecordSignatureFailure(
            "runtime_signature.policy_invalid"
        ) from None
    pointer = _validate_references(document, provision_reference, cleanup_reference)
    record, created_at = _validate_inputs(
        document,
        pointer,
        record_bytes,
        signature_bundle_bytes,
        trusted_root_bytes,
    )
    now = _trusted_now(clock)
    if now < created_at:
        _fail("runtime_signature.clock_invalid")
    authority_uri = _validate_root(trusted_root_bytes)
    bundle = _validate_bundle(
        signature_bundle_bytes,
        document["external_runtime_record"]["signature"]["format"],
    )
    output = _invoke_verifier(
        verifier=verifier,
        arguments=_arguments(document),
        record_bytes=record_bytes,
        bundle_bytes=signature_bundle_bytes,
        root_bytes=trusted_root_bytes,
        document=document,
    )
    summary, _statement, timestamps = _statement_from_output(
        output,
        bundle=bundle,
        record_bytes=record_bytes,
        document=document,
    )
    certificate_not_before, certificate_not_after = _validate_certificate(
        bundle=bundle,
        summary=summary,
        document=document,
    )
    _validate_timestamps(
        timestamps,
        authority_uri=authority_uri,
        created_at=created_at,
        now=now,
        certificate_not_before=certificate_not_before,
        certificate_not_after=certificate_not_after,
    )
    signature = document["external_runtime_record"]["signature"]
    try:
        proof = runtime_binding.VerifiedExternalRuntimeRecordSignature._mint_from_reviewed_verifier(
            record_sha256=_digest(record_bytes),
            bundle_sha256=_digest(signature_bundle_bytes),
            trusted_root_sha256=_digest(trusted_root_bytes),
            certificate_oidc_issuer=signature["certificate_oidc_issuer"],
            certificate_identity=signature["certificate_identity"],
        )
        runtime_binding.validate_external_runtime_binding(
            document,
            record_bytes,
            provision_reference=provision_reference,
            cleanup_reference=cleanup_reference,
            signature_verification=proof,
        )
    except Exception:
        raise RuntimeRecordSignatureFailure(
            "runtime_signature.binding_invalid"
        ) from None
    if record.get("created_at") != created_at.strftime("%Y-%m-%dT%H:%M:%SZ"):
        _fail("runtime_signature.binding_invalid")
    return proof


def authenticate_runtime_record_signature(
    policy: Any,
    *,
    provision_reference: runtime_binding.ExternalRuntimeBindingReference,
    cleanup_reference: runtime_binding.ExternalRuntimeBindingReference,
    record_bytes: bytes,
    signature_bundle_bytes: bytes,
    trusted_root_bytes: bytes,
    verifier: attestation.OfflineAttestationVerifier,
    clock: Callable[[], datetime] = _utc_now,
) -> runtime_binding.VerifiedExternalRuntimeRecordSignature:
    """Verify exact offline Sigstore material and mint one sealed proof."""

    try:
        return _authenticate_runtime_record_signature(
            policy,
            provision_reference=provision_reference,
            cleanup_reference=cleanup_reference,
            record_bytes=record_bytes,
            signature_bundle_bytes=signature_bundle_bytes,
            trusted_root_bytes=trusted_root_bytes,
            verifier=verifier,
            clock=clock,
        )
    except RuntimeRecordSignatureFailure:
        raise
    except Exception:
        raise RuntimeRecordSignatureFailure(
            "runtime_signature.authentication_invalid"
        ) from None
