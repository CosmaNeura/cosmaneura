#!/usr/bin/env python3
"""Pure dormant builder for one unsigned staging runtime record.

This module has no filesystem, environment, clock, subprocess, credential,
network, signing, publication, workflow, or Azure capability.  Future reviewed
adapters must authenticate the three sealed inputs before invoking the builder.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, NoReturn

try:
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import validate_staging_restore_runtime_binding as runtime_binding


class RuntimeRecordBuildFailure(RuntimeError):
    """Bounded refusal safe to report without authority or record contents."""


def _fail(code: str) -> NoReturn:
    raise RuntimeRecordBuildFailure(code)


def _canonical_positive_decimal(value: Any) -> bool:
    return (
        type(value) is str
        and value.isascii()
        and value.isdigit()
        and 1 <= len(value) <= 20
        and value[0] != "0"
    )


def _utc_second(value: Any) -> bool:
    try:
        runtime_binding._utc(value, "runtime_record_builder.timestamp_invalid")
    except Exception:
        return False
    return True


_PUBLISHER_INVOCATION_SEAL = object()
_IMAGE_PUBLICATION_SEAL = object()
_UNSIGNED_RUNTIME_RECORD_SEAL = object()


def _content_addressed_record_uri(
    value: Any,
    digest: Any,
    *,
    account_host: Any,
    container_name: Any,
) -> bool:
    if (
        type(value) is not str
        or type(digest) is not str
        or runtime_binding.DIGEST_RE.fullmatch(digest) is None
        or type(account_host) is not str
        or runtime_binding.HOST_RE.fullmatch(account_host) is None
        or type(container_name) is not str
        or not container_name.isascii()
        or container_name != container_name.lower()
        or not 3 <= len(container_name) <= 63
        or not container_name[0].isalnum()
        or not container_name[-1].isalnum()
        or "--" in container_name
        or not container_name.replace("-", "").isalnum()
    ):
        return False
    return value == (f"https://{account_host}/{container_name}/{digest[7:]}.json")


def _output_values_valid(
    *,
    record_bytes: Any,
    record_sha256: Any,
    record_id: Any,
    record_uri: Any,
    image_reference: Any,
    publisher_source_sha: Any,
    publisher_run_invocation_uri: Any,
    record_uri_account_host: Any,
    record_uri_container_name: Any,
) -> bool:
    try:
        if (
            type(record_bytes) is not bytes
            or runtime_binding._digest(record_bytes) != record_sha256
            or not _content_addressed_record_uri(
                record_uri,
                record_sha256,
                account_host=record_uri_account_host,
                container_name=record_uri_container_name,
            )
            or type(record_id) is not str
            or type(image_reference) is not str
        ):
            return False
        record = runtime_binding._parse_json_object(
            record_bytes, "runtime_record_builder.output_invalid"
        )
        projected = {
            name: value for name, value in record.items() if name != "record_id"
        }
        return (
            set(record) == set(runtime_binding.RUNTIME_RECORD_FIELDS)
            and runtime_binding._canonical(record) == record_bytes
            and record.get("record_id") == record_id
            and record_id == runtime_binding._derive_runtime_record_id(projected)
            and image_reference
            == f"{record.get('image_repository')}@{record.get('image_digest')}"
            and record.get("publisher_source_sha") == publisher_source_sha
            and type(publisher_source_sha) is str
            and runtime_binding.GIT_SHA_RE.fullmatch(publisher_source_sha) is not None
            and record.get("publisher_run_invocation_uri")
            == publisher_run_invocation_uri
            and type(publisher_run_invocation_uri) is str
            and runtime_binding.GITHUB_RUN_INVOCATION_RE.fullmatch(
                publisher_run_invocation_uri
            )
            is not None
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedPublisherInvocation:
    """Authority minted by a future reviewed GitHub invocation authenticator."""

    repository: str
    protected_ref: str
    certificate_identity: str
    event_name: str
    protected_ref_asserted: bool
    source_sha: str
    run_id: str
    run_attempt: str
    record_created_at: str
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("runtime_record_builder.publisher_invocation_invalid")

    @classmethod
    def _from_reviewed_github_attestation_adapter(
        cls,
        *,
        repository: str,
        protected_ref: str,
        certificate_identity: str,
        event_name: str,
        protected_ref_asserted: bool,
        source_sha: str,
        run_id: str,
        run_attempt: str,
        record_created_at: str,
    ) -> AuthenticatedPublisherInvocation:
        values = locals()
        if (
            cls is not AuthenticatedPublisherInvocation
            or any(
                type(value) is not str or not value or len(value) > 512
                for value in (repository, protected_ref, certificate_identity)
            )
            or event_name != "workflow_dispatch"
            or protected_ref_asserted is not True
            or type(source_sha) is not str
            or runtime_binding.GIT_SHA_RE.fullmatch(source_sha) is None
            or not _canonical_positive_decimal(run_id)
            or not _canonical_positive_decimal(run_attempt)
            or not _utc_second(record_created_at)
        ):
            _fail("runtime_record_builder.publisher_invocation_invalid")
        result = object.__new__(cls)
        for name in (
            "repository",
            "protected_ref",
            "certificate_identity",
            "event_name",
            "protected_ref_asserted",
            "source_sha",
            "run_id",
            "run_attempt",
            "record_created_at",
        ):
            object.__setattr__(result, name, values[name])
        object.__setattr__(result, "_seal", _PUBLISHER_INVOCATION_SEAL)
        return result

    @property
    def run_invocation_uri(self) -> str:
        if not self._is_authenticated():
            _fail("runtime_record_builder.publisher_invocation_invalid")
        return (
            f"https://github.com/{self.repository}/actions/runs/{self.run_id}"
            f"/attempts/{self.run_attempt}"
        )

    def _is_authenticated(self) -> bool:
        try:
            return (
                type(self) is AuthenticatedPublisherInvocation
                and getattr(self, "_seal", None) is _PUBLISHER_INVOCATION_SEAL
                and type(self.repository) is str
                and 1 <= len(self.repository) <= 512
                and type(self.protected_ref) is str
                and 1 <= len(self.protected_ref) <= 512
                and type(self.certificate_identity) is str
                and 1 <= len(self.certificate_identity) <= 512
                and self.event_name == "workflow_dispatch"
                and self.protected_ref_asserted is True
                and type(self.source_sha) is str
                and runtime_binding.GIT_SHA_RE.fullmatch(self.source_sha) is not None
                and _canonical_positive_decimal(self.run_id)
                and _canonical_positive_decimal(self.run_attempt)
                and _utc_second(self.record_created_at)
            )
        except Exception:
            return False


@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedRuntimeImagePublication:
    """Authority minted by a future reviewed immutable-image authenticator."""

    image_repository: str
    image_digest: str
    image_build_source_sha: str
    published_at: str
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("runtime_record_builder.image_publication_invalid")

    @classmethod
    def _from_reviewed_registry_attestation_adapter(
        cls,
        *,
        image_repository: str,
        image_digest: str,
        image_build_source_sha: str,
        published_at: str,
    ) -> AuthenticatedRuntimeImagePublication:
        values = locals()
        try:
            valid_repository = (
                runtime_binding._pin(
                    image_repository,
                    "image_repository",
                    "runtime_record_builder.image_publication_invalid",
                )
                == image_repository
                and runtime_binding.PLACEHOLDER_RE.fullmatch(image_repository) is None
            )
        except Exception:
            valid_repository = False
        if (
            cls is not AuthenticatedRuntimeImagePublication
            or not valid_repository
            or type(image_digest) is not str
            or runtime_binding.DIGEST_RE.fullmatch(image_digest) is None
            or type(image_build_source_sha) is not str
            or runtime_binding.GIT_SHA_RE.fullmatch(image_build_source_sha) is None
            or not _utc_second(published_at)
        ):
            _fail("runtime_record_builder.image_publication_invalid")
        result = object.__new__(cls)
        for name in (
            "image_repository",
            "image_digest",
            "image_build_source_sha",
            "published_at",
        ):
            object.__setattr__(result, name, values[name])
        object.__setattr__(result, "_seal", _IMAGE_PUBLICATION_SEAL)
        return result

    def _is_authenticated(self) -> bool:
        try:
            runtime_binding._pin(
                self.image_repository,
                "image_repository",
                "runtime_record_builder.image_publication_invalid",
            )
            return (
                type(self) is AuthenticatedRuntimeImagePublication
                and getattr(self, "_seal", None) is _IMAGE_PUBLICATION_SEAL
                and runtime_binding.PLACEHOLDER_RE.fullmatch(self.image_repository)
                is None
                and type(self.image_digest) is str
                and runtime_binding.DIGEST_RE.fullmatch(self.image_digest) is not None
                and type(self.image_build_source_sha) is str
                and runtime_binding.GIT_SHA_RE.fullmatch(self.image_build_source_sha)
                is not None
                and _utc_second(self.published_at)
            )
        except Exception:
            return False


@dataclass(frozen=True, slots=True, init=False)
class UnsignedStagingRuntimeRecord:
    """Immutable unsigned bytes plus derived content and invocation metadata."""

    record_bytes: bytes
    record_sha256: str
    record_id: str
    record_uri: str
    image_reference: str
    publisher_source_sha: str
    publisher_run_invocation_uri: str
    _record_uri_account_host: str = dataclass_field(repr=False, compare=False)
    _record_uri_container_name: str = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("runtime_record_builder.output_invalid")

    @classmethod
    def _from_built(
        cls,
        *,
        record_bytes: bytes,
        record_sha256: str,
        record_id: str,
        record_uri: str,
        image_reference: str,
        publisher_source_sha: str,
        publisher_run_invocation_uri: str,
        record_uri_account_host: str,
        record_uri_container_name: str,
    ) -> UnsignedStagingRuntimeRecord:
        if cls is not UnsignedStagingRuntimeRecord or not _output_values_valid(
            record_bytes=record_bytes,
            record_sha256=record_sha256,
            record_id=record_id,
            record_uri=record_uri,
            image_reference=image_reference,
            publisher_source_sha=publisher_source_sha,
            publisher_run_invocation_uri=publisher_run_invocation_uri,
            record_uri_account_host=record_uri_account_host,
            record_uri_container_name=record_uri_container_name,
        ):
            _fail("runtime_record_builder.output_invalid")
        result = object.__new__(cls)
        values = locals()
        for name in (
            "record_bytes",
            "record_sha256",
            "record_id",
            "record_uri",
            "image_reference",
            "publisher_source_sha",
            "publisher_run_invocation_uri",
            "record_uri_account_host",
            "record_uri_container_name",
        ):
            object.__setattr__(
                result,
                f"_{name}" if name.startswith("record_uri_") else name,
                values[name],
            )
        object.__setattr__(result, "_seal", _UNSIGNED_RUNTIME_RECORD_SEAL)
        return result

    def _is_built(self) -> bool:
        try:
            return (
                type(self) is UnsignedStagingRuntimeRecord
                and getattr(self, "_seal", None) is _UNSIGNED_RUNTIME_RECORD_SEAL
                and _output_values_valid(
                    record_bytes=self.record_bytes,
                    record_sha256=self.record_sha256,
                    record_id=self.record_id,
                    record_uri=self.record_uri,
                    image_reference=self.image_reference,
                    publisher_source_sha=self.publisher_source_sha,
                    publisher_run_invocation_uri=self.publisher_run_invocation_uri,
                    record_uri_account_host=self._record_uri_account_host,
                    record_uri_container_name=self._record_uri_container_name,
                )
            )
        except Exception:
            return False


def build_staging_runtime_record(
    build_policy: runtime_binding.ValidatedRuntimeRecordBuildPolicy,
    *,
    publisher_invocation: AuthenticatedPublisherInvocation,
    image_publication: AuthenticatedRuntimeImagePublication,
) -> UnsignedStagingRuntimeRecord:
    """Compose one canonical unsigned record from three sealed authorities."""

    if (
        type(build_policy) is not runtime_binding.ValidatedRuntimeRecordBuildPolicy
        or not build_policy._is_validated()
        or type(publisher_invocation) is not AuthenticatedPublisherInvocation
        or not publisher_invocation._is_authenticated()
        or type(image_publication) is not AuthenticatedRuntimeImagePublication
        or not image_publication._is_authenticated()
    ):
        _fail("runtime_record_builder.authority_invalid")
    try:
        document = build_policy._document()
        invocation_uri = publisher_invocation.run_invocation_uri
        binding = document["external_runtime_record"]["binding"]
        signature = document["external_runtime_record"]["signature"]
        jobs = document["runtime"]["jobs"]
        environment = document["runtime"]["container_apps_environment"]
        identities = document["identities"]["jobs"]
        published_at = runtime_binding._utc(
            image_publication.published_at,
            "runtime_record_builder.image_publication_invalid",
        )
        created_at = runtime_binding._utc(
            publisher_invocation.record_created_at,
            "runtime_record_builder.publisher_invocation_invalid",
        )
    except Exception:
        raise RuntimeRecordBuildFailure(
            "runtime_record_builder.authority_invalid"
        ) from None
    if (
        publisher_invocation.repository != binding["repository"]
        or publisher_invocation.protected_ref != binding["protected_ref"]
        or publisher_invocation.certificate_identity
        != signature["certificate_identity"]
        or publisher_invocation.event_name != "workflow_dispatch"
        or publisher_invocation.protected_ref_asserted is not True
        or image_publication.image_repository != jobs["provision"]["image_repository"]
        or image_publication.image_repository != jobs["cleanup"]["image_repository"]
        or image_publication.image_build_source_sha != publisher_invocation.source_sha
        or created_at <= published_at
    ):
        _fail("runtime_record_builder.authority_mismatch")
    image_reference = (
        f"{image_publication.image_repository}@{image_publication.image_digest}"
    )
    record_without_id: dict[str, Any] = {
        "schema_version": 1,
        "policy_id": document["policy_id"],
        "policy_binding_sha256": build_policy.policy_binding_sha256,
        "repository": publisher_invocation.repository,
        "protected_ref": publisher_invocation.protected_ref,
        "publisher_source_sha": publisher_invocation.source_sha,
        "publisher_run_invocation_uri": invocation_uri,
        "image_repository": image_publication.image_repository,
        "image_digest": image_publication.image_digest,
        "provision_job_image_reference": image_reference,
        "cleanup_job_image_reference": image_reference,
        "image_published_at": image_publication.published_at,
        "environment_resource_id": environment["resource_id"],
        "provision_job_resource_id": jobs["provision"]["resource_id"],
        "provision_job_configuration_template_sha256": jobs["provision"][
            "configuration_template_sha256"
        ],
        "provision_job_identity_resource_id": identities["provision"]["resource_id"],
        "cleanup_job_resource_id": jobs["cleanup"]["resource_id"],
        "cleanup_job_configuration_template_sha256": jobs["cleanup"][
            "configuration_template_sha256"
        ],
        "cleanup_job_identity_resource_id": identities["cleanup"]["resource_id"],
        "network_policy_sha256": environment["network_policy_sha256"],
        "created_at": publisher_invocation.record_created_at,
    }
    try:
        record_id = runtime_binding._derive_runtime_record_id(record_without_id)
        record = {**record_without_id, "record_id": record_id}
        record_bytes = runtime_binding._canonical(record)
        validated = runtime_binding._validate_external_runtime_record_contents(
            document, record_bytes
        )
    except Exception:
        raise RuntimeRecordBuildFailure(
            "runtime_record_builder.output_invalid"
        ) from None
    if validated.get("record_id") != record_id:
        _fail("runtime_record_builder.output_invalid")
    record_sha256 = runtime_binding._digest(record_bytes)
    ingress = document["runtime_record_ingress"]
    record_uri = (
        f"https://{ingress['account_host']}/{ingress['container_name']}/"
        f"{record_sha256[7:]}.json"
    )
    return UnsignedStagingRuntimeRecord._from_built(
        record_bytes=bytes(record_bytes),
        record_sha256=record_sha256,
        record_id=record_id,
        record_uri=record_uri,
        image_reference=image_reference,
        publisher_source_sha=publisher_invocation.source_sha,
        publisher_run_invocation_uri=invocation_uri,
        record_uri_account_host=ingress["account_host"],
        record_uri_container_name=ingress["container_name"],
    )
