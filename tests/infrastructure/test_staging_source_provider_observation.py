from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import sys
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify_staging_source_provider_observation.py"
POLICY = ROOT / "contracts/staging-rehearsal/v1/source-provider-observation-policy.json"
SAFE_SUBMIT_SCRIPT = ROOT / "scripts/staging_restore_safe_submit.py"
SAFE_SUBMIT_POLICY = (
    ROOT / "contracts/staging-rehearsal/v1/restore-safe-submit-policy.json"
)
WORKFLOW_ROOT = ROOT / ".github/workflows"

APPROVED_TEXT = "2026-09-03T12:00:00Z"
OBSERVED = datetime(2026, 9, 3, 12, 1, tzinfo=UTC)
BASE_EXPIRES_TEXT = "2026-09-03T12:15:00Z"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(ROOT))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


@pytest.fixture
def module() -> ModuleType:
    return _load_module("standalone_staging_source_provider_observation", SCRIPT)


def _policy() -> dict[str, Any]:
    return json.loads(POLICY.read_text())


def _activated_policy_bytes(
    module: ModuleType,
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[bytes, str]:
    policy = _policy()
    policy["activation_ready"] = True
    policy["activation"] = {name: True for name in policy["activation"]}
    if mutate is not None:
        mutate(policy)
    raw = module._canonical(policy)
    return raw, hashlib.sha256(raw).hexdigest()


def _base_record(module: ModuleType, **changes: Any) -> dict[str, Any]:
    source = _policy()["source"]
    record = {
        "schema_version": 1,
        "binding_policy_id": "cosmaneura-staging-restore-evidence-binding-v1",
        "evidence_manifest_policy_id": "cosmaneura-staging-evidence-manifest-v2",
        "evidence_manifest_policy_sha256": (
            f"sha256:{module.server_auth.evidence_manifest.POLICY_SHA256}"
        ),
        "manifest_id": "rehearsal-20260903",
        "manifest_file_sha256": f"sha256:{'1' * 64}",
        "bundle_index_sha256": f"sha256:{'2' * 64}",
        "evidence_set_sha256": f"sha256:{'3' * 64}",
        "source_sha": "4" * 40,
        "candidate_sha": "5" * 40,
        "source_server_resource_id": source["resource_id"],
        "source_database_name": source["database_name"],
        "approved_restore_time": APPROVED_TEXT,
        "observed_at": APPROVED_TEXT,
        "expires_at": BASE_EXPIRES_TEXT,
        "candidate_approval_pr_number": 276,
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
    record.update(changes)
    return record


def _binding(
    module: ModuleType,
    *,
    raw: bytes | None = None,
    seal: object | None = None,
    binding_type: type[Any] | None = None,
    **changes: Any,
) -> Any:
    cls = binding_type or module.server_auth.ServerAuthenticatedSafeSubmitBinding
    value = object.__new__(cls)
    object.__setattr__(
        value,
        "_record_bytes",
        raw if raw is not None else module._canonical(_base_record(module, **changes)),
    )
    object.__setattr__(
        value,
        "_authentication_seal",
        module.server_auth._SERVER_AUTHENTICATED_SAFE_SUBMIT_SEAL
        if seal is None
        else seal,
    )
    return value


def _provider_document(
    *,
    earliest: str = "2026-09-03T11:59:59.1234567Z",
) -> dict[str, Any]:
    source = _policy()["source"]
    return {
        "id": source["resource_id"],
        "name": source["server_name"],
        "type": source["resource_type"],
        "location": "North Central US",
        "properties": {
            "fullyQualifiedDomainName": source["fqdn"],
            "version": source["postgresql_major"],
            "state": source["state"],
            "replicationRole": source["replication_role"],
            "network": {
                "publicNetworkAccess": source["public_network_access"],
            },
            "backup": {
                "backupRetentionDays": source["backup_retention_days"],
                "geoRedundantBackup": source["geo_redundant_backup"],
                "earliestRestoreDate": earliest,
            },
        },
        "providerOnlyField": {"not": "projected into the receipt"},
    }


class FakeProvider:
    def __init__(
        self,
        response: Any,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def get_flexible_server(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _response(
    module: ModuleType,
    *,
    document: Any | None = None,
    body: Any | None = None,
    status: Any = 200,
    content_type: Any = "application/json",
) -> Any:
    if body is None:
        body = module._canonical(_provider_document() if document is None else document)
    return module.ProviderObservationResponse(
        status=status,
        content_type=content_type,
        body=body,
    )


def _verify(
    module: ModuleType,
    *,
    binding: Any | None = None,
    provider: Any | None = None,
    response: Any | None = None,
    clock: Callable[[], datetime] = lambda: OBSERVED,
    mutate_policy: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], Any]:
    raw, digest = _activated_policy_bytes(module, mutate_policy)
    actual_response = response if response is not None else _response(module)
    actual_provider = provider or FakeProvider(actual_response)
    return module._verify_provider_observation_with_document(
        policy_bytes=raw,
        policy_sha256=digest,
        binding=_binding(module) if binding is None else binding,
        provider=actual_provider,
        clock=clock,
    )


def _set_path(value: dict[str, Any], path: str, replacement: Any) -> None:
    parts = path.split(".")
    cursor: dict[str, Any] = value
    for part in parts[:-1]:
        cursor = cursor[part]
    cursor[parts[-1]] = replacement


def test_checked_in_policy_is_hash_pinned_dormant_and_unwired(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = POLICY.read_bytes()
    policy, digest, loaded = module.load_provider_observation_policy_document()

    assert hashlib.sha256(raw).hexdigest() == module.POLICY_SHA256
    assert (digest, loaded) == (module.POLICY_SHA256, raw)
    assert policy["activation_ready"] is False
    assert policy["provider"]["http_method"] == "GET"
    assert policy["provider"]["redirects_permitted"] is False
    assert policy["provider"]["maximum_requests"] == 1
    assert policy["restore_capability"]["maximum_receipt_lifetime_seconds"] == 60
    assert policy["restore_capability"]["claim"] == "provider_advertised_window_only"
    assert set(policy["receipt_fields"]) == set(module.RECEIPT_FIELDS)
    assert all(
        value is False
        for value in json.loads(SAFE_SUBMIT_POLICY.read_text())["activation"].values()
    )
    for workflow in (*WORKFLOW_ROOT.glob("*.yml"), *WORKFLOW_ROOT.glob("*.yaml")):
        assert "verify_staging_source_provider_observation" not in workflow.read_text()

    monkeypatch.setattr(
        module,
        "_verify_provider_observation_with_document",
        lambda **_kwargs: pytest.fail("supported path must not expose test injection"),
    )
    assert inspect.signature(module.verify_provider_observation).parameters == {}
    with pytest.raises(
        module.ProviderObservationFailure, match="provider_policy.disabled"
    ):
        module.verify_provider_observation()

    activated_raw, activated_digest = _activated_policy_bytes(module)
    activated_policy = json.loads(activated_raw)
    monkeypatch.setattr(
        module,
        "load_provider_observation_policy_document",
        lambda: (activated_policy, activated_digest, activated_raw),
    )
    with pytest.raises(
        module.ProviderObservationFailure,
        match="trusted_provider_transport.unimplemented",
    ):
        module.verify_provider_observation()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda policy: policy.update(schema_version=1.0),
        lambda policy: policy.update(policy_id="other-policy"),
        lambda policy: policy.update(activation_ready=1),
        lambda policy: policy["restore_capability"].update(
            maximum_receipt_lifetime_seconds=60.0
        ),
        lambda policy: policy["restore_capability"].update(
            maximum_receipt_lifetime_seconds=61
        ),
        lambda policy: policy.update(extra=True),
        lambda policy: policy["provider"].update(request_timeout_seconds=30.0),
        lambda policy: policy["provider"].update(maximum_response_bytes=1048576.0),
        lambda policy: policy["provider"].update(maximum_requests=1.0),
        lambda policy: policy["source"].update(backup_retention_days=7.0),
        lambda policy: policy["activation"].update(network_access_enabled=1),
        lambda policy: policy["provider"].update(attacker_field="unexpected"),
        lambda policy: policy["source"].update(attacker_field="unexpected"),
        lambda policy: policy["restore_capability"].update(attacker_field="unexpected"),
        lambda policy: policy["source"].update(server_name="other"),
        lambda policy: policy["provider"].update(http_method="POST"),
        lambda policy: policy["provider"].update(redirects_permitted=True),
        lambda policy: policy["receipt_fields"].append("attacker_field"),
        lambda policy: policy["activation_blockers"].clear(),
    ],
)
def test_policy_schema_mutations_refuse_before_binding_or_provider(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    raw, digest = _activated_policy_bytes(module, mutation)
    provider = FakeProvider(None)
    monkeypatch.setattr(
        module,
        "_validate_upstream_binding",
        lambda *_args: pytest.fail("invalid policy must refuse before binding"),
    )

    with pytest.raises(
        module.ProviderObservationFailure, match="provider_policy.invalid"
    ):
        module._verify_provider_observation_with_document(
            policy_bytes=raw,
            policy_sha256=digest,
            binding=object(),
            provider=provider,
        )
    assert provider.calls == []


def test_policy_bytes_and_claimed_digest_are_atomic_and_strict(
    module: ModuleType,
) -> None:
    raw, digest = _activated_policy_bytes(module)
    provider = FakeProvider(_response(module))

    with pytest.raises(
        module.ProviderObservationFailure, match="provider_policy.hash_mismatch"
    ):
        module._verify_provider_observation_with_document(
            policy_bytes=raw + b" ",
            policy_sha256=digest,
            binding=_binding(module),
            provider=provider,
        )
    with pytest.raises(
        module.ProviderObservationFailure, match="provider_policy.hash_mismatch"
    ):
        module._verify_provider_observation_with_document(
            policy_bytes=bytearray(raw),
            policy_sha256=digest,
            binding=_binding(module),
            provider=provider,
        )
    assert provider.calls == []


def test_emits_deterministic_base_bound_receipt_from_one_exact_provider_response(
    module: ModuleType,
) -> None:
    body = module._canonical(_provider_document())
    provider_response = _response(module, body=body)
    first_provider = FakeProvider(provider_response)
    second_provider = FakeProvider(_response(module, body=body))
    binding = _binding(module)

    first_result, first_verified = _verify(
        module, binding=binding, provider=first_provider
    )
    second_result, second_verified = _verify(
        module, binding=binding, provider=second_provider
    )

    assert first_result == second_result
    assert first_verified == second_verified
    assert (
        first_provider.calls
        == second_provider.calls
        == [
            {
                "management_origin": _policy()["provider"]["management_origin"],
                "resource_id": _policy()["source"]["resource_id"],
                "api_version": _policy()["provider"]["api_version"],
                "timeout_seconds": _policy()["provider"]["request_timeout_seconds"],
            }
        ]
    )
    receipt = first_result["provider_observation_receipt"]
    assert set(receipt) == set(module.RECEIPT_FIELDS)
    assert receipt["base_binding_sha256"] == module._digest(binding.record_bytes)
    assert receipt["provider_response_sha256"] == module._digest(body)
    assert receipt["earliest_restore_time"] == "2026-09-03T11:59:59.1234567Z"
    assert receipt["observed_at"] == "2026-09-03T12:01:00Z"
    assert receipt["expires_at"] == "2026-09-03T12:02:00Z"
    assert receipt["pass"] is True
    assert "providerOnlyField" not in receipt
    assert first_result["provider_receipt_sha256"] == module._digest(
        module._canonical(receipt)
    )
    assert first_verified.receipt_bytes == module._canonical(receipt)
    assert first_verified.receipt_sha256 == first_result["provider_receipt_sha256"]
    assert first_result["activation_state"] == "blocked"
    assert first_result["activation_blockers"] == list(
        module.OUTPUT_ACTIVATION_BLOCKERS
    )
    assert "projected into the receipt" not in json.dumps(first_result)
    assert "projected into the receipt" not in repr(provider_response)


def test_exact_provider_response_bytes_are_bound_even_with_equivalent_json(
    module: ModuleType,
) -> None:
    document = _provider_document()
    compact = module._canonical(document)
    expanded = json.dumps(document, sort_keys=False, indent=2).encode("ascii")

    compact_result, compact_verified = _verify(
        module, response=_response(module, body=compact)
    )
    expanded_result, expanded_verified = _verify(
        module, response=_response(module, body=expanded)
    )

    assert (
        compact_result["provider_observation_receipt"]["provider_response_sha256"]
        != expanded_result["provider_observation_receipt"]["provider_response_sha256"]
    )
    assert compact_verified.receipt_sha256 != expanded_verified.receipt_sha256


def test_result_and_receipt_projections_cannot_mutate_sealed_bytes(
    module: ModuleType,
) -> None:
    result, verified = _verify(module)
    original_bytes = verified.receipt_bytes
    original_digest = verified.receipt_sha256

    result["provider_observation_receipt"]["source_sha"] = "f" * 40
    result["provider_receipt_sha256"] = f"sha256:{'f' * 64}"
    projection = verified.receipt
    projection["manifest_id"] = "attacker-controlled"

    assert verified.receipt_bytes == original_bytes
    assert verified.receipt_sha256 == original_digest
    assert verified.receipt["source_sha"] == "4" * 40
    assert verified.receipt["manifest_id"] == "rehearsal-20260903"
    rendered = repr(verified)
    assert original_digest not in rendered
    assert "provider_response_sha256" not in rendered


@pytest.mark.parametrize(
    ("changes", "raw_mutation"),
    [
        ({"schema_version": 1.0}, None),
        ({"evidence_manifest_policy_sha256": f"sha256:{'F' * 64}"}, None),
        ({"source_sha": "4" * 39}, None),
        ({"candidate_sha": "5" * 41}, None),
        ({"source_server_resource_id": "/other"}, None),
        ({"source_database_name": "postgres"}, None),
        ({"approved_restore_time": "2026-09-03T12:00:00+00:00"}, None),
        ({"observed_at": "2026-09-03T12:00:01Z"}, None),
        ({"expires_at": APPROVED_TEXT}, None),
        ({"expires_at": "2026-09-03T12:15:01Z"}, None),
        ({}, lambda raw: raw + b" "),
        ({}, lambda _raw: b"[]"),
    ],
)
def test_invalid_upstream_binding_refuses_before_provider(
    module: ModuleType,
    changes: dict[str, Any],
    raw_mutation: Callable[[bytes], bytes] | None,
) -> None:
    raw = module._canonical(_base_record(module, **changes))
    if raw_mutation is not None:
        raw = raw_mutation(raw)
    provider = FakeProvider(None)

    with pytest.raises(
        module.ProviderObservationFailure, match="server_authenticated_binding.invalid"
    ):
        _verify(module, binding=_binding(module, raw=raw), provider=provider)
    assert provider.calls == []


def test_unsealed_wrong_type_and_subclass_bindings_refuse_before_provider(
    module: ModuleType,
) -> None:
    class BytesSubclass(bytes):
        pass

    class BindingSubclass(module.server_auth.ServerAuthenticatedSafeSubmitBinding):
        pass

    provider = FakeProvider(None)
    candidates = [
        {},
        _binding(module, seal=object()),
        _binding(module, binding_type=BindingSubclass),
        _binding(
            module,
            raw=BytesSubclass(module._canonical(_base_record(module))),
        ),
    ]
    for candidate in candidates:
        with pytest.raises(
            module.ProviderObservationFailure,
            match="server_authenticated_binding.invalid",
        ):
            _verify(module, binding=candidate, provider=provider)
    assert provider.calls == []


@pytest.mark.parametrize(
    ("status", "content_type", "body"),
    [
        (True, "application/json", None),
        (201, "application/json", None),
        (200, "application/jsonp", None),
        (200, "application/json; charset", None),
        (200, 'application/json; charset="unterminated', None),
        (200, "application/json; profile=a b", None),
        (200, 'application/json; charset="utf-8" trailing', None),
        (200, "application/json; charset=utf-8; CHARSET=ascii", None),
        (200, "application/json; profile=nonascii-\u00e9", None),
        (200, "application/json", b""),
        (200, "application/json", bytearray(b"{}")),
        (200, "application/json", b"[]"),
        (200, "application/json", b'{"id":"one","id":"two"}'),
        (200, "application/json", b"[" * 20_000 + b"]" * 20_000),
        (200, "application/json", b'{"value":' + b"9" * 5_000 + b"}"),
    ],
)
def test_invalid_http_like_response_is_bounded_and_clock_is_not_sampled(
    module: ModuleType,
    status: Any,
    content_type: Any,
    body: Any,
) -> None:
    actual_body = module._canonical(_provider_document()) if body is None else body
    provider = FakeProvider(
        module.ProviderObservationResponse(
            status=status,
            content_type=content_type,
            body=actual_body,
        )
    )

    with pytest.raises(module.ProviderObservationFailure) as failure:
        _verify(
            module,
            provider=provider,
            clock=lambda: pytest.fail("invalid response must refuse before clock"),
        )
    assert str(failure.value) == "provider_response.invalid"
    assert len(provider.calls) == 1


def test_response_size_bound_and_exact_response_type_are_enforced(
    module: ModuleType,
) -> None:
    maximum = _policy()["provider"]["maximum_response_bytes"]
    oversized = b"{" + b" " * maximum + b"}"
    providers = [
        FakeProvider(_response(module, body=oversized)),
        FakeProvider(
            {"status": 200, "content_type": "application/json", "body": b"{}"}
        ),
    ]

    class ResponseSubclass(module.ProviderObservationResponse):
        pass

    providers.append(
        FakeProvider(
            ResponseSubclass(status=200, content_type="application/json", body=b"{}")
        )
    )
    for provider in providers:
        with pytest.raises(
            module.ProviderObservationFailure, match="provider_response.invalid"
        ):
            _verify(module, provider=provider)


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "Application/JSON; Charset=UTF-8",
        'application/json; charset="utf-8"; profile=staging',
    ],
)
def test_valid_json_media_type_parameters_are_accepted(
    module: ModuleType,
    content_type: str,
) -> None:
    result, _verified = _verify(
        module, response=_response(module, content_type=content_type)
    )
    assert result["provider_observation_receipt"]["pass"] is True


def test_provider_exception_is_bounded_and_never_leaks_payload(
    module: ModuleType,
) -> None:
    secret = "provider-token-that-must-not-leak"
    provider = FakeProvider(None, error=RuntimeError(secret))

    with pytest.raises(module.ProviderObservationFailure) as failure:
        _verify(module, provider=provider)
    assert str(failure.value) == "provider.unavailable"
    assert secret not in repr(failure.value)
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("id", "/subscriptions/other"),
        ("name", "other-server"),
        ("type", "Microsoft.DBforPostgreSQL/servers"),
        ("location", "eastus"),
        ("properties.fullyQualifiedDomainName", "attacker.example"),
        ("properties.version", "15"),
        ("properties.state", "Updating"),
        ("properties.replicationRole", "Replica"),
        ("properties.network.publicNetworkAccess", "Disabled"),
        ("properties.backup.backupRetentionDays", True),
        ("properties.backup.backupRetentionDays", 8),
        ("properties.backup.geoRedundantBackup", "Enabled"),
    ],
)
def test_provider_identity_and_configuration_mismatches_fail_closed(
    module: ModuleType,
    path: str,
    replacement: Any,
) -> None:
    document = _provider_document()
    _set_path(document, path, replacement)

    with pytest.raises(
        module.ProviderObservationFailure, match="provider_response.binding_mismatch"
    ):
        _verify(module, response=_response(module, document=document))


def test_resource_type_id_and_location_use_documented_normalization_only(
    module: ModuleType,
) -> None:
    document = _provider_document()
    document["id"] = document["id"].upper() + "/"
    document["type"] = document["type"].upper()
    document["location"] = "North-Central US"

    result, _verified = _verify(module, response=_response(module, document=document))
    assert result["provider_observation_receipt"]["pass"] is True


@pytest.mark.parametrize(
    "earliest",
    [
        "2026-09-03T11:59:59Z",
        "2026-09-03T11:59:59.0Z",
        "2026-09-03T11:59:59.123Z",
        "2026-09-03T11:59:59.1234567Z",
    ],
)
def test_provider_restore_time_accepts_and_preserves_zero_to_seven_fractional_digits(
    module: ModuleType,
    earliest: str,
) -> None:
    document = _provider_document(earliest=earliest)

    result, _verified = _verify(module, response=_response(module, document=document))
    assert result["provider_observation_receipt"]["earliest_restore_time"] == earliest


@pytest.mark.parametrize(
    "earliest",
    [
        "2026-09-03T12:00:00.0000001Z",
        "2026-09-03T11:59:59.12345678Z",
        "2026-09-03T11:59:59+00:00",
        "2026-09-03t11:59:59z",
        "not-a-time",
        123,
    ],
)
def test_invalid_or_too_late_provider_restore_time_fails_closed(
    module: ModuleType,
    earliest: Any,
) -> None:
    document = _provider_document(earliest=earliest)

    with pytest.raises(
        module.ProviderObservationFailure,
        match="provider_restore_window.invalid|provider_observation.stale",
    ):
        _verify(module, response=_response(module, document=document))


@pytest.mark.parametrize(
    ("now", "error"),
    [
        (datetime(2026, 9, 3, 11, 59, 59, tzinfo=UTC), "provider_observation.stale"),
        (datetime(2026, 9, 3, 12, 15, tzinfo=UTC), "provider_observation.stale"),
        (datetime(2026, 9, 3, 12, 1, 0, 1, tzinfo=UTC), "provider_clock.invalid"),
        (datetime(2026, 9, 3, 12, 1), "provider_clock.invalid"),
        (
            datetime(2026, 9, 3, 7, 1, tzinfo=timezone(timedelta(hours=-5))),
            "provider_clock.invalid",
        ),
        ("2026-09-03T12:01:00Z", "provider_clock.invalid"),
    ],
)
def test_observation_clock_and_base_freshness_fail_closed(
    module: ModuleType,
    now: Any,
    error: str,
) -> None:
    with pytest.raises(module.ProviderObservationFailure, match=error):
        _verify(module, clock=lambda: now)


def test_clock_is_sampled_once_after_one_validated_provider_response(
    module: ModuleType,
) -> None:
    response = _response(module)
    provider = FakeProvider(response)
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        assert len(provider.calls) == 1
        return OBSERVED

    _verify(module, provider=provider, clock=clock)

    assert calls == 1
    assert len(provider.calls) == 1


def test_clock_failure_is_bounded(module: ModuleType) -> None:
    secret = "clock-detail-that-must-not-leak"

    def clock() -> datetime:
        raise RuntimeError(secret)

    with pytest.raises(module.ProviderObservationFailure) as failure:
        _verify(module, clock=clock)
    assert str(failure.value) == "provider_clock.unavailable"
    assert secret not in repr(failure.value)


def test_clock_timezone_failure_is_bounded(module: ModuleType) -> None:
    secret = "clock-timezone-detail-that-must-not-leak"

    class LeakyTimezone(tzinfo):
        def utcoffset(self, _value: datetime | None) -> timedelta:
            raise RuntimeError(secret)

        def dst(self, _value: datetime | None) -> timedelta:
            return timedelta(0)

    value = datetime(2026, 9, 3, 12, 1, tzinfo=LeakyTimezone())
    with pytest.raises(module.ProviderObservationFailure) as failure:
        _verify(module, clock=lambda: value)
    assert str(failure.value) == "provider_clock.invalid"
    assert secret not in repr(failure.value)


def test_expiry_is_capped_by_the_earlier_base_expiry(module: ModuleType) -> None:
    binding = _binding(module, expires_at="2026-09-03T12:01:30Z")

    result, _verified = _verify(module, binding=binding)

    assert result["provider_observation_receipt"]["expires_at"] == (
        "2026-09-03T12:01:30Z"
    )


def test_verified_observation_rejects_public_construction_wrong_seal_and_subclass(
    module: ModuleType,
) -> None:
    _result, trusted = _verify(module)

    with pytest.raises(TypeError):
        module.VerifiedProviderObservation(
            _receipt_bytes=trusted.receipt_bytes,
            _receipt_sha256=trusted.receipt_sha256,
            _observation_seal=object(),
        )

    unsealed = module.VerifiedProviderObservation()
    with pytest.raises(
        module.ProviderObservationFailure, match="provider_observation.invalid"
    ):
        _ = unsealed.receipt

    forged = object.__new__(module.VerifiedProviderObservation)
    object.__setattr__(forged, "_receipt_bytes", trusted.receipt_bytes)
    object.__setattr__(forged, "_receipt_sha256", trusted.receipt_sha256)
    object.__setattr__(forged, "_observation_seal", object())
    with pytest.raises(
        module.ProviderObservationFailure, match="provider_observation.invalid"
    ):
        _ = forged.receipt_sha256

    class ObservationSubclass(module.VerifiedProviderObservation):
        pass

    subclass = object.__new__(ObservationSubclass)
    for name in module.VerifiedProviderObservation.__slots__:
        object.__setattr__(subclass, name, getattr(trusted, name))
    with pytest.raises(
        module.ProviderObservationFailure, match="provider_observation.invalid"
    ):
        _ = subclass.receipt_bytes


def test_tampered_sealed_receipt_bytes_fail_closed(module: ModuleType) -> None:
    _result, trusted = _verify(module)
    object.__setattr__(trusted, "_receipt_bytes", trusted.receipt_bytes + b" ")

    with pytest.raises(
        module.ProviderObservationFailure, match="provider_observation.invalid"
    ):
        _ = trusted.receipt


def test_provider_partial_cannot_cross_safe_submit_final_binding_boundary(
    module: ModuleType,
) -> None:
    safe_submit = _load_module(
        "staging_safe_submit_for_provider_observation_test", SAFE_SUBMIT_SCRIPT
    )
    _result, partial = _verify(module)

    with pytest.raises(
        safe_submit.SafeSubmitError, match="evidence_binding_type_invalid"
    ):
        safe_submit.SafeRestoreSubmitter._binding_record(partial)
