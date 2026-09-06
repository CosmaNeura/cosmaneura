from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import validate_staging_controller_receipt_v2 as receipt_v2

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_POLICY = (
    ROOT / "contracts/staging-rehearsal/v1/restore-runtime-binding-policy.json"
)
NOW = datetime(2026, 9, 5, 12, 10, tzinfo=UTC)
SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
TENANT = "22222222-2222-4222-8222-222222222222"
PROVISION_CLIENT = "33333333-3333-4333-8333-333333333333"
PROVISION_PRINCIPAL = "44444444-4444-4444-8444-444444444444"
CONTROL_GROUP = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/cosmaneura-staging-restore-control"
)
SOURCE_GROUP = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/cosmaneura-staging"
SOURCE_SERVER = (
    f"{SOURCE_GROUP}/providers/Microsoft.DBforPostgreSQL/flexibleServers/staging-source"
)
TARGET_GROUP = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/cosmaneura-staging-rehearsal"
)


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _rid(group: str, namespace: str, *segments: str) -> str:
    return f"{group}/providers/{namespace}/" + "/".join(segments)


def _configured_policy() -> dict[str, Any]:
    policy = receipt_v2.load_controller_receipt_v2_policy()
    provision_job = _rid(CONTROL_GROUP, "Microsoft.App", "jobs", "restore-provision")
    provision_uami = _rid(
        CONTROL_GROUP,
        "Microsoft.ManagedIdentity",
        "userAssignedIdentities",
        "restore-provision",
    )
    policy["record_chain_source"]["writer_identity_resource_id"] = provision_uami
    policy["record_chain_source"]["reader_identity_resource_ids"]["provision"] = (
        provision_uami
    )
    policy["fixed_bindings"]["approved_database_role"] = "restore_sanitizer"
    policy["fixed_bindings"]["executor_resource_group_id"] = CONTROL_GROUP
    policy["fixed_bindings"]["source_server_resource_id"] = SOURCE_SERVER
    policy["fixed_bindings"]["target_resource_group_id"] = TARGET_GROUP
    policy["fixed_bindings"].update(
        {
            "tenant_id": TENANT,
            "subscription_id": SUBSCRIPTION,
            "provision_job_resource_id": provision_job,
            "provision_job_uami_client_id": PROVISION_CLIENT,
            "provision_job_uami_principal_id": PROVISION_PRINCIPAL,
        }
    )
    return policy


def _evidence() -> dict[str, Any]:
    def digest(char: str) -> str:
        return f"sha256:{char * 64}"

    return {
        "schema_version": 1,
        "binding_policy_id": "cosmaneura-staging-restore-evidence-binding-v1",
        "evidence_manifest_policy_id": "cosmaneura-staging-evidence-manifest-v2",
        "evidence_manifest_policy_sha256": "sha256:8b1cd30a90782d5596b44415e0abb9e934c78aeaca9bc7960e226d1feee8df53",
        "manifest_id": "staging-proof-20260905",
        "manifest_file_sha256": digest("2"),
        "bundle_index_sha256": digest("3"),
        "evidence_set_sha256": digest("4"),
        "source_sha": "a" * 40,
        "candidate_sha": "b" * 40,
        "source_server_resource_id": SOURCE_SERVER,
        "source_database_name": "staging",
        "approved_restore_time": "2026-09-05T12:00:00Z",
        "observed_at": "2026-09-05T12:00:00Z",
        "expires_at": "2026-09-05T12:15:00Z",
        "candidate_approval_pr_number": 313,
        "candidate_protected_master_sha": "c" * 40,
        "candidate_schema_revision": "9a2399212488",
        "candidate_receipt_sha256": digest("5"),
        "inventory_receipt_sha256": digest("6"),
        "quiescence_receipt_sha256": digest("7"),
        "server_scope_receipt_sha256": digest("8"),
        "repository": "CosmaNeura/cosmaneura",
        "protected_ref": "refs/heads/master",
        "workflow_path": ".github/workflows/staging_restore_rehearsal.yml",
        "workflow_sha": "c" * 40,
        "run_id": "33990000001",
        "run_attempt": "1",
        "event_name": "workflow_dispatch",
        "signer_issuer": "https://token.actions.githubusercontent.com",
        "signer_subject": "repo:CosmaNeura/cosmaneura:environment:staging-restore-provision",
        "attestation_receipt_sha256": digest("9"),
        "provider_receipt_sha256": digest("a"),
        "server_scope_authentication_receipt_sha256": digest("b"),
    }


def _target(rehearsal_id: str, cleanup_epoch: int) -> dict[str, Any]:
    name = f"cnr-{rehearsal_id}-e{cleanup_epoch}-pg"
    vnet = _rid(TARGET_GROUP, "Microsoft.Network", "virtualNetworks", "restore-vnet")
    target_pe = _rid(TARGET_GROUP, "Microsoft.Network", "privateEndpoints", "target-pe")
    target_zone = _rid(
        TARGET_GROUP,
        "Microsoft.Network",
        "privateDnsZones",
        "privatelink.postgres.database.azure.com",
    )
    executor_pe = _rid(
        CONTROL_GROUP, "Microsoft.Network", "privateEndpoints", "executor-pe"
    )
    executor_zone = _rid(
        CONTROL_GROUP,
        "Microsoft.Network",
        "privateDnsZones",
        "executor.internal",
    )
    values = {
        "target_fqdn": f"{name}.postgres.database.azure.com",
        "approved_database_role": "restore_sanitizer",
        "target_postgres_server_id": _rid(
            TARGET_GROUP,
            "Microsoft.DBforPostgreSQL",
            "flexibleServers",
            name,
        ),
        "target_vnet_id": vnet,
        "target_subnet_id": f"{vnet}/subnets/private-endpoints",
        "target_private_endpoint_id": target_pe,
        "target_network_interface_id": _rid(
            TARGET_GROUP,
            "Microsoft.Network",
            "networkInterfaces",
            "target-nic",
        ),
        "target_private_dns_zone_id": target_zone,
        "target_private_dns_link_id": f"{target_zone}/virtualNetworkLinks/target-link",
        "target_private_dns_zone_group_id": f"{target_pe}/privateDnsZoneGroups/default",
        "executor_private_endpoint_id": executor_pe,
        "executor_network_interface_id": _rid(
            CONTROL_GROUP,
            "Microsoft.Network",
            "networkInterfaces",
            "executor-nic",
        ),
        "executor_private_dns_zone_id": executor_zone,
        "executor_private_dns_link_id": f"{executor_zone}/virtualNetworkLinks/executor-link",
        "executor_private_dns_zone_group_id": f"{executor_pe}/privateDnsZoneGroups/default",
    }
    values["resource_set_sha256"] = _digest(
        receipt_v2._canonical(
            {name: values[name] for name in receipt_v2.RUNTIME_FIELDS}
        )
    )
    return values


def _fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    policy = _configured_policy()
    evidence = _evidence()
    safe_id = policy["upstream_policies"]["safe_submit"]["policy_id"]
    authorization_id = _digest(
        receipt_v2._canonical(
            {
                "schema_version": 1,
                "safe_submit_policy_id": safe_id,
                "evidence_binding": evidence,
            }
        )
    )
    rehearsal_id = authorization_id[7:19]
    cleanup = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)
    cleanup_text = cleanup.isoformat().replace("+00:00", "Z")
    target = _target(rehearsal_id, int(cleanup.timestamp()))
    intent = {
        "schema_version": 4,
        "policy_id": safe_id,
        "envelope_policy_id": policy["upstream_policies"]["restore_envelope"][
            "policy_id"
        ],
        "envelope_policy_sha256": policy["upstream_policies"]["restore_envelope"][
            "sha256"
        ],
        "authorization_id": authorization_id,
        "evidence_binding": evidence,
        "rehearsal_id": rehearsal_id,
        "source_server_resource_id": evidence["source_server_resource_id"],
        "provision_client_id": PROVISION_CLIENT,
        "provision_principal_id": PROVISION_PRINCIPAL,
        "target_server_resource_id": target["target_postgres_server_id"],
        "target_server_name": target["target_fqdn"].split(".", 1)[0],
        "location": "northcentralus",
        "restore_time": evidence["approved_restore_time"],
        "created_at": evidence["observed_at"],
        "cleanup_not_after": cleanup_text,
        "repository": evidence["repository"],
        "workflow_sha": evidence["workflow_sha"],
        "run_id": evidence["run_id"],
        "run_attempt": evidence["run_attempt"],
        "required_tags": {
            "environment": "staging-restore-rehearsal",
            "data-classification": "restricted-restored-copy",
            "managed-by": "staging-restore-envelope-v1",
            "expires-epoch": str(int(cleanup.timestamp())),
            "rehearsal-id": rehearsal_id,
        },
    }
    intent_sha256 = _digest(receipt_v2._canonical(intent))
    submission = {
        "schema_version": 4,
        "kind": "restore-submission-authorized",
        "policy_id": safe_id,
        "rehearsal_id": rehearsal_id,
        "intent_sha256": intent_sha256,
        "target_server_resource_id": target["target_postgres_server_id"],
        "restore_request_sha256": receipt_v2._restore_request_sha256(
            intent, intent_sha256
        ),
        "authorized_at": intent["created_at"],
    }
    provision_job = _rid(CONTROL_GROUP, "Microsoft.App", "jobs", "restore-provision")
    execution_name = "restore-provision-abc123"
    identity = {
        "tenant_id": TENANT,
        "subscription_id": SUBSCRIPTION,
        "provision_job_resource_id": provision_job,
        "provision_job_uami_resource_id": policy["record_chain_source"][
            "writer_identity_resource_id"
        ],
        "provision_job_uami_client_id": PROVISION_CLIENT,
        "provision_job_uami_principal_id": PROVISION_PRINCIPAL,
    }
    launch = {
        "launch_record_sha256": f"sha256:{'c' * 64}",
        "broker_launch_receipt_sha256": f"sha256:{'d' * 64}",
        "runtime_record_sha256": f"sha256:{'e' * 64}",
        "runtime_signature_bundle_sha256": f"sha256:{'f' * 64}",
        "execution_resource_id": f"{provision_job}/executions/{execution_name}",
        "execution_name": execution_name,
        "launch_expires_at": "2026-09-05T12:14:00Z",
        "provision_job_configuration_template_sha256": f"sha256:{'0' * 64}",
        "provision_job_image_reference": f"restoreexecutor.azurecr.io/cosmaneura/restore@sha256:{'1' * 64}",
    }
    receipt = {
        "schema_version": 2,
        "kind": "staging-provision-controller-receipt",
        "policy_id": policy["policy_id"],
        "policy_binding_sha256": receipt_v2._policy_binding_digest(policy),
        "repository": evidence["repository"],
        "policy_pins": {
            "safe_submit_policy_id": safe_id,
            "safe_submit_policy_sha256": policy["upstream_policies"]["safe_submit"][
                "sha256"
            ],
            "restore_envelope_policy_id": policy["upstream_policies"][
                "restore_envelope"
            ]["policy_id"],
            "restore_envelope_policy_sha256": policy["upstream_policies"][
                "restore_envelope"
            ]["sha256"],
        },
        "identity": identity,
        "authorization": {
            "rehearsal_id": rehearsal_id,
            "authorization_id": authorization_id,
            "evidence_binding_sha256": _digest(receipt_v2._canonical(evidence)),
            "intent_sha256": intent_sha256,
            "submission_sha256": _digest(receipt_v2._canonical(submission)),
            "restore_request_sha256": submission["restore_request_sha256"],
        },
        "launch": launch,
        "source": {name: evidence[name] for name in receipt_v2.SOURCE_FIELDS},
        "target": target,
        "restore_observed_at": "2026-09-05T12:05:00Z",
        "created_at": "2026-09-05T12:06:00Z",
        "expires_at": "2026-09-05T12:13:00Z",
        "cleanup_not_after": cleanup_text,
        "isolation": deepcopy(policy["fixed_bindings"]["isolation"]),
        "pass": True,
    }
    return policy, intent, submission, receipt


def _validate(
    policy: dict[str, Any],
    intent: dict[str, Any],
    submission: dict[str, Any],
    receipt: dict[str, Any],
    *,
    now: datetime = NOW,
) -> receipt_v2.ValidatedControllerReceiptV2Content:
    return receipt_v2.validate_controller_receipt_v2_content(
        policy,
        intent_bytes=receipt_v2._canonical(intent),
        submission_bytes=receipt_v2._canonical(submission),
        receipt_bytes=receipt_v2._canonical(receipt),
        clock=lambda: now,
    )


def _at(value: dict[str, Any], path: str) -> tuple[dict[str, Any], str]:
    parts = path.split(".")
    for part in parts[:-1]:
        value = value[part]
    return value, parts[-1]


def test_policy_is_pinned_dormant_non_authenticating_and_runtime_compatible() -> None:
    raw = receipt_v2.POLICY_PATH.read_bytes()
    policy = receipt_v2.load_controller_receipt_v2_policy()
    runtime = json.loads(RUNTIME_POLICY.read_bytes())
    contract = runtime["authenticated_controller_receipt"]

    assert hashlib.sha256(raw).hexdigest() == receipt_v2.POLICY_SHA256
    assert policy["status"]["executable"] is False
    assert policy["status"]["content_validator_implemented"] is True
    assert (
        policy["content_validation_boundary"]["source_provenance_authenticated"]
        is False
    )
    assert (
        policy["content_validation_boundary"]["provider_facts_authenticated"] is False
    )
    assert (
        policy["record_chain_source"]["caller_path_uri_or_mapping_permitted"] is False
    )
    assert contract["kind"] == "sealed-authenticated-controller-receipt-v2"
    assert contract["source"] == "exact-immutable-safe-submit-record-chain"
    assert contract["source_policy_id"] == "cosmaneura-staging-restore-safe-submit-v4"
    assert contract["content_contract_policy_id"] == policy["policy_id"]
    assert (
        contract["content_contract_policy_sha256"]
        == f"sha256:{receipt_v2.POLICY_SHA256}"
    )
    assert contract["required_runtime_fields"] == list(receipt_v2.RUNTIME_FIELDS)
    assert contract["existing_v1_receipt_satisfies_contract"] is False
    assert runtime["activation"]["authenticated_controller_receipt_bound"] is False
    isolation = policy["fixed_bindings"]["isolation"]
    assert set(isolation) == set(receipt_v2.ISOLATION_FIELDS)
    assert isolation["target_resource_group_storage_account_count"] == 0
    assert type(isolation["target_resource_group_storage_account_count"]) is int
    assert isolation["target_resource_group_redis_resource_count"] == 0
    assert type(isolation["target_resource_group_redis_resource_count"]) is int
    assert "blob_copied" not in isolation
    assert "redis_copied" not in isolation
    assert policy["observation_authority"]["isolation_count_semantics"] == {
        "fact_semantics": "point-in-time-count-not-execution-history",
        "inventory_scope": "fixed_bindings.target_resource_group_id",
        "resource_type_comparison": "ascii-case-insensitive",
        "resource_type_coverage": {
            "target_resource_group_redis_resource_count": [
                "Microsoft.Cache/Redis",
                "Microsoft.Cache/redisEnterprise",
            ],
            "target_resource_group_storage_account_count": [
                "Microsoft.Storage/storageAccounts"
            ],
        },
    }
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure, match="policy_disabled"):
        receipt_v2.validate_controller_receipt_v2_policy(policy, executable=True)


def test_valid_content_is_explicitly_not_authenticated_and_clock_is_read_once() -> None:
    policy, intent, submission, receipt = _fixture()
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return NOW

    result = receipt_v2.validate_controller_receipt_v2_content(
        policy,
        intent_bytes=receipt_v2._canonical(intent),
        submission_bytes=receipt_v2._canonical(submission),
        receipt_bytes=receipt_v2._canonical(receipt),
        clock=clock,
    )
    assert result.provenance_authenticated is False
    assert calls == 1
    assert all(
        getattr(result, name) == receipt["target"][name]
        for name in receipt_v2.RUNTIME_FIELDS
    )
    refreshed = receipt_v2.require_fresh_controller_receipt_v2_content(
        result, clock=lambda: NOW
    )
    assert refreshed is not result
    assert refreshed == result
    assert not any(name.startswith("Authenticated") for name in vars(receipt_v2))


def test_expired_content_retains_integrity_but_cannot_pass_provision_freshness() -> (
    None
):
    policy, intent, submission, receipt = _fixture()
    later = datetime(2026, 9, 5, 13, 0, tzinfo=UTC)
    validated = _validate(policy, intent, submission, receipt, now=later)
    assert validated.provenance_authenticated is False
    with pytest.raises(
        receipt_v2.ControllerReceiptV2Failure, match="^controller_receipt.expired$"
    ):
        receipt_v2.require_fresh_controller_receipt_v2_content(
            validated, clock=lambda: later
        )


def test_long_restore_after_historical_launch_and_evidence_expiry_is_valid() -> None:
    policy, intent, submission, receipt = _fixture()
    receipt.update(
        {
            "restore_observed_at": "2026-09-05T12:20:00Z",
            "created_at": "2026-09-05T12:21:00Z",
            "expires_at": "2026-09-05T12:30:00Z",
        }
    )
    now = datetime(2026, 9, 5, 12, 22, tzinfo=UTC)
    result = _validate(policy, intent, submission, receipt, now=now)
    refreshed = receipt_v2.require_fresh_controller_receipt_v2_content(
        result, clock=lambda: now
    )
    assert refreshed is not result
    assert refreshed == result


@pytest.mark.parametrize(
    ("section", "operation"),
    [
        ("top", "missing"),
        ("top", "unknown"),
        ("policy_pins", "missing"),
        ("identity", "unknown"),
        ("authorization", "missing"),
        ("launch", "unknown"),
        ("source", "missing"),
        ("target", "unknown"),
        ("isolation", "missing"),
    ],
)
def test_missing_and_unknown_fields_fail_closed(section: str, operation: str) -> None:
    policy, intent, submission, receipt = _fixture()
    target = receipt if section == "top" else receipt[section]
    target.pop(next(iter(target))) if operation == "missing" else target.update(
        unexpected=True
    )
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure, match="content_invalid"):
        _validate(policy, intent, submission, receipt)


@pytest.mark.parametrize("record", ["intent", "evidence", "submission"])
@pytest.mark.parametrize("operation", ["missing", "unknown"])
def test_record_chain_missing_and_unknown_fields_fail_closed(
    record: str, operation: str
) -> None:
    policy, intent, submission, receipt = _fixture()
    target = (
        intent["evidence_binding"]
        if record == "evidence"
        else intent
        if record == "intent"
        else submission
    )
    target.pop(next(iter(target))) if operation == "missing" else target.update(
        unexpected=True
    )
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure, match="content_invalid"):
        _validate(policy, intent, submission, receipt)


@pytest.mark.parametrize(
    "raw",
    [
        b' {"schema_version":2}',
        b'{"schema_version":2}\n',
        b'{"schema_version":2,"schema_version":2}',
        b'{"value":NaN}',
        b"[]",
        b"\xff",
        b"{}" * 32_769,
    ],
)
@pytest.mark.parametrize(
    "record", ["intent_bytes", "submission_bytes", "receipt_bytes"]
)
def test_noncanonical_duplicate_malformed_and_oversized_json_fail(
    raw: bytes, record: str
) -> None:
    policy, intent, submission, receipt = _fixture()
    inputs = {
        "intent_bytes": receipt_v2._canonical(intent),
        "submission_bytes": receipt_v2._canonical(submission),
        "receipt_bytes": receipt_v2._canonical(receipt),
    }
    inputs[record] = raw
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure, match="content_invalid"):
        receipt_v2.validate_controller_receipt_v2_content(
            policy,
            **inputs,
            clock=lambda: NOW,
        )


def test_repository_policy_file_digest_drift_fails_closed(monkeypatch: Any) -> None:
    monkeypatch.setattr(receipt_v2, "POLICY_SHA256", "0" * 64)
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure, match="policy_invalid"):
        receipt_v2.load_controller_receipt_v2_policy()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("policy_binding_sha256", f"sha256:{'9' * 64}"),
        ("policy_pins.safe_submit_policy_sha256", f"sha256:{'9' * 64}"),
        ("identity.tenant_id", "66666666-6666-4666-8666-666666666666"),
        (
            "identity.provision_job_uami_client_id",
            "55555555-5555-4555-8555-555555555555",
        ),
        ("identity.provision_job_resource_id", f"{CONTROL_GROUP}/invalid"),
        ("authorization.authorization_id", f"sha256:{'9' * 64}"),
        ("authorization.evidence_binding_sha256", f"sha256:{'9' * 64}"),
        ("authorization.intent_sha256", f"sha256:{'9' * 64}"),
        ("authorization.submission_sha256", f"sha256:{'9' * 64}"),
        ("authorization.restore_request_sha256", f"sha256:{'9' * 64}"),
        ("launch.runtime_record_sha256", "sha256:not-a-digest"),
        ("launch.execution_name", "different-execution"),
        ("source.workflow_sha", "d" * 40),
        ("source.source_server_resource_id", f"{CONTROL_GROUP}/invalid"),
        ("target.target_fqdn", "other.postgres.database.azure.com"),
        ("target.approved_database_role", "owner"),
        ("target.target_postgres_server_id", f"{TARGET_GROUP}/invalid"),
        (
            "target.target_subnet_id",
            _rid(
                CONTROL_GROUP,
                "Microsoft.Network",
                "virtualNetworks",
                "other",
                "subnets",
                "private",
            ),
        ),
        ("target.resource_set_sha256", f"sha256:{'9' * 64}"),
        ("isolation.firewall_rule_count", 1),
        ("isolation.target_resource_group_storage_account_count", 1),
        ("isolation.target_resource_group_redis_resource_count", 1),
        ("isolation.target_resource_group_storage_account_count", False),
        ("pass", False),
    ],
)
def test_digest_identity_resource_and_isolation_mismatches_fail(
    path: str, value: Any
) -> None:
    policy, intent, submission, receipt = _fixture()
    parent, name = _at(receipt, path)
    parent[name] = value
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure):
        _validate(policy, intent, submission, receipt)


@pytest.mark.parametrize(
    ("binding", "value"),
    [
        (
            "source_server_resource_id",
            _rid(
                SOURCE_GROUP,
                "Microsoft.DBforPostgreSQL",
                "flexibleServers",
                "different-source",
            ),
        ),
        ("executor_resource_group_id", TARGET_GROUP),
    ],
)
def test_exact_source_and_executor_group_policy_mismatches_fail(
    binding: str, value: str
) -> None:
    policy, intent, submission, receipt = _fixture()
    policy["fixed_bindings"][binding] = value
    receipt["policy_binding_sha256"] = receipt_v2._policy_binding_digest(policy)
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure, match="content_invalid"):
        _validate(policy, intent, submission, receipt)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("evidence_binding.candidate_protected_master_sha", "d" * 40),
        ("evidence_binding.run_attempt", 1),
        ("location", "eastus2"),
        ("cleanup_not_after", "2026-09-06T12:00:01Z"),
    ],
)
def test_safe_submit_chain_semantics_remain_exact(path: str, value: Any) -> None:
    policy, intent, submission, receipt = _fixture()
    parent, name = _at(intent, path)
    parent[name] = value
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure, match="content_invalid"):
        _validate(policy, intent, submission, receipt)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("restore_observed_at", "2026-09-05T11:59:59Z", "future"),
        ("created_at", "2026-09-05T12:11:01Z", "future"),
        ("expires_at", "2026-09-05T12:06:00Z", "expired"),
        ("expires_at", "2026-09-05T12:30:00Z", "expired"),
        ("cleanup_not_after", "2026-09-05T12:05:00Z", "future"),
        ("created_at", "2026-09-05T12:06:00+00:00", "content_invalid"),
    ],
)
def test_timestamp_future_expiry_and_lifetime_rules(
    field: str, value: str, code: str
) -> None:
    policy, intent, submission, receipt = _fixture()
    receipt[field] = value
    with pytest.raises(
        receipt_v2.ControllerReceiptV2Failure, match=f"controller_receipt.{code}"
    ):
        _validate(policy, intent, submission, receipt)


@pytest.mark.parametrize("record", ["intent", "submission"])
def test_record_chain_mismatch_fails_even_when_receipt_digest_is_recomputed(
    record: str,
) -> None:
    policy, intent, submission, receipt = _fixture()
    if record == "intent":
        intent["target_server_resource_id"] += "-other"
        receipt["authorization"]["intent_sha256"] = _digest(
            receipt_v2._canonical(intent)
        )
    else:
        submission["target_server_resource_id"] += "-other"
        receipt["authorization"]["submission_sha256"] = _digest(
            receipt_v2._canonical(submission)
        )
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure, match="content_invalid"):
        _validate(policy, intent, submission, receipt)


@pytest.mark.parametrize(
    "mutation",
    ["receipt", "intent_and_digest", "invalid_receipt_rehashed"],
)
def test_detached_or_tampered_validated_content_is_not_reusable(
    mutation: str,
) -> None:
    policy, intent, submission, receipt = _fixture()
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure, match="content_invalid"):
        receipt_v2.ValidatedControllerReceiptV2Content()
    result = _validate(policy, intent, submission, receipt)
    if mutation == "receipt":
        object.__setattr__(result, "receipt_bytes", b"{}")
    elif mutation == "intent_and_digest":
        object.__setattr__(result, "intent_bytes", b"{}")
        object.__setattr__(result, "intent_sha256", _digest(b"{}"))
    else:
        receipt["repository"] = "Untrusted/Detached"
        raw = receipt_v2._canonical(receipt)
        object.__setattr__(result, "receipt_bytes", raw)
        object.__setattr__(result, "receipt_sha256", _digest(raw))
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure, match="content_invalid"):
        receipt_v2.require_fresh_controller_receipt_v2_content(
            result, clock=lambda: NOW
        )


def test_freshness_returns_rederived_content_not_hostile_caller_fields() -> None:
    class EvilStr(str):
        def __eq__(self, _other: object) -> bool:
            return True

    policy, intent, submission, receipt = _fixture()
    result = _validate(policy, intent, submission, receipt)
    object.__setattr__(result, "target_fqdn", EvilStr("attacker.example.com"))
    object.__setattr__(result, "policy_binding_sha256", f"sha256:{'0' * 64}")
    refreshed = receipt_v2.require_fresh_controller_receipt_v2_content(
        result, clock=lambda: NOW
    )
    assert refreshed is not result
    assert type(refreshed.target_fqdn) is str
    assert refreshed.target_fqdn == receipt["target"]["target_fqdn"]


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("top", True),
        ("source", True),
        ("writer", "not-an-id"),
        (
            "executor_group",
            f"{CONTROL_GROUP}/providers/Microsoft.Network/virtualNetworks/control",
        ),
        (
            "source_server",
            f"{SOURCE_GROUP}/providers/Microsoft.Storage/storageAccounts/notpostgres",
        ),
        ("isolation_type", False),
        ("target_group", "not-an-id"),
        ("role", "Owner"),
    ],
)
def test_policy_unknown_fields_and_invalid_configured_pins_fail(
    mutation: str,
    value: Any,
) -> None:
    policy = _configured_policy()
    if mutation == "top":
        policy["unexpected"] = value
    elif mutation == "source":
        policy["record_chain_source"]["unexpected"] = value
    elif mutation == "writer":
        policy["record_chain_source"]["writer_identity_resource_id"] = value
        policy["record_chain_source"]["reader_identity_resource_ids"]["provision"] = (
            value
        )
    elif mutation == "executor_group":
        policy["fixed_bindings"]["executor_resource_group_id"] = value
    elif mutation == "source_server":
        policy["fixed_bindings"]["source_server_resource_id"] = value
    elif mutation == "isolation_type":
        policy["fixed_bindings"]["isolation"][
            "target_resource_group_storage_account_count"
        ] = value
    elif mutation == "target_group":
        policy["fixed_bindings"]["target_resource_group_id"] = value
    else:
        policy["fixed_bindings"]["approved_database_role"] = value
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure, match="policy_invalid"):
        receipt_v2.validate_controller_receipt_v2_policy(policy)


@pytest.mark.parametrize(
    "clock",
    [
        lambda: datetime(2026, 9, 5, 12, 10),
        lambda: NOW.replace(microsecond=1),
        lambda: "now",
    ],
)
def test_untrusted_clock_shapes_fail_closed(clock: Any) -> None:
    policy, intent, submission, receipt = _fixture()
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure, match="clock_invalid"):
        receipt_v2.validate_controller_receipt_v2_content(
            policy,
            intent_bytes=receipt_v2._canonical(intent),
            submission_bytes=receipt_v2._canonical(submission),
            receipt_bytes=receipt_v2._canonical(receipt),
            clock=clock,
        )


def test_public_api_has_no_source_selector_and_errors_never_echo_content() -> None:
    parameters = inspect.signature(
        receipt_v2.validate_controller_receipt_v2_content
    ).parameters
    assert set(parameters) == {
        "policy",
        "intent_bytes",
        "submission_bytes",
        "receipt_bytes",
        "clock",
    }
    assert not any(
        token in name for name in parameters for token in ("path", "uri", "mapping")
    )
    source = inspect.getsource(receipt_v2)
    assert all(
        token not in source
        for token in ("import hmac", "cryptography", "urllib", "requests")
    )

    policy, intent, submission, receipt = _fixture()
    secret = "never-echo-this-detached-value"
    receipt["target"]["target_fqdn"] = secret
    with pytest.raises(receipt_v2.ControllerReceiptV2Failure) as error:
        _validate(policy, intent, submission, receipt)
    assert secret not in str(error.value)
    assert len(str(error.value)) < 64
