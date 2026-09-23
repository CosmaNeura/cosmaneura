from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scripts import build_staging_runtime_record as builder
from scripts import validate_staging_restore_runtime_binding as binding

from tests.infrastructure import test_staging_restore_runtime_binding as helpers

PUBLISHED_AT = "2026-09-04T12:00:00Z"
CREATED_AT = "2026-09-04T12:01:00Z"
SOURCE_SHA = "c" * 40


def _dormant_configured_policy() -> dict[str, Any]:
    policy = helpers._configured_policy()
    policy["activation_ready"] = False
    policy["activation"] = dict.fromkeys(policy["activation"], False)
    policy["activation_blockers"] = [
        "restore_runtime_binding_policy_disabled",
        *(blocker for _name, blocker in binding.ACTIVATION),
    ]
    return policy


def _invocation(
    policy: dict[str, Any], **changes: Any
) -> builder.AuthenticatedPublisherInvocation:
    values = {
        "repository": policy["external_runtime_record"]["binding"]["repository"],
        "protected_ref": policy["external_runtime_record"]["binding"]["protected_ref"],
        "certificate_identity": policy["external_runtime_record"]["signature"][
            "certificate_identity"
        ],
        "event_name": "workflow_dispatch",
        "protected_ref_asserted": True,
        "source_sha": SOURCE_SHA,
        "run_id": "901",
        "run_attempt": "1",
        "record_created_at": CREATED_AT,
    }
    values.update(changes)
    return builder.AuthenticatedPublisherInvocation._from_reviewed_github_attestation_adapter(
        **values
    )


def _image(
    policy: dict[str, Any], **changes: Any
) -> builder.AuthenticatedRuntimeImagePublication:
    values = {
        "image_repository": policy["runtime"]["jobs"]["provision"]["image_repository"],
        "image_digest": f"sha256:{'a' * 64}",
        "image_build_source_sha": SOURCE_SHA,
        "published_at": PUBLISHED_AT,
    }
    values.update(changes)
    return builder.AuthenticatedRuntimeImagePublication._from_reviewed_registry_attestation_adapter(
        **values
    )


def _build(
    policy: dict[str, Any] | None = None,
    *,
    invocation_changes: dict[str, Any] | None = None,
    image_changes: dict[str, Any] | None = None,
) -> tuple[
    dict[str, Any],
    binding.ValidatedRuntimeRecordBuildPolicy,
    builder.UnsignedStagingRuntimeRecord,
]:
    document = policy or _dormant_configured_policy()
    build_policy = binding.validate_runtime_record_build_policy(document)
    result = builder.build_staging_runtime_record(
        build_policy,
        publisher_invocation=_invocation(document, **(invocation_changes or {})),
        image_publication=_image(document, **(image_changes or {})),
    )
    return document, build_policy, result


def test_builds_deterministic_closed_world_content_addressed_record() -> None:
    policy, build_policy, result = _build()
    repeated = builder.build_staging_runtime_record(
        build_policy,
        publisher_invocation=_invocation(policy),
        image_publication=_image(policy),
    )
    record = json.loads(result.record_bytes)
    projected = {name: value for name, value in record.items() if name != "record_id"}

    assert result._is_built()
    assert repeated == result
    assert set(record) == set(binding.RUNTIME_RECORD_FIELDS)
    assert len(record) == 22
    assert result.record_bytes == binding._canonical(record)
    assert not result.record_bytes.endswith(b"\n")
    assert result.record_sha256 == binding._digest(result.record_bytes)
    assert result.record_id == binding._derive_runtime_record_id(projected)
    assert result.record_id == record["record_id"]
    assert len(result.record_id) == len("runtime-") + 64
    assert result.record_uri == (
        "https://restoreevidence.blob.core.windows.net/"
        f"restore-runtime-bindings-v1/{result.record_sha256[7:]}.json"
    )
    assert record["policy_binding_sha256"] == build_policy.policy_binding_sha256
    assert record["publisher_source_sha"] == SOURCE_SHA
    assert record["publisher_run_invocation_uri"].endswith(
        "/actions/runs/901/attempts/1"
    )
    assert result.image_reference == (
        f"restoreexecutor.azurecr.io/cosmaneura/restore-executor@sha256:{'a' * 64}"
    )


def test_builder_output_is_accepted_by_the_shared_receiver_validator() -> None:
    dormant, _build_policy, result = _build()
    executable = helpers._activated(dormant)
    bundle_digest = f"sha256:{'b' * 64}"
    pointer_values = {
        "record_uri": result.record_uri,
        "record_sha256": result.record_sha256,
        "signature_bundle_uri": (
            "https://restoreevidence.blob.core.windows.net/"
            f"restore-runtime-bindings-v1/{bundle_digest[7:]}.sigstore.json"
        ),
        "signature_bundle_sha256": bundle_digest,
    }
    observations = {
        job_kind: helpers._job_observation(executable, job_kind, pointer_values)
        for job_kind in ("provision", "cleanup")
    }
    references = {
        job_kind: binding.ExternalRuntimeBindingReference._from_authenticated_job_observation(
            observation
        )
        for job_kind, observation in observations.items()
    }
    root = executable["evidence_ingress"]["private_versioned_secret_source"]["secrets"][
        "github_trusted_root"
    ]
    signature = (
        binding.VerifiedExternalRuntimeRecordSignature._mint_from_reviewed_verifier(
            record_sha256=result.record_sha256,
            bundle_sha256=bundle_digest,
            trusted_root_sha256=root["sha256"],
            certificate_oidc_issuer=executable["external_runtime_record"]["signature"][
                "certificate_oidc_issuer"
            ],
            certificate_identity=executable["external_runtime_record"]["signature"][
                "certificate_identity"
            ],
        )
    )

    validated = binding.validate_external_runtime_binding(
        executable,
        result.record_bytes,
        provision_reference=references["provision"],
        cleanup_reference=references["cleanup"],
        signature_verification=signature,
    )

    assert validated.record_bytes == result.record_bytes
    assert validated.record_sha256 == result.record_sha256


def test_record_id_derivation_covers_every_other_authoritative_field() -> None:
    _policy, _build_policy, result = _build()
    record = json.loads(result.record_bytes)
    projected = {name: value for name, value in record.items() if name != "record_id"}
    original = binding._derive_runtime_record_id(projected)

    assert projected, (
        "there is nothing to check, so the assertions below would pass vacuously"
    )
    for field in projected:
        changed = deepcopy(projected)
        value = changed[field]
        changed[field] = value + "x" if type(value) is str else value + 1
        assert binding._derive_runtime_record_id(changed) != original, field


@pytest.mark.parametrize(
    "record_id",
    [
        "runtime-0123456789abcdef",
        f"runtime-{'a' * 64}",
        f"runtime-{'A' * 64}",
    ],
)
def test_shared_content_validator_rejects_stale_or_noncanonical_record_id(
    record_id: str,
) -> None:
    policy, _build_policy, result = _build()
    record = json.loads(result.record_bytes)
    record["record_id"] = record_id

    with pytest.raises(binding.RuntimeBindingFailure, match="runtime_record.record_id"):
        binding._validate_external_runtime_record_contents(
            policy, binding._canonical(record)
        )


def test_build_policy_snapshots_configured_policy_and_ignores_false_activation() -> (
    None
):
    policy = _dormant_configured_policy()
    build_policy = binding.validate_runtime_record_build_policy(policy)
    expected_binding = build_policy.policy_binding_sha256
    policy["policy_id"] = "mutated-after-snapshot"

    result = builder.build_staging_runtime_record(
        build_policy,
        publisher_invocation=_invocation(_dormant_configured_policy()),
        image_publication=_image(_dormant_configured_policy()),
    )

    assert json.loads(result.record_bytes)["policy_binding_sha256"] == expected_binding
    with pytest.raises(
        binding.RuntimeBindingFailure,
        match="runtime_record.build_policy_unconfigured",
    ):
        binding.validate_runtime_record_build_policy(helpers._policy())


def test_build_policy_rejects_configured_non_private_runtime_ingress() -> None:
    policy = _dormant_configured_policy()
    policy["runtime_record_ingress"]["account_host"] = "evil.example"

    with pytest.raises(
        binding.RuntimeBindingFailure,
        match="evidence_ingress.private_binding",
    ):
        binding.validate_runtime_record_build_policy(policy)


def test_policy_pins_exact_record_id_and_timestamp_derivations() -> None:
    policy = binding.load_policy()
    record = policy["external_runtime_record"]

    assert record["record_id_derivation"] == {
        "algorithm": "sha256",
        "serialization": "canonical-json-no-trailing-newline",
        "projection": "all-required-fields-except-record_id",
        "prefix": "runtime-",
        "hex_characters": 64,
    }
    assert record["timestamp_binding"] == {
        "format": "rfc3339-utc-second-Z",
        "image_published_at_source": ("sealed-authenticated-runtime-image-publication"),
        "created_at_source": "sealed-authenticated-publisher-invocation",
        "created_at_strictly_after_image_published_at": True,
        "caller_override_permitted": False,
    }
    assert policy["activation_ready"] is False
    assert set(policy["activation"].values()) == {False}


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"source_sha": "C" * 40}, "publisher_invocation_invalid"),
        ({"source_sha": "c" * 39}, "publisher_invocation_invalid"),
        ({"run_id": "0"}, "publisher_invocation_invalid"),
        ({"run_id": "0901"}, "publisher_invocation_invalid"),
        ({"run_attempt": "01"}, "publisher_invocation_invalid"),
        (
            {"record_created_at": "2026-09-04T12:01:00.1Z"},
            "publisher_invocation_invalid",
        ),
        (
            {"record_created_at": "2026-09-04T12:01:00+00:00"},
            "publisher_invocation_invalid",
        ),
        ({"event_name": "push"}, "publisher_invocation_invalid"),
        ({"protected_ref_asserted": False}, "publisher_invocation_invalid"),
    ],
)
def test_publisher_authority_refuses_noncanonical_claims(
    changes: dict[str, Any], expected: str
) -> None:
    with pytest.raises(builder.RuntimeRecordBuildFailure, match=expected):
        _invocation(_dormant_configured_policy(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"image_digest": f"sha256:{'A' * 64}"},
        {"image_digest": "latest"},
        {"image_build_source_sha": "C" * 40},
        {"published_at": "2026-09-04T12:00:00.1Z"},
        {"image_repository": "restoreexecutor.azurecr.io/repo:latest"},
    ],
)
def test_image_authority_refuses_noncanonical_claims(changes: dict[str, Any]) -> None:
    with pytest.raises(
        builder.RuntimeRecordBuildFailure, match="image_publication_invalid"
    ):
        _image(_dormant_configured_policy(), **changes)


@pytest.mark.parametrize(
    ("invocation_changes", "image_changes"),
    [
        ({"repository": "CosmaNeura/other"}, {}),
        ({"protected_ref": "refs/heads/other"}, {}),
        ({"certificate_identity": "https://github.com/other"}, {}),
        ({}, {"image_repository": "other.azurecr.io/restore"}),
        ({}, {"image_build_source_sha": "d" * 40}),
        ({"record_created_at": PUBLISHED_AT}, {}),
        ({"record_created_at": "2026-09-04T11:59:59Z"}, {}),
    ],
)
def test_builder_rejects_cross_authority_or_policy_mismatch(
    invocation_changes: dict[str, Any], image_changes: dict[str, Any]
) -> None:
    policy = _dormant_configured_policy()
    build_policy = binding.validate_runtime_record_build_policy(policy)

    with pytest.raises(builder.RuntimeRecordBuildFailure, match="authority_mismatch"):
        builder.build_staging_runtime_record(
            build_policy,
            publisher_invocation=_invocation(policy, **invocation_changes),
            image_publication=_image(policy, **image_changes),
        )


def test_direct_fake_subclass_and_tampered_authorities_refuse() -> None:
    policy = _dormant_configured_policy()
    build_policy = binding.validate_runtime_record_build_policy(policy)
    invocation = _invocation(policy)
    image = _image(policy)

    with pytest.raises(builder.RuntimeRecordBuildFailure):
        builder.AuthenticatedPublisherInvocation()
    with pytest.raises(builder.RuntimeRecordBuildFailure):
        builder.AuthenticatedRuntimeImagePublication()
    with pytest.raises(builder.RuntimeRecordBuildFailure):
        builder.UnsignedStagingRuntimeRecord()
    with pytest.raises(binding.RuntimeBindingFailure):
        binding.ValidatedRuntimeRecordBuildPolicy()

    fake = object.__new__(builder.AuthenticatedPublisherInvocation)
    with pytest.raises(builder.RuntimeRecordBuildFailure, match="authority_invalid"):
        builder.build_staging_runtime_record(
            build_policy, publisher_invocation=fake, image_publication=image
        )

    class InvocationSubclass(builder.AuthenticatedPublisherInvocation):
        pass

    with pytest.raises(builder.RuntimeRecordBuildFailure):
        InvocationSubclass._from_reviewed_github_attestation_adapter(
            repository=invocation.repository,
            protected_ref=invocation.protected_ref,
            certificate_identity=invocation.certificate_identity,
            event_name=invocation.event_name,
            protected_ref_asserted=True,
            source_sha=invocation.source_sha,
            run_id=invocation.run_id,
            run_attempt=invocation.run_attempt,
            record_created_at=invocation.record_created_at,
        )

    class ImageSubclass(builder.AuthenticatedRuntimeImagePublication):
        pass

    with pytest.raises(builder.RuntimeRecordBuildFailure):
        ImageSubclass._from_reviewed_registry_attestation_adapter(
            image_repository=image.image_repository,
            image_digest=image.image_digest,
            image_build_source_sha=image.image_build_source_sha,
            published_at=image.published_at,
        )

    fake_image = object.__new__(builder.AuthenticatedRuntimeImagePublication)
    with pytest.raises(builder.RuntimeRecordBuildFailure, match="authority_invalid"):
        builder.build_staging_runtime_record(
            build_policy,
            publisher_invocation=invocation,
            image_publication=fake_image,
        )

    object.__setattr__(invocation, "source_sha", "d" * 40)
    with pytest.raises(builder.RuntimeRecordBuildFailure, match="authority_mismatch"):
        builder.build_staging_runtime_record(
            build_policy, publisher_invocation=invocation, image_publication=image
        )

    object.__setattr__(build_policy, "policy_binding_sha256", f"sha256:{'f' * 64}")
    with pytest.raises(builder.RuntimeRecordBuildFailure, match="authority_invalid"):
        builder.build_staging_runtime_record(
            build_policy,
            publisher_invocation=_invocation(policy),
            image_publication=image,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("record_id", f"runtime-{'f' * 64}"),
        ("image_reference", "restoreexecutor.azurecr.io/other@sha256:" + "a" * 64),
        ("publisher_source_sha", "d" * 40),
        (
            "publisher_run_invocation_uri",
            "https://github.com/CosmaNeura/cosmaneura/actions/runs/902/attempts/1",
        ),
        (
            "record_uri",
            "https://evil.example/restore-runtime-bindings-v1/" + "a" * 64 + ".json",
        ),
    ],
)
def test_unsigned_output_detects_mirrored_metadata_tampering(
    field: str, value: str
) -> None:
    _policy, _build_policy, result = _build()

    object.__setattr__(result, field, value)

    assert result._is_built() is False


def test_unsigned_output_rejects_same_digest_at_another_https_location() -> None:
    _policy, _build_policy, result = _build()

    for record_uri in (
        f"https://evil.example/restore-runtime-bindings-v1/{result.record_sha256[7:]}.json",
        "https://restoreevidence.blob.core.windows.net/other-container/"
        f"{result.record_sha256[7:]}.json",
    ):
        object.__setattr__(result, "record_uri", record_uri)
        assert result._is_built() is False

    object.__setattr__(result, "record_uri", object())
    assert result._is_built() is False


def test_builder_module_has_no_io_clock_cli_or_environment_imports() -> None:
    source_path = Path(builder.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert imported.isdisjoint(
        {
            "argparse",
            "datetime",
            "os",
            "pathlib",
            "requests",
            "socket",
            "subprocess",
            "time",
            "urllib",
        }
    )
    assert "open(" not in source_path.read_text(encoding="utf-8")
