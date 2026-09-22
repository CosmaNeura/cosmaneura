from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import pytest

from scripts import read_staging_controller_receipt_v2_prefix as prefix_reader
from scripts import staging_restore_launch_material as launch_material
from scripts import validate_staging_controller_receipt_v2 as content
from scripts import validate_staging_controller_receipt_v2_production as production
from scripts import validate_staging_restore_launch_binding as launch
from scripts import validate_staging_restore_runtime_binding as runtime_binding

from tests.infrastructure import (
    test_staging_broker_launch_receipt_authentication as auth_helpers,
)
from tests.infrastructure import (
    test_staging_broker_launch_receipt_mailbox as mailbox_helpers,
)
from tests.infrastructure import (
    test_staging_controller_receipt_v2_private_reader as ingress_helpers,
)
from tests.infrastructure import test_staging_restore_runtime_binding as runtime_helpers
from tests.infrastructure import test_staging_runtime_record_private_readers as wire

NOW = datetime(2026, 9, 5, 12, 10, tzinfo=UTC)
ROOT_BYTES = b'{"trusted":"root"}'
Failure = production.ControllerReceiptV2ProductionFailure


@contextmanager
def _no_deadline(_seconds: int) -> Iterator[None]:
    yield


def _launch_material(
    monkeypatch: pytest.MonkeyPatch,
    runtime: dict[str, Any],
    controller: dict[str, Any],
) -> tuple[
    launch_material.ProvisionLaunchMaterial,
    launch.ValidatedLaunchRecord,
]:
    root = runtime["evidence_ingress"]["private_versioned_secret_source"]["secrets"][
        "github_trusted_root"
    ]
    root["sha256"] = runtime_binding._digest(ROOT_BYTES)
    runtime_bytes, provision_ref, _cleanup_ref, signature = (
        runtime_helpers._external_binding(runtime)
    )
    runtime_record = runtime_binding._parse_policy(runtime_bytes)
    _unused, intent, _submission, _receipt = ingress_helpers.content_helpers._fixture()
    rehearsal_id = intent["rehearsal_id"]
    _old_receipt, old_record = ingress_helpers._launch_authority(
        monkeypatch,
        runtime,
        action="provision",
        rehearsal_id=rehearsal_id,
    )
    record_document = runtime_binding._parse_policy(old_record.record_bytes)
    record_document["runtime_record_sha256"] = provision_ref.record_sha256
    evidence = intent["evidence_binding"]
    record_document["payload"]["evidence_files"]["SHA256SUMS"]["sha256"] = evidence[
        "bundle_index_sha256"
    ]
    record_document["payload"]["evidence_files"]["evidence-manifest.json"]["sha256"] = (
        evidence["manifest_file_sha256"]
    )
    created = content._utc(intent["created_at"])
    cleanup = content._utc(intent["cleanup_not_after"])
    ttl_hours = int((cleanup - created).total_seconds() // 3600)
    assert cleanup == created + timedelta(hours=ttl_hours)
    record_document["payload"]["ttl_hours"] = ttl_hours
    launch_created = content._utc(record_document["created_at"])
    record_document["expires_at"] = (
        launch_created + timedelta(hours=ttl_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    record_bytes = runtime_binding._canonical(record_document)
    record_sha256 = runtime_binding._digest(record_bytes)
    mailbox = runtime["launch_binding"]["mailbox"]
    record_uri = (
        f"https://{mailbox['account_host']}/{mailbox['container_names']['provision']}/"
        f"{record_sha256[7:]}.json"
    )
    record = launch.validate_launch_record(
        runtime,
        record_bytes,
        record_sha256=record_sha256,
        record_uri=record_uri,
    )
    broker = runtime_binding._parse_policy(
        mailbox_helpers._receipt(runtime, "provision")
    )
    broker.update(
        {
            "launch_record_uri": record_uri,
            "launch_record_sha256": record_sha256,
            "runtime_record_uri": provision_ref.record_uri,
            "runtime_record_sha256": provision_ref.record_sha256,
            "runtime_signature_bundle_uri": provision_ref.signature_bundle_uri,
            "runtime_signature_bundle_sha256": (provision_ref.signature_bundle_sha256),
            "provision_job_projection_sha256": runtime["runtime"]["jobs"]["provision"][
                "configuration_template_sha256"
            ],
            "provision_job_image_reference": runtime_record[
                "provision_job_image_reference"
            ],
            "cleanup_job_projection_sha256": runtime["runtime"]["jobs"]["cleanup"][
                "configuration_template_sha256"
            ],
            "cleanup_job_image_reference": runtime_record[
                "cleanup_job_image_reference"
            ],
            "execution_template_sha256": broker[
                "provision_job_execution_template_sha256"
            ],
            "start_client_request_id": launch._client_request_id(record_sha256),
            "execution_resource_id": (
                f"{runtime['runtime']['jobs']['provision']['resource_id']}/executions/"
                f"{mailbox_helpers.EXECUTION_NAME}"
            ),
            "observed_at": "2026-09-05T12:03:00Z",
        }
    )
    receipt_bytes = runtime_binding._canonical(broker)
    receipt = mailbox_helpers._receive(
        monkeypatch,
        runtime,
        mailbox_helpers.FakeTransport(
            {
                "receipt": receipt_bytes,
                "signature_envelope": auth_helpers._envelope(runtime, receipt_bytes),
            }
        ),
        mailbox_helpers.Clock(),
        wall_clock=lambda: datetime(2026, 9, 5, 12, 4, tzinfo=UTC),
    )
    authenticated_runtime = (
        launch_material.AuthenticatedRuntimeRecordMaterial._from_authenticated_read(
            record_bytes=runtime_bytes,
            trusted_root_bytes=ROOT_BYTES,
            signature_verification=signature,
        )
    )
    return (
        launch_material.ProvisionLaunchMaterial(
            authenticated_receipt=receipt,
            launch_record_bytes=record_bytes,
            authenticated_runtime_record=authenticated_runtime,
        ),
        record,
    )


def _case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    now: datetime = NOW,
    response_factory: Any = None,
) -> dict[str, Any]:
    runtime, controller = ingress_helpers._configured_policies()
    material, record = _launch_material(monkeypatch, runtime, controller)
    records = ingress_helpers._records(
        runtime, controller, material.authenticated_receipt, record
    )
    identity = wire.Opener(
        [wire.Response(ingress_helpers._token(runtime, "provision"))],
        name="identity",
        events=[],
    )
    factory = response_factory or (lambda _kind, raw: ingress_helpers.Response(raw))
    blob = ingress_helpers.Opener(
        [factory(kind, records[kind]) for kind in production.PREFIX_RECORD_KINDS]
    )
    reader_type = prefix_reader.ManagedIdentityPrivateControllerReceiptV2PrefixReader
    reader = reader_type._from_reviewed_configuration(
        runtime_policy=runtime,
        controller_policy=controller,
        production_policy=production.load_controller_receipt_v2_production_policy(),
        clock=lambda: now,
        identity_opener=identity,
        blob_opener=blob,
        epoch_clock=lambda: ingress_helpers.EPOCH_NOW,
        monotonic=lambda: 10.0,
        deadline_factory=_no_deadline,
    )
    monkeypatch.setenv("IDENTITY_ENDPOINT", wire.IDENTITY_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", wire.IDENTITY_HEADER)
    monkeypatch.setenv(launch.EXECUTION_NAME_ENV, mailbox_helpers.EXECUTION_NAME)
    return {
        "runtime": runtime,
        "controller": controller,
        "material": material,
        "record": record,
        "records": records,
        "identity": identity,
        "blob": blob,
        "reader": reader,
    }


def _read(case: dict[str, Any]) -> production.ControllerReceiptV2ProductionAuthority:
    return case["reader"].read_and_bind(launch_material_value=case["material"])


def _replace_authenticated_receipt(
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, Any],
    changes: dict[str, Any],
    *,
    launch_record_bytes: bytes | None = None,
    observed_at: datetime = datetime(2026, 9, 5, 12, 4, tzinfo=UTC),
) -> None:
    broker = runtime_binding._parse_policy(
        case["material"].authenticated_receipt.receipt_bytes
    )
    broker.update(changes)
    receipt_bytes = runtime_binding._canonical(broker)
    receipt = mailbox_helpers._receive(
        monkeypatch,
        case["runtime"],
        mailbox_helpers.FakeTransport(
            {
                "receipt": receipt_bytes,
                "signature_envelope": auth_helpers._envelope(
                    case["runtime"], receipt_bytes
                ),
            }
        ),
        mailbox_helpers.Clock(),
        wall_clock=lambda: observed_at,
    )
    material = case["material"]
    case["material"] = launch_material.ProvisionLaunchMaterial(
        authenticated_receipt=receipt,
        launch_record_bytes=(
            material.launch_record_bytes
            if launch_record_bytes is None
            else launch_record_bytes
        ),
        authenticated_runtime_record=material.authenticated_runtime_record,
    )


def _replace_launch_record(
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, Any],
    record_document: dict[str, Any],
    *,
    broker_changes: dict[str, Any] | None = None,
    observed_at: datetime = datetime(2026, 9, 5, 12, 4, tzinfo=UTC),
) -> None:
    record_bytes = runtime_binding._canonical(record_document)
    record_sha256 = runtime_binding._digest(record_bytes)
    mailbox = case["runtime"]["launch_binding"]["mailbox"]
    record_uri = (
        f"https://{mailbox['account_host']}/{mailbox['container_names']['provision']}/"
        f"{record_sha256[7:]}.json"
    )
    record = launch.validate_launch_record(
        case["runtime"],
        record_bytes,
        record_sha256=record_sha256,
        record_uri=record_uri,
    )
    _replace_authenticated_receipt(
        monkeypatch,
        case,
        {
            "launch_record_uri": record_uri,
            "launch_record_sha256": record_sha256,
            "start_client_request_id": launch._client_request_id(record_sha256),
            **(broker_changes or {}),
        },
        launch_record_bytes=record_bytes,
        observed_at=observed_at,
    )
    case["record"] = record


def _replace_reader(
    case: dict[str, Any],
    *,
    now: datetime,
    controller: dict[str, Any] | None = None,
    response_factory: Any = None,
) -> None:
    controller = controller or case["controller"]
    identity = wire.Opener(
        [wire.Response(ingress_helpers._token(case["runtime"], "provision"))],
        name="identity",
        events=[],
    )
    factory = response_factory or (lambda _kind, raw: ingress_helpers.Response(raw))
    blob = ingress_helpers.Opener(
        [
            factory(kind, case["records"][kind])
            for kind in production.PREFIX_RECORD_KINDS
        ]
    )
    case.update(
        {
            "controller": controller,
            "identity": identity,
            "blob": blob,
            "reader": prefix_reader.ManagedIdentityPrivateControllerReceiptV2PrefixReader._from_reviewed_configuration(
                runtime_policy=case["runtime"],
                controller_policy=controller,
                production_policy=production.load_controller_receipt_v2_production_policy(),
                clock=lambda: now,
                identity_opener=identity,
                blob_opener=blob,
                epoch_clock=lambda: ingress_helpers.EPOCH_NOW,
                monotonic=lambda: 10.0,
                deadline_factory=_no_deadline,
            ),
        }
    )


def test_reads_only_exact_prefix_and_mints_narrow_provision_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    result = _read(case)
    document = result.authority
    source = case["controller"]["record_chain_source"]
    expected_uris = [
        f"https://{source['account_host']}/{source['container_names'][kind]}/"
        f"{result.rehearsal_id}.json"
        for kind in production.PREFIX_RECORD_KINDS
    ]

    assert result._is_authenticated()
    assert result.intent_bytes == case["records"]["intent"]
    assert result.submission_bytes == case["records"]["submission"]
    assert result.record_source_authenticated is True
    assert result.controller_receipt_exists is False
    assert result.provider_facts_authenticated is False
    assert result.runtime_authority is False
    assert result.writer_identity_or_private_route_proven is False
    assert result.executable is False
    assert document["launch"]["execution_resource_id"] == result.execution_binding
    assert document["source"]["workflow_sha"] == case["record"].workflow_sha
    assert (
        document["identity"]["provision_job_uami_resource_id"]
        == case["runtime"]["identities"]["jobs"]["provision"]["resource_id"]
    )
    assert len(case["identity"].calls) == 1
    assert [call[0].full_url for call in case["blob"].calls] == expected_uris
    assert all(urlsplit(uri).query == "" for uri in expected_uris)
    assert "controller_receipt" not in " ".join(expected_uris)
    assert ingress_helpers.TOKEN not in repr(result)


def test_data_plane_api_has_no_caller_selectors_and_public_factory_is_dormant() -> None:
    assert set(
        inspect.signature(
            prefix_reader.ManagedIdentityPrivateControllerReceiptV2PrefixReader.read_and_bind
        ).parameters
    ) == {"self", "launch_material_value"}
    assert set(
        inspect.signature(
            prefix_reader.build_private_controller_receipt_v2_prefix_reader
        ).parameters
    ) == {"clock"}
    with pytest.raises(Failure, match="policy_disabled"):
        prefix_reader.build_private_controller_receipt_v2_prefix_reader(
            clock=lambda: NOW
        )


def test_companion_policy_is_exact_downstream_and_all_activation_stays_dark() -> None:
    policy = production.load_controller_receipt_v2_production_policy()
    assert policy["activation_ready"] is False
    assert policy["activation"]["prefix_reader_implemented"] is True
    assert policy["activation"]["production_authority_implemented"] is True
    assert all(
        value is False
        for name, value in policy["activation"].items()
        if name not in {"prefix_reader_implemented", "production_authority_implemented"}
    )
    assert policy["upstream_policies"]["controller_receipt_v2"]["sha256"] == (
        f"sha256:{content.POLICY_SHA256}"
    )
    assert policy["upstream_policies"]["restore_runtime_binding"]["sha256"] == (
        f"sha256:{runtime_binding.CANONICAL_POLICY_SHA256}"
    )
    changed = deepcopy(policy)
    changed["activation"]["live_provider_calls_enabled"] = True
    with pytest.raises(Failure, match="policy_invalid"):
        production.validate_controller_receipt_v2_production_policy(changed)
    with pytest.raises(Failure, match="policy_disabled"):
        production.validate_controller_receipt_v2_production_policy(
            policy, executable=True
        )


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("Content-MD5", "AAAA"),
        ("ETag", 'W/"weak"'),
        ("x-ms-immutability-policy-mode", "Unlocked"),
        ("x-ms-legal-hold", "false"),
        ("x-ms-server-encrypted", "false"),
    ],
)
def test_prefix_worm_metadata_fails_closed_without_reading_receipt(
    monkeypatch: pytest.MonkeyPatch, header: str, value: str
) -> None:
    case = _case(
        monkeypatch,
        response_factory=lambda kind, raw: ingress_helpers.Response(
            raw, replace=(header, value) if kind == "intent" else None
        ),
    )
    with pytest.raises(Failure, match="record_invalid"):
        _read(case)
    assert len(case["blob"].calls) == 1
    with pytest.raises(Failure, match="already_used"):
        _read(case)


@pytest.mark.parametrize("mutation", ("submission", "identity", "source"))
def test_prefix_cross_bindings_refuse_before_authority(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    case = _case(monkeypatch)
    if mutation == "submission":
        value = runtime_binding._parse_policy(case["records"]["submission"])
        value["intent_sha256"] = f"sha256:{'f' * 64}"
        case["blob"].responses[1] = ingress_helpers.Response(content._canonical(value))
    elif mutation == "identity":
        value = runtime_binding._parse_policy(case["records"]["intent"])
        value["provision_client_id"] = "90000000-0000-4000-8000-000000000009"
        case["blob"].responses[0] = ingress_helpers.Response(content._canonical(value))
    else:
        value = runtime_binding._parse_policy(case["records"]["intent"])
        value["run_id"] = "999"
        case["blob"].responses[0] = ingress_helpers.Response(content._canonical(value))
    with pytest.raises(Failure, match="prefix_invalid"):
        _read(case)
    assert len(case["blob"].calls) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_template_sha256", f"sha256:{'9' * 64}"),
        ("cleanup_job_projection_sha256", f"sha256:{'8' * 64}"),
        (
            "cleanup_job_image_reference",
            f"wrong.azurecr.io/restore/cleanup@sha256:{'7' * 64}",
        ),
        ("start_client_request_id", "00000000-0000-4000-8000-000000000000"),
    ],
)
def test_canonical_broker_and_execution_bindings_fail_closed(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    case = _case(monkeypatch)
    _replace_authenticated_receipt(monkeypatch, case, {field: value})

    with pytest.raises(Failure, match="launch_invalid"):
        _read(case)
    assert case["identity"].calls == case["blob"].calls == []


@pytest.mark.parametrize("logical_name", ("SHA256SUMS", "evidence-manifest.json"))
def test_launch_raw_evidence_digest_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch, logical_name: str
) -> None:
    case = _case(monkeypatch)
    record = runtime_binding._parse_policy(case["material"].launch_record_bytes)
    record["payload"]["evidence_files"][logical_name]["sha256"] = f"sha256:{'f' * 64}"
    _replace_launch_record(monkeypatch, case, record)

    with pytest.raises(Failure, match="prefix_invalid"):
        _read(case)
    assert len(case["blob"].calls) == 2


def test_launch_ttl_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(monkeypatch)
    record = runtime_binding._parse_policy(case["material"].launch_record_bytes)
    record["payload"]["ttl_hours"] = 1
    record["expires_at"] = (
        content._utc(record["created_at"]) + timedelta(hours=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    _replace_launch_record(monkeypatch, case, record)

    with pytest.raises(Failure, match="prefix_invalid"):
        _read(case)
    assert len(case["blob"].calls) == 2


def test_configured_subscription_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    controller = deepcopy(case["controller"])
    controller["fixed_bindings"]["subscription_id"] = (
        "90000000-0000-4000-8000-000000000009"
    )
    _replace_reader(case, now=NOW, controller=controller)

    with pytest.raises(Failure, match="prefix_invalid"):
        _read(case)
    assert len(case["blob"].calls) == 2


def test_future_intent_beyond_clock_skew_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    record = runtime_binding._parse_policy(case["material"].launch_record_bytes)
    record["created_at"] = "2026-09-05T11:50:00Z"
    record["expires_at"] = "2026-09-05T17:50:00Z"
    _replace_launch_record(
        monkeypatch,
        case,
        record,
        broker_changes={"observed_at": "2026-09-05T11:52:00Z"},
        observed_at=datetime(2026, 9, 5, 11, 53, tzinfo=UTC),
    )
    _replace_reader(
        case,
        now=datetime(2026, 9, 5, 11, 58, tzinfo=UTC),
        response_factory=lambda _kind, raw: ingress_helpers.Response(
            raw,
            replace=("Last-Modified", "Sat, 05 Sep 2026 11:57:00 GMT"),
        ),
    )

    with pytest.raises(Failure, match="prefix_invalid"):
        _read(case)
    assert len(case["blob"].calls) == 2


@pytest.mark.parametrize(
    ("now", "last_modified"),
    [
        (NOW, "Sat, 05 Sep 2026 11:55:00 GMT"),
        (datetime(2026, 9, 5, 12, 30, tzinfo=UTC), "Sat, 05 Sep 2026 12:20:00 GMT"),
    ],
)
def test_worm_write_chronology_fails_closed(
    monkeypatch: pytest.MonkeyPatch, now: datetime, last_modified: str
) -> None:
    case = _case(monkeypatch)
    _replace_reader(
        case,
        now=now,
        response_factory=lambda _kind, raw: ingress_helpers.Response(
            raw,
            replace=("Last-Modified", last_modified),
        ),
    )

    with pytest.raises(Failure, match="prefix_invalid"):
        _read(case)
    assert len(case["blob"].calls) == 2


def test_launch_runtime_and_execution_are_rechecked_around_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)

    def mutate() -> None:
        object.__setattr__(
            case["material"].authenticated_receipt,
            "execution_name",
            "changed-execution",
        )

    case["identity"].effects = [mutate]
    with pytest.raises(Failure, match="launch_invalid"):
        _read(case)
    assert case["blob"].calls == []


def test_seals_detect_prefix_authority_and_runtime_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(Failure, match="prefix_unsealed"):
        production.AuthenticatedControllerReceiptV2PrefixSource()
    with pytest.raises(Failure, match="authority_unsealed"):
        production.ControllerReceiptV2ProductionAuthority()
    case = _case(monkeypatch)
    result = _read(case)
    object.__setattr__(result, "authority_bytes", b"{}")
    assert not result._is_authenticated()

    case = _case(monkeypatch)
    result = _read(case)
    object.__setattr__(result._prefix._objects[0], "body", b"{}")
    assert not result._is_authenticated()

    case = _case(monkeypatch)
    result = _read(case)
    object.__setattr__(
        result._launch_material.authenticated_runtime_record,
        "record_bytes",
        b"{}",
    )
    assert not result._is_authenticated()


def test_expired_launch_refuses_before_identity_or_blob_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch, now=datetime(2026, 9, 5, 18, 2, tzinfo=UTC))
    with pytest.raises(Failure, match="launch_invalid"):
        _read(case)
    assert case["identity"].calls == case["blob"].calls == []
