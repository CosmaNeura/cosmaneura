from __future__ import annotations

import ast
import base64
import inspect
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Callable, cast

import pytest

from scripts import authenticate_staging_github_attestation as github_auth
from scripts import authenticate_staging_runtime_image_publication as authentication
from scripts import authenticate_staging_runtime_record_signature as signature_auth
from scripts import build_staging_runtime_record as builder
from scripts import validate_staging_restore_runtime_binding as binding

from tests.infrastructure import test_staging_restore_runtime_binding as helpers
from tests.infrastructure import (
    test_staging_runtime_record_signature_authentication as signature_helpers,
)

NOW = datetime(2026, 9, 4, 12, 5, tzinfo=UTC)
SOURCE_SHA = "c" * 40
RUN_ID = "901"
RUN_ATTEMPT = "1"
VERIFIER_DIGEST = f"sha256:{'d' * 64}"
IDENTITY = (
    "https://github.com/CosmaNeura/cosmaneura/.github/workflows/"
    "staging_runtime_image_publish.yml@refs/heads/master"
)
LAYER = {
    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
    "digest": f"sha256:{'a' * 64}",
    "size": 123,
}


class FakeTransport:
    def __init__(
        self,
        manifest: authentication.RegistryManifestResponse,
        config: authentication.RegistryConfigResponse,
        events: list[str],
    ) -> None:
        self.manifest = manifest
        self.config = config
        self.events = events
        self.manifest_calls: list[Any] = []
        self.config_calls: list[Any] = []
        self.error: Exception | None = None
        self.after_config: Callable[[], None] | None = None

    def get_manifest(self, request: Any) -> Any:
        self.events.append("manifest")
        self.manifest_calls.append(request)
        if self.error:
            raise self.error
        return self.manifest

    def get_config(self, request: Any) -> Any:
        self.events.append("config")
        self.config_calls.append(request)
        if self.error:
            raise self.error
        if self.after_config:
            self.after_config()
        return self.config


class FakeVerifier:
    def __init__(self, output: Any, events: list[str]) -> None:
        self.output = output
        self.events = events
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.version = "2.97.0"
        self.digest = VERIFIER_DIGEST

    def verify(self, **kwargs: Any) -> Any:
        self.events.append("verifier")
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return github_auth.AttestationVerifierResponse(
            returncode=0,
            stdout=authentication._canonical(self.output),
            stderr=b"",
            verifier_version=self.version,
            verifier_sha256=self.digest,
        )


def _claims(**changes: str) -> dict[str, str]:
    values = signature_helpers._claims(helpers._configured_policy())
    values["buildSignerURI"] = values["buildConfigURI"] = IDENTITY
    values.update(changes)
    return values


def _case(
    *,
    claim_changes: dict[str, str] | None = None,
    config_changes: dict[str, Any] | None = None,
    manifest_changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events: list[str] = []
    policy = helpers._configured_policy()
    policy["activation_ready"] = False
    policy["activation"] = dict.fromkeys(policy["activation"], False)
    policy["activation_blockers"] = [
        "restore_runtime_binding_policy_disabled",
        *(blocker for _name, blocker in binding.ACTIVATION),
    ]
    image_auth = policy["private_registry"]["image_publication_authentication"]
    image_auth["certificate"]["identity"] = IDENTITY
    image_auth["verifier_profile"]["publisher_containment_proven"] = False
    image_auth["publication"]["image_build_provenance_proven"] = False
    policy["offline_github_verifier"]["executable_sha256"] = VERIFIER_DIGEST
    root_bytes = signature_helpers._root()[1]
    policy["evidence_ingress"]["private_versioned_secret_source"]["secrets"][
        "github_trusted_root"
    ]["sha256"] = authentication._digest(root_bytes)
    build_policy = binding.validate_runtime_record_build_policy(policy)

    config = {
        "architecture": "amd64",
        "os": "linux",
        "config": {
            "Labels": {
                "org.opencontainers.image.source": (
                    "https://github.com/CosmaNeura/cosmaneura"
                ),
                "org.opencontainers.image.revision": SOURCE_SHA,
                "build.run_id": RUN_ID,
                "build.run_attempt": RUN_ATTEMPT,
            }
        },
        "rootfs": {"type": "layers", "diff_ids": []},
        "history": [],
    }
    if config_changes:
        for path, value in config_changes.items():
            if path.startswith("config.Labels."):
                config["config"]["Labels"][path.removeprefix("config.Labels.")] = value
                continue
            target = config
            parts = path.split(".")
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = value
    config_bytes = authentication._canonical(config)
    config_digest = authentication._digest(config_bytes)
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": config_digest,
            "size": len(config_bytes),
        },
        "layers": [dict(LAYER)],
    }
    if manifest_changes:
        for path, value in manifest_changes.items():
            target = manifest
            parts = path.split(".")
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = value
    manifest_bytes = authentication._canonical(manifest)
    manifest_digest = authentication._digest(manifest_bytes)
    statement = {
        "_type": signature_auth.STATEMENT_TYPE,
        "subject": [
            {
                "name": image_auth["statement"]["subject_name"],
                "digest": {"sha256": manifest_digest[7:]},
            }
        ],
        "predicateType": signature_auth.PREDICATE_TYPE,
        "predicate": {
            "buildDefinition": {
                "buildType": signature_auth.BUILD_TYPE,
                "externalParameters": {},
                "internalParameters": {},
                "resolvedDependencies": [],
            },
            "runDetails": {"builder": {}, "metadata": {}, "byproducts": []},
        },
    }
    claims = _claims(**(claim_changes or {}))
    certificate = signature_helpers._certificate(policy, claims, san=IDENTITY)
    summary = signature_helpers._summary(policy, claims, certificate)
    summary["subjectAlternativeName"] = IDENTITY
    bundle = {
        "mediaType": signature_auth.BUNDLE_MEDIA_TYPE,
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
    output = [
        {
            "attestation": {
                "bundle": deepcopy(bundle),
                "bundle_url": "",
                "initiator": "",
            },
            "verificationResult": {
                "mediaType": signature_auth.VERIFICATION_MEDIA_TYPE,
                "signature": {"certificate": summary},
                "verifiedTimestamps": [
                    {
                        "type": "TimestampAuthority",
                        "uri": signature_auth.TIMESTAMP_AUTHORITY_URI,
                        "timestamp": "2026-09-04T12:04:59.123456789Z",
                    }
                ],
                "verifiedIdentity": {
                    "subjectAlternativeName": {"subjectAlternativeName": IDENTITY},
                    "issuer": {"issuer": "", "regexp": ".*"},
                    "runnerEnvironment": "github-hosted",
                },
                "statement": deepcopy(statement),
            },
        }
    ]
    registry = policy["private_registry"]
    manifest_response = authentication.RegistryManifestResponse(
        registry_host=registry["host"],
        repository=registry["repository"],
        requested_digest=manifest_digest,
        status_code=200,
        content_type="application/vnd.oci.image.manifest.v1+json",
        docker_content_digest=manifest_digest,
        body=manifest_bytes,
    )
    config_response = authentication.RegistryConfigResponse(
        registry_host=registry["host"],
        repository=registry["repository"],
        requested_digest=manifest["config"]["digest"],
        status_code=200,
        body=config_bytes,
    )
    transport = FakeTransport(manifest_response, config_response, events)
    verifier_delegate = FakeVerifier(output, events)
    return {
        "policy": policy,
        "build_policy": build_policy,
        "root_bytes": root_bytes,
        "bundle": bundle,
        "bundle_bytes": bundle_bytes,
        "statement": statement,
        "manifest": manifest,
        "manifest_bytes": manifest_bytes,
        "config": config,
        "config_bytes": config_bytes,
        "transport": transport,
        "reader": authentication.OneShotExactRegistryImageReader(transport),
        "verifier_delegate": verifier_delegate,
        "verifier": authentication.OneShotOfflineAttestationVerifier(verifier_delegate),
        "output": output,
        "events": events,
    }


def _authenticate(case: dict[str, Any]) -> Any:
    def clock() -> datetime:
        case["events"].append("clock")
        return NOW

    return authentication.authenticate_runtime_image_publication(
        case["build_policy"],
        provenance_bundle_bytes=case["bundle_bytes"],
        trusted_root_bytes=case["root_bytes"],
        registry_reader=case["reader"],
        verifier=case["verifier"],
        clock=clock,
    )


def test_golden_path_uses_exact_raw_bytes_and_builder_consumes_publication() -> None:
    case = _case()
    result = _authenticate(case)
    receipt = result.receipt
    invocation = builder.AuthenticatedPublisherInvocation._from_reviewed_github_attestation_adapter(
        repository="CosmaNeura/cosmaneura",
        protected_ref="refs/heads/master",
        certificate_identity=case["policy"]["external_runtime_record"]["signature"][
            "certificate_identity"
        ],
        event_name="workflow_dispatch",
        protected_ref_asserted=True,
        source_sha=SOURCE_SHA,
        run_id=RUN_ID,
        run_attempt=RUN_ATTEMPT,
        record_created_at="2026-09-04T12:06:00Z",
    )
    record = builder.build_staging_runtime_record(
        case["build_policy"],
        publisher_invocation=invocation,
        image_publication=result.publication,
    )

    assert result._is_authenticated()
    assert result.receipt_bytes == authentication._canonical(receipt)
    assert result.receipt_sha256 == authentication._digest(result.receipt_bytes)
    assert result.publication.image_digest == authentication._digest(
        case["manifest_bytes"]
    )
    assert result.publication.image_build_source_sha == SOURCE_SHA
    assert result.publication.published_at == "2026-09-04T12:05:00Z"
    assert receipt["image_build_provenance_proven"] is False
    assert record.image_reference.endswith(f"@{result.publication.image_digest}")
    assert len(case["transport"].manifest_calls) == 1
    assert len(case["transport"].config_calls) == 1
    assert case["events"] == ["manifest", "config", "verifier", "clock"]

    call = case["verifier_delegate"].calls[0]
    profile = case["policy"]["private_registry"]["image_publication_authentication"][
        "verifier_profile"
    ]
    assert call["files"][profile["subject_filename"]] is case["manifest_bytes"]
    assert call["files"][profile["bundle_filename"]] == case["bundle_bytes"]
    assert call["files"][profile["trusted_root_filename"]] == case["root_bytes"]
    expected_arguments = (
        f"gh attestation verify runtime-image-manifest.json --bundle runtime-image-provenance.sigstore.json "
        f"--cert-identity {IDENTITY} --cert-oidc-issuer https://token.actions.githubusercontent.com "
        "--custom-trusted-root github-trusted-root.jsonl --deny-self-hosted-runners --digest-alg sha256 "
        "--format=json --hostname github.com --no-public-good --predicate-type https://slsa.dev/provenance/v1 "
        f"--repo CosmaNeura/cosmaneura --signer-digest {SOURCE_SHA} --source-digest {SOURCE_SHA} "
        "--source-ref refs/heads/master"
    )
    assert call["arguments"] == tuple(expected_arguments.split())
    assert call["timeout_seconds"] == 30
    assert call["maximum_output_bytes"] == 4_194_304
    assert "oci://" not in " ".join(call["arguments"])


@pytest.mark.parametrize(
    ("target", "field", "value", "code"),
    [
        ("manifest", "docker_content_digest", f"sha256:{'0' * 64}", "manifest"),
        ("manifest", "content_type", "application/json", "manifest"),
        ("manifest", "body", b"{}", "manifest"),
        ("config", "requested_digest", f"sha256:{'0' * 64}", "config"),
        ("config", "body", b"{}", "config"),
    ],
)
def test_raw_registry_readback_mismatches_refuse_before_verifier(
    target: str, field: str, value: Any, code: str
) -> None:
    case = _case()
    response = getattr(case["transport"], target)
    object.__setattr__(response, field, value)
    with pytest.raises(
        authentication.RuntimeImagePublicationFailure,
        match=rf"runtime_image\.{code}_invalid",
    ):
        _authenticate(case)
    assert case["verifier_delegate"].calls == []


@pytest.mark.parametrize(
    ("config_changes", "manifest_changes"),
    [
        ({"os": "windows"}, None),
        ({"architecture": "arm64"}, None),
        (
            {"config.Labels.org.opencontainers.image.source": "https://wrong.invalid"},
            None,
        ),
        ({"config.Labels.org.opencontainers.image.revision": "e" * 40}, None),
        ({"config.Labels.build.run_id": "902"}, None),
        ({"config.Labels.build.run_attempt": "2"}, None),
        (None, {"mediaType": "application/vnd.oci.image.index.v1+json"}),
        (None, {"config.mediaType": "application/octet-stream"}),
        (None, {"config.size": 1}),
        (None, {"config.digest": f"sha256:{'e' * 64}"}),
        (None, {"layers": []}),
        (None, {"layers": [{**LAYER, "urls": ["https://public.invalid/layer"]}]}),
        (None, {"layers": [{**LAYER, "mediaType": "invalid"}]}),
        (None, {"layers": [{**LAYER, "digest": "sha256:bad"}]}),
        (None, {"layers": [{**LAYER, "size": 0}]}),
    ],
)
def test_platform_labels_media_type_and_descriptor_chain_are_exact(
    config_changes: dict[str, Any] | None,
    manifest_changes: dict[str, Any] | None,
) -> None:
    case = _case(config_changes=config_changes, manifest_changes=manifest_changes)
    with pytest.raises(authentication.RuntimeImagePublicationFailure):
        _authenticate(case)
    assert case["verifier_delegate"].calls == []


@pytest.mark.parametrize(
    "claim_changes",
    [
        {
            "buildSignerURI": "https://github.com/CosmaNeura/cosmaneura/.github/workflows/wrong.yml@refs/heads/master"
        },
        {"sourceRepositoryURI": "https://github.com/wrong/repo"},
        {"sourceRepositoryRef": "refs/heads/staging"},
        {"buildTrigger": "push"},
        {"runnerEnvironment": "self-hosted"},
        {"sourceRepositoryDigest": "e" * 40},
        {"buildSignerDigest": "e" * 40},
        {"buildConfigDigest": "e" * 40},
    ],
)
def test_wrong_github_certificate_claims_refuse_before_registry_or_verifier(
    claim_changes: dict[str, str],
) -> None:
    case = _case(claim_changes=claim_changes)
    with pytest.raises(
        authentication.RuntimeImagePublicationFailure,
        match="runtime_image.certificate_invalid",
    ):
        _authenticate(case)
    assert case["transport"].manifest_calls == []
    assert case["verifier_delegate"].calls == []


def test_bundle_root_subject_and_timestamp_are_strict() -> None:
    cases = [_case() for _ in range(5)]
    cases[0]["bundle_bytes"] = b'{"mediaType":NaN}'
    cases[1]["root_bytes"] = b'{"mediaType":"wrong"}\n'
    cases[1]["policy"]["evidence_ingress"]["private_versioned_secret_source"][
        "secrets"
    ]["github_trusted_root"]["sha256"] = authentication._digest(cases[1]["root_bytes"])
    cases[1]["build_policy"] = binding.validate_runtime_record_build_policy(
        cases[1]["policy"]
    )
    statement = cases[2]["statement"]
    statement["subject"][0]["digest"]["sha256"] = "A" * 64
    cases[2]["bundle"]["dsseEnvelope"]["payload"] = base64.b64encode(
        authentication._canonical(statement)
    ).decode()
    cases[2]["bundle_bytes"] = authentication._canonical(cases[2]["bundle"])
    cases[3]["output"][0]["verificationResult"]["verifiedTimestamps"][0]["uri"] = (
        "timestamp.sigstore.dev"
    )
    cases[4]["output"][0]["verificationResult"]["verifiedTimestamps"][0][
        "timestamp"
    ] = "2026-09-04T12:05:01Z"
    assert cases, (
        "there is nothing to check, so the assertions below would pass vacuously"
    )
    for index, case in enumerate(cases):
        with pytest.raises(authentication.RuntimeImagePublicationFailure):
            _authenticate(case)
        if index < 3:
            assert case["verifier_delegate"].calls == []


def test_certificate_run_must_match_config_labels() -> None:
    case = _case(
        claim_changes={
            "runInvocationURI": "https://github.com/CosmaNeura/cosmaneura/actions/runs/902/attempts/2"
        }
    )
    with pytest.raises(
        authentication.RuntimeImagePublicationFailure, match="config_invalid"
    ):
        _authenticate(case)
    assert case["events"] == ["manifest", "config"]


def test_one_shot_adapters_and_mutating_policy_fail_closed() -> None:
    case = _case()
    _authenticate(case)
    with pytest.raises(
        authentication.RuntimeImagePublicationFailure,
        match="reader_already_used",
    ):
        _authenticate(case)
    assert len(case["verifier_delegate"].calls) == 1

    mutated = _case()
    mutated["transport"].after_config = lambda: object.__setattr__(
        mutated["build_policy"], "policy_binding_sha256", f"sha256:{'0' * 64}"
    )
    with pytest.raises(
        authentication.RuntimeImagePublicationFailure, match="authority_changed"
    ):
        _authenticate(mutated)


@pytest.mark.parametrize(
    ("field", "value"),
    [("trusted_root_sha256", f"sha256:{'0' * 64}"), ("schema_version", True)],
)
def test_output_seal_rejects_fake_subclass_and_tampered_receipt(
    field: str, value: Any
) -> None:
    result = _authenticate(_case())
    receipt = result.receipt
    receipt[field] = value
    changed = authentication._canonical(receipt)
    object.__setattr__(result, "_receipt_bytes", changed)
    object.__setattr__(result, "_receipt_sha256", authentication._digest(changed))
    assert not result._is_authenticated()
    with pytest.raises(authentication.RuntimeImagePublicationFailure):
        _ = result.publication
    with pytest.raises(authentication.RuntimeImagePublicationFailure):
        authentication.AuthenticatedRuntimeImagePublicationEvidence()

    class Fake(authentication.AuthenticatedRuntimeImagePublicationEvidence):
        pass

    fake = object.__new__(Fake)
    for name in authentication.AuthenticatedRuntimeImagePublicationEvidence.__slots__:
        object.__setattr__(fake, name, getattr(result, name))
    assert not fake._is_authenticated()


@pytest.mark.parametrize(
    ("delegate", "code"),
    [("transport", "registry"), ("verifier_delegate", "verifier")],
)
def test_errors_are_bounded_and_module_has_no_default_io(
    delegate: str, code: str
) -> None:
    case = _case()
    case[delegate].error = authentication.RuntimeImagePublicationFailure(
        "secret-provider-detail"
    )
    with pytest.raises(
        authentication.RuntimeImagePublicationFailure,
        match=rf"^runtime_image\.{code}_unavailable$",
    ) as caught:
        _authenticate(case)
    assert "secret-provider-detail" not in str(caught.value)
    tree = ast.parse(inspect.getsource(authentication))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint(
        {"os", "pathlib", "requests", "socket", "subprocess", "urllib"}
    )
    signature = inspect.signature(authentication.authenticate_runtime_image_publication)
    assert signature.parameters["registry_reader"].default is inspect.Parameter.empty
    assert signature.parameters["verifier"].default is inspect.Parameter.empty
    assert signature.parameters["clock"].default is inspect.Parameter.empty

    class ExplodingAdapter:
        def __getattribute__(self, _name: str) -> Any:
            raise authentication.RuntimeImagePublicationFailure("secret-adapter-detail")

    for constructor, error in (
        (authentication.OneShotExactRegistryImageReader, "reader_invalid"),
        (authentication.OneShotOfflineAttestationVerifier, "verifier_invalid"),
    ):
        with pytest.raises(authentication.RuntimeImagePublicationFailure, match=error):
            constructor(cast(Any, ExplodingAdapter()))


def test_current_receiver_refuses_future_proof_claims() -> None:
    for section, field in (
        ("verifier_profile", "publisher_containment_proven"),
        ("publication", "image_build_provenance_proven"),
    ):
        case = _case()
        policy = deepcopy(case["policy"])
        policy["private_registry"]["image_publication_authentication"][section][
            field
        ] = True
        case["build_policy"] = binding.validate_runtime_record_build_policy(policy)
        with pytest.raises(
            authentication.RuntimeImagePublicationFailure,
            match="runtime_image.policy_invalid",
        ):
            _authenticate(case)
        assert case["transport"].manifest_calls == []


@pytest.mark.parametrize("reuse_executor", [False, True])
def test_publisher_containment_evidence_is_unique_and_separate(
    reuse_executor: bool,
) -> None:
    policy = helpers._configured_policy()
    evidence = policy["private_registry"]["image_publication_authentication"][
        "verifier_profile"
    ]["publisher_containment"]["evidence"]
    source = (
        next(iter(policy["offline_github_verifier"]["containment_evidence"].values()))
        if reuse_executor
        else next(iter(evidence.values()))
    )
    evidence["review_sha256"] = source
    with pytest.raises(
        binding.RuntimeBindingFailure,
        match="private_registry.image_publication_authentication",
    ):
        binding.validate_runtime_binding_policy(policy)


def test_image_attestor_identity_must_remain_separate_from_record_signer() -> None:
    policy = helpers._configured_policy()
    policy["private_registry"]["image_publication_authentication"]["certificate"][
        "identity"
    ] = policy["external_runtime_record"]["signature"]["certificate_identity"]
    with pytest.raises(binding.RuntimeBindingFailure):
        binding.validate_runtime_binding_policy(policy)


def test_verifier_identity_is_exact_and_failure_text_is_bounded() -> None:
    case = _case()
    case["verifier_delegate"].digest = f"sha256:{'0' * 64}"
    with pytest.raises(
        authentication.RuntimeImagePublicationFailure,
        match="^runtime_image.verifier_invalid$",
    ):
        _authenticate(case)
    assert case["events"] == ["manifest", "config", "verifier"]


@pytest.mark.parametrize(
    "malformed",
    [
        b'{"mediaType":"a","mediaType":"b"}',
        b'{"mediaType":NaN}',
        b"[" * 66 + b"0" + b"]" * 66,
        b"x" * (2_097_152 + 1),
    ],
    ids=("duplicate", "nonfinite", "deep", "oversized"),
)
def test_malformed_or_oversized_bundle_never_reaches_callbacks(
    malformed: bytes,
) -> None:
    case = _case()
    case["bundle_bytes"] = malformed
    with pytest.raises(authentication.RuntimeImagePublicationFailure):
        _authenticate(case)
    assert case["transport"].manifest_calls == []
    assert case["verifier_delegate"].calls == []


def test_checked_in_policy_keeps_all_25_gates_false_and_proofs_unset() -> None:
    policy = json.loads(binding.POLICY_PATH.read_bytes())
    image_auth = policy["private_registry"]["image_publication_authentication"]
    assert len(policy["activation"]) == 25
    assert policy["activation"] == dict.fromkeys(policy["activation"], False)
    assert image_auth["receiver_implemented"] is True
    assert image_auth["verifier_profile"]["executor_invocation_plan_applies"] is False
    assert image_auth["verifier_profile"]["publisher_containment_proven"] is False
    assert image_auth["publication"]["image_build_provenance_proven"] is False
    assert (
        image_auth["certificate"]["identity"]
        != policy["external_runtime_record"]["signature"]["certificate_identity"]
    )
