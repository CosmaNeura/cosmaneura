from __future__ import annotations

import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, cast

import pytest

from scripts import authenticate_staging_github_attestation as github
from scripts import authenticate_staging_server_scope_receipt as server_auth
from scripts import compose_staging_restore_evidence_binding as composition
from scripts import staging_restore_safe_submit as safe_submit
from scripts import verify_staging_source_provider_observation as provider

ROOT = Path(__file__).resolve().parents[2]
POLICY = (
    ROOT / "contracts/staging-rehearsal/v1/restore-evidence-composition-policy.json"
)
NOW = datetime(2026, 9, 3, 12, 5, tzinfo=UTC)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _policy() -> dict[str, Any]:
    return json.loads(POLICY.read_text())


def _activated_policy(
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[bytes, str]:
    value = _policy()
    value["activation_ready"] = True
    value["activation"] = dict.fromkeys(value["activation"], True)
    if mutate:
        mutate(value)
    raw = _canonical(value)
    return raw, hashlib.sha256(raw).hexdigest()


def _base_record(**changes: Any) -> dict[str, Any]:
    source = json.loads(provider.POLICY_PATH.read_text())["source"]
    value = {
        "schema_version": 1,
        "binding_policy_id": "cosmaneura-staging-restore-evidence-binding-v1",
        "evidence_manifest_policy_id": "cosmaneura-staging-evidence-manifest-v2",
        "evidence_manifest_policy_sha256": (
            f"sha256:{server_auth.evidence_manifest.POLICY_SHA256}"
        ),
        "manifest_id": "rehearsal-20260903",
        "manifest_file_sha256": f"sha256:{'1' * 64}",
        "bundle_index_sha256": f"sha256:{'2' * 64}",
        "evidence_set_sha256": f"sha256:{'3' * 64}",
        "source_sha": "4" * 40,
        "candidate_sha": "5" * 40,
        "source_server_resource_id": source["resource_id"],
        "source_database_name": source["database_name"],
        "approved_restore_time": "2026-09-03T12:00:00Z",
        "observed_at": "2026-09-03T12:00:00Z",
        "expires_at": "2026-09-03T12:15:00Z",
        "candidate_approval_pr_number": 278,
        "candidate_protected_master_sha": "6" * 40,
        "candidate_schema_revision": "7" * 12,
        "candidate_receipt_sha256": f"sha256:{'8' * 64}",
        "inventory_receipt_sha256": f"sha256:{'9' * 64}",
        "quiescence_receipt_sha256": f"sha256:{'a' * 64}",
        "server_scope_receipt_sha256": f"sha256:{'b' * 64}",
        "repository": "CosmaNeura/cosmaneura",
        "protected_ref": "refs/heads/master",
        "workflow_path": ".github/workflows/staging_restore_rehearsal.yml",
        "workflow_sha": "6" * 40,
        "run_id": "901",
        "run_attempt": "1",
        "event_name": "workflow_dispatch",
        "signer_issuer": "https://token.actions.githubusercontent.com",
        "signer_subject": (
            "repo:CosmaNeura/cosmaneura:environment:staging-restore-provision"
        ),
        "server_scope_authentication_receipt_sha256": f"sha256:{'c' * 64}",
    }
    value.update(changes)
    return value


def _server_receipt(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    policy = json.loads(server_auth.POLICY_PATH.read_text())
    value = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "policy_sha256": f"sha256:{server_auth.POLICY_SHA256}",
        "manifest_id": base["manifest_id"],
        "manifest_file_sha256": base["manifest_file_sha256"],
        "bundle_index_sha256": base["bundle_index_sha256"],
        "evidence_set_sha256": base["evidence_set_sha256"],
        "server_scope_file_sha256": f"sha256:{'d' * 64}",
        "server_scope_receipt_sha256": base["server_scope_receipt_sha256"],
        "source_sha": base["source_sha"],
        "source_server_resource_id": base["source_server_resource_id"],
        "source_database_name": base["source_database_name"],
        "source_resource_hmac": "e" * 64,
        "primary_database_identity_hmac": "f" * 64,
        "server_scope_key_version": policy["hmac"]["allowed_key_versions"][0],
        "server_scope_evidence_hmac": "0" * 64,
        "observed_at": base["observed_at"],
        "expires_at": base["expires_at"],
        "authenticated_at": "2026-09-03T12:02:00Z",
        "pass": True,
    }
    value.update(changes)
    return value


def _provider_receipt(
    base: dict[str, Any], base_bytes: bytes, **changes: Any
) -> dict[str, Any]:
    policy = json.loads(provider.POLICY_PATH.read_text())
    source, transport, capability = (
        policy["source"],
        policy["provider"],
        policy["restore_capability"],
    )
    value = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "policy_sha256": f"sha256:{provider.POLICY_SHA256}",
        "base_binding_sha256": _digest(base_bytes),
        "manifest_id": base["manifest_id"],
        "evidence_set_sha256": base["evidence_set_sha256"],
        "source_sha": base["source_sha"],
        "candidate_sha": base["candidate_sha"],
        "provider_kind": transport["kind"],
        "management_origin": transport["management_origin"],
        "provider_api_version": transport["api_version"],
        "source_server_resource_id": base["source_server_resource_id"],
        "source_server_name": source["server_name"],
        "source_server_type": source["resource_type"],
        "source_location": source["location"],
        "source_database_name": base["source_database_name"],
        "source_fqdn": source["fqdn"],
        "source_postgresql_major": source["postgresql_major"],
        "source_state": source["state"],
        "source_public_network_access": source["public_network_access"],
        "source_backup_retention_days": source["backup_retention_days"],
        "source_geo_redundant_backup": source["geo_redundant_backup"],
        "source_replication_role": source["replication_role"],
        "approved_restore_time": base["approved_restore_time"],
        "earliest_restore_time": "2026-09-03T11:59:59.1234567Z",
        "restore_mode": capability["mode"],
        "restore_window_claim": capability["claim"],
        "provider_response_sha256": f"sha256:{'1' * 64}",
        "observed_at": "2026-09-03T12:05:00Z",
        "expires_at": "2026-09-03T12:06:00Z",
        "pass": True,
    }
    value.update(changes)
    return value


def _github_receipt(
    base: dict[str, Any], base_bytes: bytes, **changes: Any
) -> dict[str, Any]:
    policy = json.loads(github.POLICY_PATH.read_text())
    certificate = policy["certificate"]
    value = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "policy_sha256": f"sha256:{github.POLICY_SHA256}",
        "base_binding_sha256": _digest(base_bytes),
        "manifest_id": base["manifest_id"],
        "manifest_file_sha256": base["manifest_file_sha256"],
        "evidence_set_sha256": base["evidence_set_sha256"],
        "source_sha": base["source_sha"],
        "candidate_sha": base["candidate_sha"],
        "repository": base["repository"],
        "protected_ref": base["protected_ref"],
        "workflow_path": base["workflow_path"],
        "workflow_sha": base["workflow_sha"],
        "run_id": base["run_id"],
        "run_attempt": base["run_attempt"],
        "event_name": base["event_name"],
        "attestation_bundle_sha256": f"sha256:{'2' * 64}",
        "statement_sha256": f"sha256:{'3' * 64}",
        "subject_name": policy["statement"]["subject_name"],
        "subject_sha256": base["manifest_file_sha256"],
        "predicate_type": policy["statement"]["predicate_type"],
        "certificate_sha256": f"sha256:{'4' * 64}",
        "certificate_claims_sha256": f"sha256:{'5' * 64}",
        "certificate_oidc_issuer": base["signer_issuer"],
        "certificate_subject_alternative_name": certificate["subject_alternative_name"],
        "certificate_deployment_environment": certificate[
            "deployment_environment_extension"
        ]["value"],
        "certificate_token_subject": base["signer_subject"],
        "certificate_runner_environment": certificate["runner_environment"],
        "certificate_run_invocation_uri": (
            "https://github.com/CosmaNeura/cosmaneura/actions/runs/901/attempts/1"
        ),
        "trusted_root_sha256": f"sha256:{'6' * 64}",
        "verifier_version": policy["verifier"]["version"],
        "verifier_sha256": f"sha256:{'7' * 64}",
        "verified_timestamps_sha256": f"sha256:{'8' * 64}",
        "verified_at": "2026-09-03T12:05:00Z",
        "expires_at": "2026-09-03T12:06:00Z",
        "pass": True,
    }
    value.update(changes)
    return value


def _sealed_base(record: dict[str, Any], *, cls: type[Any] | None = None) -> Any:
    value = object.__new__(cls or server_auth.ServerAuthenticatedSafeSubmitBinding)
    object.__setattr__(value, "_record_bytes", _canonical(record))
    object.__setattr__(
        value,
        "_authentication_seal",
        server_auth._SERVER_AUTHENTICATED_SAFE_SUBMIT_SEAL,
    )
    return value


def _sealed_provider(receipt: dict[str, Any], *, cls: type[Any] | None = None) -> Any:
    raw = _canonical(receipt)
    value = object.__new__(cls or provider.VerifiedProviderObservation)
    object.__setattr__(value, "_receipt_bytes", raw)
    object.__setattr__(value, "_receipt_sha256", _digest(raw))
    object.__setattr__(
        value, "_observation_seal", provider._VERIFIED_PROVIDER_OBSERVATION_SEAL
    )
    return value


def _sealed_github(receipt: dict[str, Any], *, cls: type[Any] | None = None) -> Any:
    raw = _canonical(receipt)
    value = object.__new__(cls or github.VerifiedGitHubAttestation)
    object.__setattr__(value, "_receipt_bytes", raw)
    object.__setattr__(value, "_receipt_sha256", _digest(raw))
    object.__setattr__(
        value, "_attestation_seal", github._VERIFIED_GITHUB_ATTESTATION_SEAL
    )
    return value


def _case(**base_changes: Any) -> dict[str, Any]:
    base = _base_record(**base_changes)
    server_receipt = _server_receipt(base)
    server_raw = _canonical(server_receipt)
    base["server_scope_authentication_receipt_sha256"] = _digest(server_raw)
    base_bytes = _canonical(base)
    provider_receipt = _provider_receipt(base, base_bytes)
    github_receipt = _github_receipt(base, base_bytes)
    return {
        "base": base,
        "binding": _sealed_base(base),
        "server_receipt": server_receipt,
        "server_raw": server_raw,
        "provider_receipt": provider_receipt,
        "provider": _sealed_provider(provider_receipt),
        "github_receipt": github_receipt,
        "github": _sealed_github(github_receipt),
    }


def _call(
    case: dict[str, Any],
    *,
    clock: Callable[[], datetime] = lambda: NOW,
    policy_mutation: Callable[[dict[str, Any]], None] | None = None,
    policy_sha256: str | None = None,
) -> tuple[dict[str, Any], Any]:
    raw, digest = _activated_policy(policy_mutation)
    return composition._compose_restore_evidence_binding_with_document(
        policy_bytes=raw,
        policy_sha256=policy_sha256 or digest,
        server_binding=case["binding"],
        server_authentication_receipt_bytes=case["server_raw"],
        provider_observation=case["provider"],
        github_attestation=case["github"],
        clock=clock,
    )


def _rebase_server_receipt(case: dict[str, Any], **changes: Any) -> None:
    receipt = dict(case["server_receipt"])
    receipt.update(changes)
    raw = _canonical(receipt)
    base = dict(case["base"])
    base["server_scope_authentication_receipt_sha256"] = _digest(raw)
    base_bytes = _canonical(base)
    provider_receipt = dict(case["provider_receipt"])
    github_receipt = dict(case["github_receipt"])
    provider_receipt["base_binding_sha256"] = _digest(base_bytes)
    github_receipt["base_binding_sha256"] = _digest(base_bytes)
    case.update(
        base=base,
        binding=_sealed_base(base),
        server_receipt=receipt,
        server_raw=raw,
        provider_receipt=provider_receipt,
        provider=_sealed_provider(provider_receipt),
        github_receipt=github_receipt,
        github=_sealed_github(github_receipt),
    )


def _different(value: Any) -> Any:
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value + 1
    assert isinstance(value, str)
    if value.startswith("sha256:"):
        return f"sha256:{'f' * 64}"
    if len(value) == 40:
        return "f" * 40
    if value.endswith("Z"):
        return "2026-09-03T12:06:00Z"
    return f"{value}-mismatch"


class CountingClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.value


def test_checked_in_policy_is_exact_pinned_dormant_and_public_refuses() -> None:
    raw = POLICY.read_bytes()
    policy = _policy()
    pins = policy["policy_pins"]
    assert hashlib.sha256(raw).hexdigest() == composition.POLICY_SHA256
    assert policy["closed_world"] is True
    assert policy["activation_ready"] is False
    assert set(policy["activation"].values()) == {False}
    assert pins == {
        "evidence_manifest": {
            "policy_id": "cosmaneura-staging-evidence-manifest-v2",
            "sha256": f"sha256:{server_auth.evidence_manifest.POLICY_SHA256}",
        },
        "server_scope_authentication": {
            "policy_id": json.loads(server_auth.POLICY_PATH.read_text())["policy_id"],
            "sha256": f"sha256:{server_auth.POLICY_SHA256}",
        },
        "source_provider_observation": {
            "policy_id": json.loads(provider.POLICY_PATH.read_text())["policy_id"],
            "sha256": f"sha256:{provider.POLICY_SHA256}",
        },
        "github_attestation_authentication": {
            "policy_id": json.loads(github.POLICY_PATH.read_text())["policy_id"],
            "sha256": f"sha256:{github.POLICY_SHA256}",
        },
    }
    assert policy["output"]["fields"] == [
        name
        for name in safe_submit.RestoreEvidenceBinding.__dataclass_fields__
        if name != "_binding_seal"
    ]
    assert (
        inspect.signature(composition.compose_restore_evidence_binding).parameters == {}
    )
    with pytest.raises(composition.CompositionFailure):
        composition.compose_restore_evidence_binding()


def test_happy_path_mints_exact_digest_only_final_binding() -> None:
    case = _case()
    result, binding = _call(case)
    repeated_result, repeated_binding = _call(case)
    record = safe_submit.SafeRestoreSubmitter._binding_record(binding)
    expected = dict(case["base"])
    expected["expires_at"] = "2026-09-03T12:06:00Z"
    expected["provider_receipt_sha256"] = case["provider"].receipt_sha256
    expected["attestation_receipt_sha256"] = case["github"].receipt_sha256

    assert type(binding) is safe_submit.RestoreEvidenceBinding
    assert binding._is_module_minted_binding()
    assert (result, record) == (
        repeated_result,
        safe_submit.SafeRestoreSubmitter._binding_record(repeated_binding),
    )
    assert record == expected
    assert set(record) == set(_policy()["output"]["fields"])
    assert binding.approved_restore_time == datetime(2026, 9, 3, 12, tzinfo=UTC)
    assert binding.observed_at == datetime(2026, 9, 3, 12, tzinfo=UTC)
    assert binding.expires_at == datetime(2026, 9, 3, 12, 6, tzinfo=UTC)
    assert result == {
        "schema_version": 1,
        "policy_id": _policy()["policy_id"],
        "evidence_binding_sha256": _digest(_canonical(record)),
        "activation_state": "blocked",
        "activation_blockers": _policy()["output_activation_blockers"],
    }

    rendered = json.dumps(result, sort_keys=True) + repr(binding)
    for receipt_only in (
        case["server_receipt"]["server_scope_evidence_hmac"],
        case["provider_receipt"]["source_fqdn"],
        case["github_receipt"]["certificate_run_invocation_uri"],
    ):
        assert receipt_only not in rendered


# fmt: off
OVERLAPS = [
    *(("server", name) for name in "manifest_id manifest_file_sha256 bundle_index_sha256 evidence_set_sha256 server_scope_receipt_sha256 source_sha source_server_resource_id source_database_name observed_at expires_at".split()),
    *(("provider", name) for name in "manifest_id evidence_set_sha256 source_sha candidate_sha source_server_resource_id source_database_name approved_restore_time".split()),
    *(("github", name) for name in "manifest_id manifest_file_sha256 evidence_set_sha256 source_sha candidate_sha repository protected_ref workflow_path workflow_sha run_id run_attempt event_name subject_sha256 certificate_oidc_issuer certificate_token_subject".split()),
]
# fmt: on


@pytest.mark.parametrize(("source", "field"), OVERLAPS)
def test_every_overlapping_receipt_field_is_cross_bound(
    source: str, field: str
) -> None:
    case = _case()
    receipt = dict(case[f"{source}_receipt"])
    receipt[field] = _different(receipt[field])
    if source == "server":
        _rebase_server_receipt(case, **{field: receipt[field]})
    elif source == "provider":
        case["provider"] = _sealed_provider(receipt)
    else:
        case["github"] = _sealed_github(receipt)
    with pytest.raises(composition.CompositionFailure):
        _call(case)


@pytest.mark.parametrize(
    ("source", "field"),
    [
        ("server", "policy_id"),
        ("server", "policy_sha256"),
        ("server", "pass"),
        ("provider", "policy_id"),
        ("provider", "policy_sha256"),
        ("provider", "base_binding_sha256"),
        ("provider", "pass"),
        ("github", "policy_id"),
        ("github", "policy_sha256"),
        ("github", "base_binding_sha256"),
        ("github", "pass"),
    ],
)
def test_policy_pins_base_hashes_and_pass_values_are_exact(
    source: str, field: str
) -> None:
    case = _case()
    receipt = dict(case[f"{source}_receipt"])
    receipt[field] = _different(receipt[field])
    if source == "server":
        _rebase_server_receipt(case, **{field: receipt[field]})
    elif source == "provider":
        case["provider"] = _sealed_provider(receipt)
    else:
        case["github"] = _sealed_github(receipt)
    with pytest.raises(composition.CompositionFailure):
        _call(case)


@pytest.mark.parametrize("source", ["server", "provider", "github"])
def test_receipt_schema_version_rejects_boolean_alias(source: str) -> None:
    case = _case()
    receipt = {**case[f"{source}_receipt"], "schema_version": True}
    if source == "server":
        _rebase_server_receipt(case, schema_version=True)
    elif source == "provider":
        case["provider"] = _sealed_provider(receipt)
    else:
        case["github"] = _sealed_github(receipt)
    with pytest.raises(composition.CompositionFailure):
        _call(case)


@pytest.mark.parametrize(
    "kind",
    "raw_base unsealed_base subclass_base noncanonical_base raw_provider "
    "unsealed_provider subclass_provider noncanonical_provider raw_github "
    "unsealed_github subclass_github noncanonical_github raw_server_receipt "
    "noncanonical_server_receipt extra_server_receipt extra_provider_receipt "
    "extra_github_receipt".split(),
)
def test_unsealed_raw_subclass_and_tampered_inputs_fail_before_clock(kind: str) -> None:
    case = _case()
    if kind == "raw_base":
        case["binding"] = case["base"]
    elif kind == "unsealed_base":
        case["binding"] = object.__new__(
            server_auth.ServerAuthenticatedSafeSubmitBinding
        )
    elif kind == "subclass_base":

        class BaseSubclass(server_auth.ServerAuthenticatedSafeSubmitBinding):
            pass

        case["binding"] = _sealed_base(case["base"], cls=BaseSubclass)
    elif kind == "noncanonical_base":
        object.__setattr__(
            case["binding"], "_record_bytes", _canonical(case["base"]) + b" "
        )
    elif kind == "raw_provider":
        case["provider"] = case["provider_receipt"]
    elif kind == "unsealed_provider":
        case["provider"] = object.__new__(provider.VerifiedProviderObservation)
    elif kind == "subclass_provider":

        class ProviderSubclass(provider.VerifiedProviderObservation):
            pass

        case["provider"] = _sealed_provider(
            case["provider_receipt"], cls=ProviderSubclass
        )
    elif kind == "noncanonical_provider":
        raw = case["provider"].receipt_bytes + b" "
        object.__setattr__(case["provider"], "_receipt_bytes", raw)
        object.__setattr__(case["provider"], "_receipt_sha256", _digest(raw))
    elif kind == "raw_github":
        case["github"] = case["github_receipt"]
    elif kind == "unsealed_github":
        case["github"] = object.__new__(github.VerifiedGitHubAttestation)
    elif kind == "subclass_github":

        class GitHubSubclass(github.VerifiedGitHubAttestation):
            pass

        case["github"] = _sealed_github(case["github_receipt"], cls=GitHubSubclass)
    elif kind == "noncanonical_github":
        raw = case["github"].receipt_bytes + b" "
        object.__setattr__(case["github"], "_receipt_bytes", raw)
        object.__setattr__(case["github"], "_receipt_sha256", _digest(raw))
    elif kind == "raw_server_receipt":
        case["server_raw"] = case["server_receipt"]
    elif kind == "noncanonical_server_receipt":
        case["server_raw"] += b" "
    elif kind == "extra_server_receipt":
        _rebase_server_receipt(case, attacker=True)
    elif kind == "extra_provider_receipt":
        case["provider"] = _sealed_provider(
            {**case["provider_receipt"], "attacker": True}
        )
    else:
        case["github"] = _sealed_github({**case["github_receipt"], "attacker": True})
    clock = CountingClock()
    with pytest.raises(composition.CompositionFailure):
        _call(case, clock=clock)
    assert clock.calls == 0


def test_receipt_digest_mismatches_and_policy_mutations_fail_before_clock() -> None:
    cases = [_case() for _ in range(5)]
    cases[0]["server_raw"] = _canonical(
        {**cases[0]["server_receipt"], "server_scope_evidence_hmac": "1" * 64}
    )
    object.__setattr__(cases[1]["provider"], "_receipt_sha256", f"sha256:{'f' * 64}")
    object.__setattr__(cases[2]["github"], "_receipt_sha256", f"sha256:{'f' * 64}")
    assert cases, (
        "there is nothing to check, so the assertions below would pass vacuously"
    )
    for index, case in enumerate(cases):
        clock = CountingClock()
        kwargs: dict[str, Any] = {"clock": clock}
        if index == 3:
            kwargs["policy_sha256"] = "f" * 64
        elif index == 4:
            kwargs["policy_mutation"] = lambda value: value.update(attacker=True)
        with pytest.raises(composition.CompositionFailure):
            _call(case, **kwargs)
        assert clock.calls == 0


@pytest.mark.parametrize(
    ("source", "changes", "now", "clock_reads"),
    [
        ("server", {"authenticated_at": "2026-09-03T12:05:30Z"}, NOW, 1),
        ("provider", {"observed_at": "2026-09-03T12:05:30Z"}, NOW, 1),
        ("github", {"verified_at": "2026-09-03T12:05:30Z"}, NOW, 1),
        ("provider", {}, datetime(2026, 9, 3, 12, 6, tzinfo=UTC), 1),
        ("provider", {"expires_at": "2026-09-03T12:06:01Z"}, NOW, 0),
        ("github", {"expires_at": "2026-09-03T12:06:01Z"}, NOW, 0),
    ],
)
def test_now_must_be_inside_every_validity_window(
    source: str, changes: dict[str, Any], now: datetime, clock_reads: int
) -> None:
    case = _case()
    receipt = dict(case[f"{source}_receipt"])
    receipt.update(changes)
    if source == "server":
        _rebase_server_receipt(case, **changes)
    elif source == "provider":
        case["provider"] = _sealed_provider(receipt)
    elif source == "github":
        case["github"] = _sealed_github(receipt)
    clock = CountingClock(now)
    with pytest.raises(composition.CompositionFailure):
        _call(case, clock=clock)
    assert clock.calls == clock_reads


def test_clock_is_read_once_after_validation_and_must_be_utc() -> None:
    case = _case()
    clock = CountingClock()
    result, binding = _call(case, clock=clock)
    assert clock.calls == 1
    assert binding.expires_at == datetime(2026, 9, 3, 12, 6, tzinfo=UTC)
    assert result["evidence_binding_sha256"] == _digest(
        _canonical(safe_submit.SafeRestoreSubmitter._binding_record(binding))
    )


@pytest.mark.parametrize(
    "value",
    [
        NOW.replace(tzinfo=None),
        NOW.astimezone(timezone(timedelta(hours=1))),
        NOW.replace(microsecond=1),
    ],
)
def test_clock_rejects_naive_non_utc_and_fractional_values(value: datetime) -> None:
    clock = CountingClock(value)
    with pytest.raises(
        composition.CompositionFailure, match="composition_clock.invalid"
    ):
        _call(_case(), clock=clock)
    assert clock.calls == 1


def test_final_binding_crosses_boundary_but_canonical_submit_stays_dormant() -> None:
    _result, binding = _call(_case())
    assert (
        safe_submit.SafeRestoreSubmitter._binding_record(binding)[
            "provider_receipt_sha256"
        ]
        == binding.provider_receipt_sha256
    )

    envelope, policy = safe_submit.validate_canonical_contracts()
    assert set(policy["activation"].values()) == {False}
    config = safe_submit.config_from_policies(envelope, policy, executable=False)

    class NoSideEffects:
        calls = 0

        def __getattr__(self, _name: str) -> Any:
            self.calls += 1
            raise AssertionError("side effect attempted")

    transport, markers = NoSideEffects(), NoSideEffects()
    engine = safe_submit.SafeRestoreSubmitter(
        cast(safe_submit.ArmTransport, transport),
        cast(safe_submit.MarkerStore, markers),
        config,
    )
    assert engine._validate_evidence_record(
        safe_submit.SafeRestoreSubmitter._binding_record(binding),
        require_fresh=False,
    ) == (
        datetime(2026, 9, 3, 12, tzinfo=UTC),
        datetime(2026, 9, 3, 12, tzinfo=UTC),
        datetime(2026, 9, 3, 12, 6, tzinfo=UTC),
    )
    request = safe_submit.RestoreRequest(
        rehearsal_id="0" * 12,
        evidence_binding=binding,
        cleanup_not_after=NOW + timedelta(hours=6),
        repository=binding.repository,
        workflow_sha=binding.workflow_sha,
        run_id=binding.run_id,
        run_attempt=binding.run_attempt,
    )
    with pytest.raises(safe_submit.SafeSubmitError, match="safe_submit_not_activated"):
        engine.submit(request)
    assert transport.calls == markers.calls == 0
