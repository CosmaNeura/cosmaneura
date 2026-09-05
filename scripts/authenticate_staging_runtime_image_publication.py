#!/usr/bin/env python3
"""Authenticate one immutable runtime image from offline, exact-byte evidence.

The module has no registry, credential, subprocess, network, workflow, or Azure
implementation.  Callers must inject one-shot registry and verifier adapters.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import threading
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, NoReturn, Protocol

from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID, ObjectIdentifier

try:
    from scripts import authenticate_staging_github_attestation as attestation
    from scripts import (
        authenticate_staging_runtime_record_signature as runtime_signature,
    )
    from scripts import build_staging_runtime_record as record_builder
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import authenticate_staging_github_attestation as attestation
    import authenticate_staging_runtime_record_signature as runtime_signature
    import build_staging_runtime_record as record_builder
    import validate_staging_restore_runtime_binding as runtime_binding


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DECIMAL_RE = re.compile(r"^[1-9][0-9]{0,19}$")
TIMESTAMP_RE = re.compile(
    r"^(20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
    r"(?:\.([0-9]{1,9}))?Z$"
)
RECEIPT_FIELDS = tuple(
    "schema_version policy_id policy_binding_sha256 registry_host image_repository "
    "image_digest manifest_media_type manifest_size config_digest config_size image_os "
    "image_architecture image_build_source_sha build_run_id build_run_attempt "
    "certificate_identity certificate_run_invocation_uri attestation_bundle_sha256 "
    "trusted_root_sha256 statement_sha256 certificate_sha256 verifier_version "
    "verifier_sha256 verified_timestamp published_at image_build_provenance_proven pass".split()
)


class RuntimeImagePublicationFailure(RuntimeError):
    """Bounded refusal safe to report without registry or provenance bytes."""


def _fail(code: str) -> NoReturn:
    raise RuntimeImagePublicationFailure(code)


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


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
        raise RuntimeImagePublicationFailure(
            "runtime_image.canonical_invalid"
        ) from None


def _load_json(raw: bytes, code: str, *, maximum_bytes: int) -> Any:
    if type(raw) is not bytes or not 0 < len(raw) <= maximum_bytes:
        _fail(code)

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                _fail(code)
            result[key] = value
        return result

    def nonfinite(_value: str) -> NoReturn:
        _fail(code)

    def integer(value: str) -> int:
        if len(value.removeprefix("-")) > 20:
            _fail(code)
        return int(value)

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=nonfinite,
            parse_float=nonfinite,
            parse_int=integer,
        )
    except RuntimeImagePublicationFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise RuntimeImagePublicationFailure(code) from None
    nodes = 0

    def bounded(item: Any, depth: int = 0) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 64 or nodes > 100_000:
            _fail(code)
        if isinstance(item, str) and len(item) > maximum_bytes:
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
        raise RuntimeImagePublicationFailure(code) from None
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
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise RuntimeImagePublicationFailure(code) from None
    if not raw:
        _fail(code)
    return raw


def _trusted_now(clock: Callable[[], datetime]) -> tuple[datetime, str]:
    try:
        value = clock()
        valid = (
            type(value) is datetime
            and value.tzinfo is not None
            and value.utcoffset() == timedelta(0)
            and value.microsecond == 0
        )
    except Exception:
        raise RuntimeImagePublicationFailure("runtime_image.clock_invalid") from None
    if not valid:
        _fail("runtime_image.clock_invalid")
    normalized = value.astimezone(UTC)
    return normalized, normalized.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class RegistryManifestRequest:
    registry_host: str
    repository: str
    digest: str
    accept: str
    maximum_bytes: int


@dataclass(frozen=True, slots=True)
class RegistryConfigRequest:
    registry_host: str
    repository: str
    manifest_digest: str
    digest: str
    expected_size: int
    maximum_bytes: int


@dataclass(frozen=True, slots=True)
class RegistryManifestResponse:
    registry_host: str
    repository: str
    requested_digest: str
    status_code: int
    content_type: str
    docker_content_digest: str
    body: bytes = dataclass_field(repr=False)


@dataclass(frozen=True, slots=True)
class RegistryConfigResponse:
    registry_host: str
    repository: str
    requested_digest: str
    status_code: int
    body: bytes = dataclass_field(repr=False)


class ExactRegistryImageTransport(Protocol):
    def get_manifest(
        self, request: RegistryManifestRequest
    ) -> RegistryManifestResponse: ...

    def get_config(self, request: RegistryConfigRequest) -> RegistryConfigResponse: ...


class OneShotExactRegistryImageReader:
    """One manifest GET followed by one descriptor-derived config GET."""

    __slots__ = (
        "_config_request",
        "_lock",
        "_manifest_request",
        "_stage",
        "_transport",
    )

    def __init__(self, transport: ExactRegistryImageTransport) -> None:
        if not callable(getattr(transport, "get_manifest", None)) or not callable(
            getattr(transport, "get_config", None)
        ):
            _fail("runtime_image.reader_invalid")
        self._transport = transport
        self._lock = threading.Lock()
        self._stage = 0
        self._manifest_request: RegistryManifestRequest | None = None
        self._config_request: RegistryConfigRequest | None = None

    def read_manifest(
        self, request: RegistryManifestRequest
    ) -> RegistryManifestResponse:
        with self._lock:
            if self._stage != 0 or type(request) is not RegistryManifestRequest:
                _fail("runtime_image.reader_already_used")
            self._stage = 1
            self._manifest_request = request
        try:
            return self._transport.get_manifest(request)
        except RuntimeImagePublicationFailure:
            raise
        except Exception:
            raise RuntimeImagePublicationFailure(
                "runtime_image.registry_unavailable"
            ) from None

    def read_config(self, request: RegistryConfigRequest) -> RegistryConfigResponse:
        with self._lock:
            manifest = self._manifest_request
            if (
                self._stage != 1
                or manifest is None
                or type(request) is not RegistryConfigRequest
                or request.registry_host != manifest.registry_host
                or request.repository != manifest.repository
                or request.manifest_digest != manifest.digest
            ):
                _fail("runtime_image.reader_already_used")
            self._stage = 2
            self._config_request = request
        try:
            return self._transport.get_config(request)
        except RuntimeImagePublicationFailure:
            raise
        except Exception:
            raise RuntimeImagePublicationFailure(
                "runtime_image.registry_unavailable"
            ) from None

    def _completed(
        self,
        manifest: RegistryManifestRequest,
        config: RegistryConfigRequest,
    ) -> bool:
        with self._lock:
            return (
                type(self) is OneShotExactRegistryImageReader
                and self._stage == 2
                and self._manifest_request == manifest
                and self._config_request == config
            )


class OneShotOfflineAttestationVerifier:
    """Single-use wrapper around an injected offline verifier implementation."""

    __slots__ = ("_delegate", "_lock", "_used")

    def __init__(self, delegate: attestation.OfflineAttestationVerifier) -> None:
        if not callable(getattr(delegate, "verify", None)):
            _fail("runtime_image.verifier_invalid")
        self._delegate = delegate
        self._lock = threading.Lock()
        self._used = False

    def verify(
        self,
        *,
        arguments: tuple[str, ...],
        files: Mapping[str, bytes],
        timeout_seconds: int,
        maximum_output_bytes: int,
    ) -> attestation.AttestationVerifierResponse:
        with self._lock:
            if self._used:
                _fail("runtime_image.verifier_already_used")
            self._used = True
        return self._delegate.verify(
            arguments=arguments,
            files=files,
            timeout_seconds=timeout_seconds,
            maximum_output_bytes=maximum_output_bytes,
        )

    def _used_once(self) -> bool:
        with self._lock:
            return type(self) is OneShotOfflineAttestationVerifier and self._used


_AUTHENTICATED_IMAGE_EVIDENCE_SEAL = object()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class AuthenticatedRuntimeImagePublicationEvidence:
    """Sealed publication authority plus canonical audit receipt."""

    _publication: record_builder.AuthenticatedRuntimeImagePublication = dataclass_field(
        repr=False
    )
    _receipt_bytes: bytes = dataclass_field(repr=False)
    _receipt_binding: tuple[object, ...] = dataclass_field(repr=False, compare=False)
    _receipt_sha256: str = dataclass_field(repr=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("runtime_image.output_invalid")

    def _is_authenticated(self) -> bool:
        try:
            receipt = _exact_keys(
                _load_json(
                    self._receipt_bytes,
                    "runtime_image.output_invalid",
                    maximum_bytes=65_536,
                ),
                RECEIPT_FIELDS,
                "runtime_image.output_invalid",
            )
            publication = self._publication
            return (
                type(self) is AuthenticatedRuntimeImagePublicationEvidence
                and getattr(self, "_seal", None) is _AUTHENTICATED_IMAGE_EVIDENCE_SEAL
                and type(self._receipt_bytes) is bytes
                and type(self._receipt_binding) is tuple
                and all(
                    type(actual) is type(expected) and actual == expected
                    for actual, expected in zip(
                        (receipt[field] for field in RECEIPT_FIELDS),
                        self._receipt_binding,
                        strict=True,
                    )
                )
                and _canonical(receipt) == self._receipt_bytes
                and _digest(self._receipt_bytes) == self._receipt_sha256
                and type(publication)
                is record_builder.AuthenticatedRuntimeImagePublication
                and publication._is_authenticated()
                and receipt["image_repository"] == publication.image_repository
                and receipt["image_digest"] == publication.image_digest
                and receipt["image_build_source_sha"]
                == publication.image_build_source_sha
                and receipt["published_at"] == publication.published_at
                and receipt["image_build_provenance_proven"] is False
                and receipt["pass"] is True
            )
        except Exception:
            return False

    @property
    def publication(self) -> record_builder.AuthenticatedRuntimeImagePublication:
        if not self._is_authenticated():
            _fail("runtime_image.output_invalid")
        return self._publication

    @property
    def receipt_bytes(self) -> bytes:
        if not self._is_authenticated():
            _fail("runtime_image.output_invalid")
        return self._receipt_bytes

    @property
    def receipt_sha256(self) -> str:
        if not self._is_authenticated():
            _fail("runtime_image.output_invalid")
        return self._receipt_sha256

    @property
    def receipt(self) -> dict[str, Any]:
        if not self._is_authenticated():
            _fail("runtime_image.output_invalid")
        return dict(
            _mapping(
                _load_json(
                    self._receipt_bytes,
                    "runtime_image.output_invalid",
                    maximum_bytes=65_536,
                ),
                "runtime_image.output_invalid",
            )
        )


@dataclass(frozen=True, slots=True)
class _CertificateAuthority:
    raw: bytes = dataclass_field(repr=False)
    issuer_name: str
    identity: str
    source_sha: str
    run_id: str
    run_attempt: str
    run_invocation_uri: str
    not_before: datetime
    not_after: datetime
    claims: tuple[tuple[str, str], ...] = dataclass_field(repr=False)


def _policy_snapshot(
    authority: runtime_binding.ValidatedRuntimeRecordBuildPolicy,
) -> tuple[dict[str, Any], bytes, str]:
    try:
        if (
            type(authority) is not runtime_binding.ValidatedRuntimeRecordBuildPolicy
            or not authority._is_validated()
        ):
            _fail("runtime_image.policy_invalid")
        policy_bytes = bytes(authority.policy_bytes)
        policy_binding_sha256 = authority.policy_binding_sha256
        document = authority._document()
        runtime_binding._runtime_image_publication_authentication(document)
        image_authentication = document["private_registry"][
            "image_publication_authentication"
        ]
        if (
            runtime_binding._canonical(document) != policy_bytes
            or runtime_binding._policy_binding_digest(document) != policy_binding_sha256
            or image_authentication["verifier_profile"]["publisher_containment_proven"]
            is not False
            or image_authentication["publication"]["image_build_provenance_proven"]
            is not False
        ):
            _fail("runtime_image.policy_invalid")
        return document, policy_bytes, policy_binding_sha256
    except RuntimeImagePublicationFailure:
        raise
    except Exception:
        raise RuntimeImagePublicationFailure("runtime_image.policy_invalid") from None


def _policy_unchanged(
    authority: runtime_binding.ValidatedRuntimeRecordBuildPolicy,
    policy_bytes: bytes,
    policy_binding_sha256: str,
) -> bool:
    try:
        return (
            type(authority) is runtime_binding.ValidatedRuntimeRecordBuildPolicy
            and authority._is_validated()
            and authority.policy_bytes == policy_bytes
            and authority.policy_binding_sha256 == policy_binding_sha256
            and runtime_binding._canonical(authority._document()) == policy_bytes
        )
    except Exception:
        return False


def _validate_root_and_bundle(
    document: Mapping[str, Any], bundle_bytes: bytes, root_bytes: bytes
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    authentication = document["private_registry"]["image_publication_authentication"]
    root_policy = document["evidence_ingress"]["private_versioned_secret_source"][
        "secrets"
    ]["github_trusted_root"]
    if (
        type(bundle_bytes) is not bytes
        or not 0 < len(bundle_bytes) <= authentication["bundle"]["maximum_bytes"]
        or type(root_bytes) is not bytes
        or not root_policy["minimum_bytes"]
        <= len(root_bytes)
        <= root_policy["maximum_bytes"]
        or _digest(root_bytes) != root_policy["sha256"]
    ):
        _fail("runtime_image.input_invalid")
    try:
        runtime_signature._validate_root(root_bytes)
        bundle = runtime_signature._validate_bundle(
            bundle_bytes, authentication["bundle"]["media_type"]
        )
        envelope = _mapping(bundle["dsseEnvelope"], "runtime_image.bundle_invalid")
        statement = _mapping(
            _load_json(
                _base64(envelope["payload"], "runtime_image.bundle_invalid"),
                "runtime_image.statement_invalid",
                maximum_bytes=authentication["bundle"]["maximum_bytes"],
            ),
            "runtime_image.statement_invalid",
        )
        return bundle, statement
    except RuntimeImagePublicationFailure:
        raise
    except Exception:
        raise RuntimeImagePublicationFailure("runtime_image.bundle_invalid") from None


def _der_utf8(raw: bytes) -> str:
    if type(raw) is not bytes or len(raw) < 2 or raw[0] != 12:
        _fail("runtime_image.certificate_invalid")
    first = raw[1]
    if first < 0x80:
        start, length = 2, first
    else:
        length_bytes = first & 0x7F
        if not 1 <= length_bytes <= 4 or len(raw) < 2 + length_bytes:
            _fail("runtime_image.certificate_invalid")
        encoded = raw[2 : 2 + length_bytes]
        if encoded[0] == 0:
            _fail("runtime_image.certificate_invalid")
        start, length = 2 + length_bytes, int.from_bytes(encoded, "big")
        if length < 128:
            _fail("runtime_image.certificate_invalid")
    if start + length != len(raw):
        _fail("runtime_image.certificate_invalid")
    try:
        value = raw[start:].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise RuntimeImagePublicationFailure(
            "runtime_image.certificate_invalid"
        ) from None
    if not value or "\x00" in value:
        _fail("runtime_image.certificate_invalid")
    return value


def _certificate_authority(
    bundle: Mapping[str, Any], document: Mapping[str, Any]
) -> _CertificateAuthority:
    try:
        material = _mapping(
            bundle["verificationMaterial"], "runtime_image.bundle_invalid"
        )
        raw = _base64(
            _mapping(material["certificate"], "runtime_image.certificate_invalid")[
                "rawBytes"
            ],
            "runtime_image.certificate_invalid",
        )
        certificate = x509.load_der_x509_certificate(raw)
        san = certificate.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )
        if not isinstance(san.value, x509.SubjectAlternativeName):
            _fail("runtime_image.certificate_invalid")
        uris = san.value.get_values_for_type(x509.UniformResourceIdentifier)
        all_names = list(san.value)
        issuer_orgs = certificate.issuer.get_attributes_for_oid(
            NameOID.ORGANIZATION_NAME
        )
        claims: dict[str, str] = {}
        for field, oid in runtime_signature.CERTIFICATE_OIDS.items():
            extension = certificate.extensions.get_extension_for_oid(
                ObjectIdentifier(oid)
            )
            if extension.critical or not isinstance(
                extension.value, x509.UnrecognizedExtension
            ):
                _fail("runtime_image.certificate_invalid")
            claims[field] = _der_utf8(extension.value.value)
    except RuntimeImagePublicationFailure:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        x509.ExtensionNotFound,
        x509.DuplicateExtension,
    ):
        raise RuntimeImagePublicationFailure(
            "runtime_image.certificate_invalid"
        ) from None
    authentication = document["private_registry"]["image_publication_authentication"]
    policy = authentication["certificate"]
    repository = document["external_runtime_record"]["binding"]["repository"]
    protected_ref = document["external_runtime_record"]["binding"]["protected_ref"]
    identity = policy["identity"]
    source_sha = claims.get("sourceRepositoryDigest", "")
    run_uri = claims.get("runInvocationURI", "")
    run_prefix = f"https://github.com/{repository}/actions/runs/"
    run_parts = run_uri.removeprefix(run_prefix).split("/attempts/")
    expected = {
        "issuer": policy["oidc_issuer"],
        "buildSignerURI": identity,
        "buildSignerDigest": source_sha,
        "runnerEnvironment": policy["runner_environment"],
        "sourceRepositoryURI": authentication["image"]["source_repository_uri"],
        "sourceRepositoryDigest": source_sha,
        "sourceRepositoryRef": protected_ref,
        "sourceRepositoryIdentifier": policy["source_repository_identifier"],
        "sourceRepositoryOwnerURI": f"https://github.com/{repository.partition('/')[0]}",
        "sourceRepositoryOwnerIdentifier": policy["source_repository_owner_identifier"],
        "buildConfigURI": identity,
        "buildConfigDigest": source_sha,
        "buildTrigger": policy["workflow_trigger"],
        "runInvocationURI": run_uri,
        "sourceRepositoryVisibilityAtSigning": policy["source_repository_visibility"],
    }
    if (
        len(certificate.subject) != 0
        or san.critical is not True
        or len(all_names) != 1
        or uris != [identity]
        or [item.value for item in issuer_orgs]
        != [runtime_signature.CERTIFICATE_AUTHORITY_ORGANIZATION]
        or claims != expected
        or SHA_RE.fullmatch(source_sha) is None
        or not run_uri.startswith(run_prefix)
        or len(run_parts) != 2
        or any(DECIMAL_RE.fullmatch(item) is None for item in run_parts)
    ):
        _fail("runtime_image.certificate_invalid")
    return _CertificateAuthority(
        raw=raw,
        issuer_name=certificate.issuer.rfc4514_string(),
        identity=identity,
        source_sha=source_sha,
        run_id=run_parts[0],
        run_attempt=run_parts[1],
        run_invocation_uri=run_uri,
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
        claims=tuple(sorted(claims.items())),
    )


def _validate_statement(
    statement: Mapping[str, Any], document: Mapping[str, Any]
) -> str:
    contract = document["private_registry"]["image_publication_authentication"][
        "statement"
    ]
    statement = _exact_keys(
        statement,
        {"_type", "subject", "predicateType", "predicate"},
        "runtime_image.statement_invalid",
    )
    subjects = statement["subject"]
    if not isinstance(subjects, list) or len(subjects) != 1:
        _fail("runtime_image.statement_invalid")
    subject = _exact_keys(
        subjects[0], {"name", "digest"}, "runtime_image.statement_invalid"
    )
    digest = _exact_keys(
        subject["digest"], {"sha256"}, "runtime_image.statement_invalid"
    )
    predicate = _exact_keys(
        statement["predicate"],
        {"buildDefinition", "runDetails"},
        "runtime_image.statement_invalid",
    )
    build_definition = _exact_keys(
        predicate["buildDefinition"],
        {
            "buildType",
            "externalParameters",
            "internalParameters",
            "resolvedDependencies",
        },
        "runtime_image.statement_invalid",
    )
    run_details = _exact_keys(
        predicate["runDetails"],
        {"builder", "metadata", "byproducts"},
        "runtime_image.statement_invalid",
    )
    if (
        statement["_type"] != contract["type"]
        or statement["predicateType"] != contract["predicate_type"]
        or subject["name"] != contract["subject_name"]
        or type(digest["sha256"]) is not str
        or HEX_RE.fullmatch(digest["sha256"]) is None
        or build_definition["buildType"] != contract["build_type"]
        or not isinstance(build_definition["externalParameters"], Mapping)
        or not isinstance(build_definition["internalParameters"], Mapping)
        or not isinstance(build_definition["resolvedDependencies"], list)
        or not isinstance(run_details["builder"], Mapping)
        or not isinstance(run_details["metadata"], Mapping)
        or not isinstance(run_details["byproducts"], list)
    ):
        _fail("runtime_image.statement_invalid")
    return f"sha256:{digest['sha256']}"


def _validate_manifest_response(
    response: Any,
    request: RegistryManifestRequest,
    document: Mapping[str, Any],
) -> tuple[bytes, str, int]:
    if type(response) is not RegistryManifestResponse:
        _fail("runtime_image.manifest_invalid")
    body = response.body
    if (
        response.registry_host != request.registry_host
        or response.repository != request.repository
        or response.requested_digest != request.digest
        or response.status_code != 200
        or response.content_type != request.accept
        or response.docker_content_digest != request.digest
        or type(body) is not bytes
        or not 0 < len(body) <= request.maximum_bytes
        or _digest(body) != request.digest
    ):
        _fail("runtime_image.manifest_invalid")
    manifest = _mapping(
        _load_json(
            body, "runtime_image.manifest_invalid", maximum_bytes=request.maximum_bytes
        ),
        "runtime_image.manifest_invalid",
    )
    if not {"schemaVersion", "mediaType", "config", "layers"} <= set(
        manifest
    ) or not set(manifest) <= {
        "schemaVersion",
        "mediaType",
        "config",
        "layers",
        "annotations",
    }:
        _fail("runtime_image.manifest_invalid")
    config = _mapping(manifest["config"], "runtime_image.manifest_invalid")
    if not {"mediaType", "digest", "size"} <= set(config) or not set(config) <= {
        "mediaType",
        "digest",
        "size",
        "annotations",
    }:
        _fail("runtime_image.manifest_invalid")
    readback = document["private_registry"]["image_publication_authentication"][
        "registry_readback"
    ]
    if (
        type(manifest["schemaVersion"]) is not int
        or manifest["schemaVersion"] != 2
        or manifest["mediaType"] != readback["manifest_media_type"]
        or not isinstance(manifest["layers"], list)
        or config.get("mediaType") != readback["config_media_type"]
        or type(config.get("digest")) is not str
        or DIGEST_RE.fullmatch(config["digest"]) is None
        or type(config.get("size")) is not int
        or isinstance(config["size"], bool)
        or not 0 < config["size"] <= readback["maximum_config_bytes"]
    ):
        _fail("runtime_image.manifest_invalid")
    return bytes(body), config["digest"], config["size"]


def _validate_config_response(
    response: Any,
    request: RegistryConfigRequest,
    document: Mapping[str, Any],
    certificate: _CertificateAuthority,
) -> tuple[bytes, str]:
    if type(response) is not RegistryConfigResponse:
        _fail("runtime_image.config_invalid")
    body = response.body
    if (
        response.registry_host != request.registry_host
        or response.repository != request.repository
        or response.requested_digest != request.digest
        or response.status_code != 200
        or type(body) is not bytes
        or len(body) != request.expected_size
        or not 0 < len(body) <= request.maximum_bytes
        or _digest(body) != request.digest
    ):
        _fail("runtime_image.config_invalid")
    config = _mapping(
        _load_json(
            body, "runtime_image.config_invalid", maximum_bytes=request.maximum_bytes
        ),
        "runtime_image.config_invalid",
    )
    image_policy = document["private_registry"]["image_publication_authentication"][
        "image"
    ]
    runtime_config = _mapping(config.get("config"), "runtime_image.config_invalid")
    labels = _mapping(runtime_config.get("Labels"), "runtime_image.config_invalid")
    source_sha = labels.get(image_policy["revision_label"])
    if (
        config.get("os") != image_policy["os"]
        or config.get("architecture") != image_policy["architecture"]
        or labels.get(image_policy["source_label"])
        != image_policy["source_repository_uri"]
        or type(source_sha) is not str
        or SHA_RE.fullmatch(source_sha) is None
        or source_sha != certificate.source_sha
        or labels.get(image_policy["run_id_label"]) != certificate.run_id
        or labels.get(image_policy["run_attempt_label"]) != certificate.run_attempt
    ):
        _fail("runtime_image.config_invalid")
    return bytes(body), str(source_sha)


def _arguments(
    document: Mapping[str, Any], certificate: _CertificateAuthority
) -> tuple[str, ...]:
    authentication = document["private_registry"]["image_publication_authentication"]
    profile = authentication["verifier_profile"]
    binding = document["external_runtime_record"]["binding"]
    return (
        profile["command"],
        "attestation",
        "verify",
        profile["subject_filename"],
        "--bundle",
        profile["bundle_filename"],
        "--cert-identity",
        certificate.identity,
        "--cert-oidc-issuer",
        authentication["certificate"]["oidc_issuer"],
        "--custom-trusted-root",
        profile["trusted_root_filename"],
        "--deny-self-hosted-runners",
        "--digest-alg",
        "sha256",
        "--format=json",
        "--hostname",
        "github.com",
        "--no-public-good",
        "--predicate-type",
        authentication["statement"]["predicate_type"],
        "--repo",
        binding["repository"],
        "--signer-digest",
        certificate.source_sha,
        "--source-digest",
        certificate.source_sha,
        "--source-ref",
        binding["protected_ref"],
    )


def _invoke_verifier(
    *,
    verifier: OneShotOfflineAttestationVerifier,
    arguments: tuple[str, ...],
    manifest_bytes: bytes,
    bundle_bytes: bytes,
    root_bytes: bytes,
    document: Mapping[str, Any],
) -> bytes:
    authentication = document["private_registry"]["image_publication_authentication"]
    profile = authentication["verifier_profile"]
    files = MappingProxyType(
        {
            profile["subject_filename"]: manifest_bytes,
            profile["bundle_filename"]: bundle_bytes,
            profile["trusted_root_filename"]: root_bytes,
        }
    )
    try:
        response = verifier.verify(
            arguments=arguments,
            files=files,
            timeout_seconds=profile["timeout_seconds"],
            maximum_output_bytes=profile["maximum_output_bytes"],
        )
    except RuntimeImagePublicationFailure:
        raise
    except Exception:
        raise RuntimeImagePublicationFailure(
            "runtime_image.verifier_unavailable"
        ) from None
    verifier_policy = document["offline_github_verifier"]
    if (
        type(response) is not attestation.AttestationVerifierResponse
        or type(response.returncode) is not int
        or response.returncode != 0
        or type(response.stdout) is not bytes
        or not 0 < len(response.stdout) <= profile["maximum_output_bytes"]
        or type(response.stderr) is not bytes
        or response.stderr != b""
        or response.verifier_version != profile["version"]
        or response.verifier_version != verifier_policy["version"]
        or response.verifier_sha256 != verifier_policy["executable_sha256"]
    ):
        _fail("runtime_image.verifier_invalid")
    return bytes(response.stdout)


def _timestamp(value: Any) -> tuple[datetime, int]:
    if type(value) is not str or (match := TIMESTAMP_RE.fullmatch(value)) is None:
        _fail("runtime_image.timestamp_invalid")
    try:
        second = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=UTC
        )
    except ValueError:
        raise RuntimeImagePublicationFailure(
            "runtime_image.timestamp_invalid"
        ) from None
    return second, int((match.group(2) or "0").ljust(9, "0"))


def _validate_verifier_output(
    raw: bytes,
    *,
    bundle: Mapping[str, Any],
    statement: Mapping[str, Any],
    certificate: _CertificateAuthority,
    now: datetime,
    document: Mapping[str, Any],
) -> str:
    authentication = document["private_registry"]["image_publication_authentication"]
    output = _load_json(
        raw,
        "runtime_image.verifier_invalid",
        maximum_bytes=authentication["verifier_profile"]["maximum_output_bytes"],
    )
    if not isinstance(output, list) or len(output) != 1:
        _fail("runtime_image.verifier_invalid")
    entry = _exact_keys(
        output[0],
        {"attestation", "verificationResult"},
        "runtime_image.verifier_invalid",
    )
    embedded = _exact_keys(
        entry["attestation"],
        {"bundle", "bundle_url", "initiator"},
        "runtime_image.verifier_invalid",
    )
    verification = _exact_keys(
        entry["verificationResult"],
        {
            "mediaType",
            "signature",
            "verifiedTimestamps",
            "verifiedIdentity",
            "statement",
        },
        "runtime_image.verifier_invalid",
    )
    expected_identity = {
        "subjectAlternativeName": {"subjectAlternativeName": certificate.identity},
        "issuer": {"issuer": "", "regexp": ".*"},
        "runnerEnvironment": authentication["certificate"]["runner_environment"],
    }
    if (
        embedded["bundle"] != bundle
        or embedded["bundle_url"] != ""
        or embedded["initiator"] != ""
        or verification["mediaType"] != runtime_signature.VERIFICATION_MEDIA_TYPE
        or verification["verifiedIdentity"] != expected_identity
        or verification["statement"] != statement
    ):
        _fail("runtime_image.verifier_invalid")
    summary = _mapping(
        _exact_keys(
            verification["signature"],
            {"certificate"},
            "runtime_image.certificate_invalid",
        )["certificate"],
        "runtime_image.certificate_invalid",
    )
    claims = dict(certificate.claims)
    if (
        set(summary) - runtime_signature.OPTIONAL_CERTIFICATE_FIELDS
        != runtime_signature.REQUIRED_CERTIFICATE_FIELDS
        or summary.get("certificateIssuer") != certificate.issuer_name
        or summary.get("subjectAlternativeName") != certificate.identity
        or any(summary.get(field) != value for field, value in claims.items())
    ):
        _fail("runtime_image.certificate_invalid")
    optional = {
        "githubWorkflowTrigger": authentication["certificate"]["workflow_trigger"],
        "githubWorkflowSHA": certificate.source_sha,
        "githubWorkflowRepository": document["external_runtime_record"]["binding"][
            "repository"
        ],
        "githubWorkflowRef": document["external_runtime_record"]["binding"][
            "protected_ref"
        ],
    }
    if any(
        field in summary and summary[field] != value
        for field, value in optional.items()
    ) or (
        "githubWorkflowName" in summary
        and (
            type(summary["githubWorkflowName"]) is not str
            or not 1 <= len(summary["githubWorkflowName"]) <= 256
            or "\x00" in summary["githubWorkflowName"]
        )
    ):
        _fail("runtime_image.certificate_invalid")
    timestamps = verification["verifiedTimestamps"]
    if not isinstance(timestamps, list) or len(timestamps) != 1:
        _fail("runtime_image.timestamp_invalid")
    timestamp = _exact_keys(
        timestamps[0], {"type", "uri", "timestamp"}, "runtime_image.timestamp_invalid"
    )
    second, nanosecond = _timestamp(timestamp["timestamp"])
    if (
        timestamp["type"] != "TimestampAuthority"
        or timestamp["uri"] != runtime_signature.TIMESTAMP_AUTHORITY_URI
        or second > now
        or (second == now and nanosecond > 0)
        or second < certificate.not_before
        or second >= certificate.not_after
    ):
        _fail("runtime_image.timestamp_invalid")
    return timestamp["timestamp"]


def authenticate_runtime_image_publication(
    build_policy: runtime_binding.ValidatedRuntimeRecordBuildPolicy,
    *,
    provenance_bundle_bytes: bytes,
    trusted_root_bytes: bytes,
    registry_reader: OneShotExactRegistryImageReader,
    verifier: OneShotOfflineAttestationVerifier,
    clock: Callable[[], datetime],
) -> AuthenticatedRuntimeImagePublicationEvidence:
    """Authenticate exact registry bytes and mint a dormant publication seal."""

    document, policy_bytes, policy_binding_sha256 = _policy_snapshot(build_policy)
    if (
        type(registry_reader) is not OneShotExactRegistryImageReader
        or type(verifier) is not OneShotOfflineAttestationVerifier
    ):
        _fail("runtime_image.adapter_invalid")
    bundle_bytes = (
        bytes(provenance_bundle_bytes)
        if type(provenance_bundle_bytes) is bytes
        else b""
    )
    root_bytes = bytes(trusted_root_bytes) if type(trusted_root_bytes) is bytes else b""
    bundle, statement = _validate_root_and_bundle(document, bundle_bytes, root_bytes)
    certificate = _certificate_authority(bundle, document)
    manifest_digest = _validate_statement(statement, document)
    registry = document["private_registry"]
    authentication = registry["image_publication_authentication"]
    readback = authentication["registry_readback"]
    manifest_request = RegistryManifestRequest(
        registry_host=registry["host"],
        repository=registry["repository"],
        digest=manifest_digest,
        accept=readback["manifest_media_type"],
        maximum_bytes=readback["maximum_manifest_bytes"],
    )
    manifest_response = registry_reader.read_manifest(manifest_request)
    manifest_bytes, config_digest, config_size = _validate_manifest_response(
        manifest_response, manifest_request, document
    )
    manifest_digest = _digest(manifest_bytes)
    config_request = RegistryConfigRequest(
        registry_host=registry["host"],
        repository=registry["repository"],
        manifest_digest=manifest_digest,
        digest=config_digest,
        expected_size=config_size,
        maximum_bytes=readback["maximum_config_bytes"],
    )
    config_response = registry_reader.read_config(config_request)
    config_bytes, image_build_source_sha = _validate_config_response(
        config_response, config_request, document, certificate
    )
    verifier_output = _invoke_verifier(
        verifier=verifier,
        arguments=_arguments(document, certificate),
        manifest_bytes=manifest_bytes,
        bundle_bytes=bundle_bytes,
        root_bytes=root_bytes,
        document=document,
    )
    now, published_at = _trusted_now(clock)
    verified_timestamp = _validate_verifier_output(
        verifier_output,
        bundle=bundle,
        statement=statement,
        certificate=certificate,
        now=now,
        document=document,
    )
    if (
        not registry_reader._completed(manifest_request, config_request)
        or not verifier._used_once()
        or not _policy_unchanged(build_policy, policy_bytes, policy_binding_sha256)
    ):
        _fail("runtime_image.authority_changed")
    try:
        publication = record_builder.AuthenticatedRuntimeImagePublication._from_reviewed_registry_attestation_adapter(
            image_repository=registry["repository"],
            image_digest=manifest_digest,
            image_build_source_sha=image_build_source_sha,
            published_at=published_at,
        )
    except Exception:
        raise RuntimeImagePublicationFailure("runtime_image.output_invalid") from None
    receipt_binding: tuple[object, ...] = (
        1,
        document["policy_id"],
        policy_binding_sha256,
        registry["host"],
        registry["repository"],
        manifest_digest,
        readback["manifest_media_type"],
        len(manifest_bytes),
        config_digest,
        len(config_bytes),
        authentication["image"]["os"],
        authentication["image"]["architecture"],
        image_build_source_sha,
        certificate.run_id,
        certificate.run_attempt,
        certificate.identity,
        certificate.run_invocation_uri,
        _digest(bundle_bytes),
        _digest(root_bytes),
        _digest(_canonical(statement)),
        _digest(certificate.raw),
        document["offline_github_verifier"]["version"],
        document["offline_github_verifier"]["executable_sha256"],
        verified_timestamp,
        published_at,
        False,
        True,
    )
    receipt = dict(zip(RECEIPT_FIELDS, receipt_binding, strict=True))
    if set(receipt) != set(RECEIPT_FIELDS):
        _fail("runtime_image.output_invalid")
    receipt_bytes = _canonical(receipt)
    result = object.__new__(AuthenticatedRuntimeImagePublicationEvidence)
    object.__setattr__(result, "_publication", publication)
    object.__setattr__(result, "_receipt_bytes", receipt_bytes)
    object.__setattr__(result, "_receipt_binding", receipt_binding)
    object.__setattr__(result, "_receipt_sha256", _digest(receipt_bytes))
    object.__setattr__(result, "_seal", _AUTHENTICATED_IMAGE_EVIDENCE_SEAL)
    if not result._is_authenticated():
        _fail("runtime_image.output_invalid")
    return result
