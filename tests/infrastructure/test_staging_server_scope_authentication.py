from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/authenticate_staging_server_scope_receipt.py"
POLICY = ROOT / "contracts/staging-rehearsal/v1/server-scope-authentication-policy.json"
SERVER_POLICY = (
    ROOT / "contracts/staging-rehearsal/v1/server-scope-evidence-policy.json"
)
KEY = b"server-scope-authentication-test-key-with-32-bytes"
SOURCE_SHA = "1" * 40
AUTHENTICATED_AT = datetime(2026, 9, 3, 12, 5, tzinfo=UTC)


def _document_bytes(value: Any) -> bytes:
    return (json.dumps(value, separators=(",", ":"), ensure_ascii=True) + "\n").encode(
        "ascii"
    )


def _validated_snapshot(
    module: ModuleType,
    *,
    result_bytes: bytes,
    server_scope_bytes: bytes,
) -> Any:
    return module.evidence_manifest.ValidatedEvidenceBundle._from_validated_bytes(
        result_bytes=result_bytes,
        server_scope_bytes=server_scope_bytes,
    )


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "standalone_staging_server_scope_authentication",
        SCRIPT,
    )
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
    return _load_module()


def _configured_server_policy(module: ModuleType) -> tuple[dict[str, Any], Any]:
    collector = module.server_scope
    authentication_policy = json.loads(POLICY.read_text())
    policy = json.loads(SERVER_POLICY.read_text())
    source = authentication_policy["source"]
    primary = {
        "name": source["database_name"],
        "is_template": False,
        "allow_connections": True,
        "owner": "owner-role",
        "encoding": "UTF8",
        "collation": "C",
        "ctype": "C",
        "locale_provider": "c",
        "icu_locale": None,
        "icu_rules": None,
        "collation_version": None,
        "default_tablespace": "pg_default",
        "connection_limit": -1,
    }
    disabled_template = {
        **primary,
        "name": "template0",
        "is_template": True,
        "allow_connections": False,
    }
    server_surfaces = {name: [] for name in collector.SERVER_SURFACES}
    server_surfaces["databases"] = [primary, disabled_template]
    server_surfaces["settings"] = [
        [name, "configured", None, "default", "configured", "configured", False]
        for name in policy["required_settings"]
    ]
    database_surfaces = {name: [] for name in collector.DATABASE_SURFACES}
    snapshot = collector.ServerSnapshot(
        version_major=16,
        visibility={
            "pg_authid": True,
            "pg_monitor": True,
            "pg_read_all_settings": True,
        },
        surfaces=server_surfaces,
    )

    class Adapter:
        def start_capture(self, maximum_seconds: int) -> None:
            assert maximum_seconds == collector.MAXIMUM_CAPTURE_SECONDS

        def server_snapshot(self, required_settings: list[str]) -> Any:
            assert required_settings == policy["required_settings"]
            return snapshot

        def database_snapshot(self, database_name: str) -> dict[str, list[Any]]:
            assert database_name == primary["name"]
            return database_surfaces

    policy["activation_ready"] = True
    policy["source"].update(
        resource_id=source["resource_id"],
        revision=SOURCE_SHA,
        host="test.postgres.database.azure.com",
        primary_database=source["database_name"],
    )
    policy["hmac"]["key_version"] = authentication_policy["hmac"][
        "allowed_key_versions"
    ][0]
    policy["expected_server_surfaces"] = {
        name: collector._pin_json(
            collector._surface_pin(KEY, f"server:{name}", server_surfaces[name])
        )
        for name in collector.SERVER_SURFACES
    }
    primary_identity = collector._identity_hmac(KEY, "database", primary["name"])
    disabled_identity = collector._identity_hmac(
        KEY, "database", disabled_template["name"]
    )
    policy["expected_databases"] = [
        {
            "identity_hmac": primary_identity,
            "is_template": False,
            "allow_connections": True,
            "status": "primary_inventory",
            "surfaces": {
                name: collector._pin_json(
                    collector._surface_pin(
                        KEY,
                        f"database:{primary_identity}:{name}",
                        database_surfaces[name],
                    )
                )
                for name in collector.DATABASE_SURFACES
            },
        },
        {
            "identity_hmac": disabled_identity,
            "is_template": True,
            "allow_connections": False,
            "status": "connections_disabled",
            "surfaces": None,
        },
    ]
    return policy, Adapter()


def _fixture(module: ModuleType) -> dict[str, Any]:
    authentication_policy = json.loads(POLICY.read_text())
    authentication_policy["activation_ready"] = True
    server_policy, adapter = _configured_server_policy(module)
    server_scope_policy_bytes = _document_bytes(server_policy)
    server_scope_policy_sha256 = hashlib.sha256(server_scope_policy_bytes).hexdigest()
    authentication_policy["server_scope_policy"]["sha256"] = (
        f"sha256:{server_scope_policy_sha256}"
    )
    authentication_policy_bytes = _document_bytes(authentication_policy)
    authentication_policy_sha256 = hashlib.sha256(
        authentication_policy_bytes
    ).hexdigest()
    times = iter(
        [
            datetime(2026, 9, 3, 11, 59, tzinfo=UTC),
            datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        ]
    )
    server_receipt = module.server_scope.collect_evidence(
        server_policy,
        server_scope_policy_sha256,
        adapter,
        KEY,
        source_resource_id=authentication_policy["source"]["resource_id"],
        source_revision=SOURCE_SHA,
        key_version=authentication_policy["hmac"]["allowed_key_versions"][0],
        clock=lambda: next(times),
        monotonic=lambda: 0.0,
    )
    server_scope_receipt_sha256 = module._digest(module._canonical(server_receipt))
    binding = {
        "schema_version": 1,
        "binding_policy_id": "cosmaneura-staging-restore-evidence-binding-v1",
        "evidence_manifest_policy_id": authentication_policy["manifest_policy"][
            "policy_id"
        ],
        "evidence_manifest_policy_sha256": authentication_policy["manifest_policy"][
            "sha256"
        ],
        "manifest_id": "rehearsal-20260903",
        "manifest_file_sha256": f"sha256:{'2' * 64}",
        "bundle_index_sha256": f"sha256:{'3' * 64}",
        "evidence_set_sha256": f"sha256:{'4' * 64}",
        "source_sha": SOURCE_SHA,
        "candidate_sha": "5" * 40,
        "source_server_resource_id": authentication_policy["source"]["resource_id"],
        "source_database_name": authentication_policy["source"]["database_name"],
        "approved_restore_time": "2026-09-03T12:00:00Z",
        "observed_at": "2026-09-03T12:00:00Z",
        "expires_at": "2026-09-03T12:15:00Z",
        "candidate_approval_pr_number": 230,
        "candidate_protected_master_sha": "6" * 40,
        "candidate_schema_revision": "7" * 12,
        "candidate_receipt_sha256": f"sha256:{'8' * 64}",
        "inventory_receipt_sha256": f"sha256:{'9' * 64}",
        "quiescence_receipt_sha256": f"sha256:{'a' * 64}",
        "server_scope_receipt_sha256": server_scope_receipt_sha256,
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
    }
    result = {
        "schema_version": 1,
        "policy_id": authentication_policy["manifest_policy"]["policy_id"],
        "manifest_id": binding["manifest_id"],
        "evidence_set_sha256": binding["evidence_set_sha256"],
        "evidence_binding": binding,
        "outcome": "contract_valid",
        "activation_state": "blocked",
        "activation_blockers": ["server_scope_policy_unconfigured"],
    }
    server_scope_bytes = module._canonical(server_receipt) + b"\n"
    snapshot = _validated_snapshot(
        module,
        result_bytes=module._canonical(result),
        server_scope_bytes=server_scope_bytes,
    )
    return {
        "authentication_policy_bytes": authentication_policy_bytes,
        "authentication_policy_sha256": authentication_policy_sha256,
        "server_scope_policy_bytes": server_scope_policy_bytes,
        "server_scope_policy_sha256": server_scope_policy_sha256,
        "snapshot": snapshot,
        "key": KEY,
        "clock": lambda: AUTHENTICATED_AT,
    }


def _authenticate(module: ModuleType, fixture: dict[str, Any]) -> dict[str, Any]:
    return module._authenticate_validated_bundle(**fixture)


def test_authenticates_exact_snapshot_and_emits_deterministic_aggregate_receipt(
    module: ModuleType,
) -> None:
    fixture = _fixture(module)

    first = _authenticate(module, fixture)
    second = _authenticate(module, fixture)

    assert first == second
    receipt = first["server_scope_authentication_receipt"]
    assert set(receipt) == set(module.RECEIPT_FIELDS)
    assert first["server_scope_authentication_receipt_sha256"] == module._digest(
        module._canonical(receipt)
    )
    assert first["activation_state"] == "blocked"
    assert first["activation_blockers"] == list(module.OUTPUT_ACTIVATION_BLOCKERS)
    assert receipt["authenticated_at"] == "2026-09-03T12:05:00Z"
    rendered = json.dumps(first, sort_keys=True)
    assert KEY.decode() not in rendered
    for forbidden in ("owner-role", "template0", "test.postgres.database.azure.com"):
        assert forbidden not in rendered


def test_public_authentication_boundary_uses_canonical_disabled_policies(
    module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module.evidence_manifest,
        "validate_bundle_snapshot",
        lambda **_kwargs: pytest.fail("disabled policy must refuse before validation"),
    )

    with pytest.raises(
        module.AuthenticationFailure, match="authentication_policy.disabled"
    ):
        module.authenticate_evidence_bundle(
            bundle=(tmp_path / "missing-bundle").resolve(),
            key=KEY,
            validation_now=AUTHENTICATED_AT,
        )


def test_private_injected_path_still_validates_bundle_itself(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    fixture = _fixture(module)
    fixture.pop("snapshot")

    with pytest.raises(module.evidence_manifest.EvidenceFailure, match="bundle.unsafe"):
        module._authenticate_evidence_bundle_with_documents(
            **fixture,
            bundle=(tmp_path / "missing-bundle").resolve(),
            validation_now=AUTHENTICATED_AT,
        )


def test_wrong_key_fails_closed(module: ModuleType) -> None:
    fixture = _fixture(module)
    fixture["key"] = b"different-authentication-key-with-at-least-32-bytes"

    with pytest.raises(module.AuthenticationFailure, match="hmac"):
        _authenticate(module, fixture)


def test_nested_mutation_fails_after_unkeyed_digests_are_recomputed(
    module: ModuleType,
) -> None:
    fixture = _fixture(module)
    snapshot = fixture["snapshot"]
    receipt = deepcopy(snapshot.server_scope_receipt)
    receipt["capture_started_at"] = "2026-09-03T11:58:00Z"
    binding = deepcopy(snapshot.result["evidence_binding"])
    binding["server_scope_receipt_sha256"] = module._digest(module._canonical(receipt))
    result = deepcopy(snapshot.result)
    result["evidence_binding"] = binding
    fixture["snapshot"] = _validated_snapshot(
        module,
        result_bytes=module._canonical(result),
        server_scope_bytes=module._canonical(receipt) + b"\n",
    )

    with pytest.raises(module.AuthenticationFailure, match="hmac"):
        _authenticate(module, fixture)


def test_snapshot_bytes_must_match_the_validated_receipt(module: ModuleType) -> None:
    fixture = _fixture(module)
    snapshot = fixture["snapshot"]
    mismatched_receipt = deepcopy(snapshot.server_scope_receipt)
    mismatched_receipt["capture_started_at"] = "2026-09-03T11:58:00Z"
    fixture["snapshot"] = _validated_snapshot(
        module,
        result_bytes=snapshot.result_bytes,
        server_scope_bytes=module._canonical(mismatched_receipt) + b"\n",
    )

    with pytest.raises(
        module.AuthenticationFailure, match="server_scope.binding_mismatch"
    ):
        _authenticate(module, fixture)


@pytest.mark.parametrize(
    "mutation",
    [
        "source_resource",
        "primary_database",
        "key_version",
        "policy_digest",
    ],
)
def test_policy_and_identity_mismatches_fail_closed(
    module: ModuleType,
    mutation: str,
) -> None:
    fixture = _fixture(module)
    server_policy = json.loads(fixture["server_scope_policy_bytes"])
    expected_error: str
    if mutation == "source_resource":
        server_policy["source"]["resource_id"] += "-other"
        expected_error = "receipt.source_resource_hmac_mismatch"
    elif mutation == "primary_database":
        server_policy["source"]["primary_database"] = "postgres"
        expected_error = "receipt.primary_database_hmac_mismatch"
    elif mutation == "key_version":
        server_policy["hmac"]["key_version"] = "other-key-v1"
        expected_error = "receipt.key_version_mismatch"
    else:
        fixture["server_scope_policy_sha256"] = "f" * 64
        authentication_policy = json.loads(fixture["authentication_policy_bytes"])
        authentication_policy["server_scope_policy"]["sha256"] = f"sha256:{'f' * 64}"
        fixture["authentication_policy_bytes"] = _document_bytes(authentication_policy)
        fixture["authentication_policy_sha256"] = hashlib.sha256(
            fixture["authentication_policy_bytes"]
        ).hexdigest()
        expected_error = "policy.hash_mismatch"

    if mutation != "policy_digest":
        server_policy_bytes = _document_bytes(server_policy)
        server_policy_sha256 = hashlib.sha256(server_policy_bytes).hexdigest()
        fixture["server_scope_policy_bytes"] = server_policy_bytes
        fixture["server_scope_policy_sha256"] = server_policy_sha256
        authentication_policy = json.loads(fixture["authentication_policy_bytes"])
        authentication_policy["server_scope_policy"]["sha256"] = (
            f"sha256:{server_policy_sha256}"
        )
        fixture["authentication_policy_bytes"] = _document_bytes(authentication_policy)
        fixture["authentication_policy_sha256"] = hashlib.sha256(
            fixture["authentication_policy_bytes"]
        ).hexdigest()
        snapshot = fixture["snapshot"]
        server_receipt = snapshot.server_scope_receipt
        server_receipt["policy_sha256"] = server_policy_sha256
        unsigned_receipt = dict(server_receipt)
        unsigned_receipt.pop("evidence_hmac")
        server_receipt["evidence_hmac"] = module.server_scope.evidence_hmac(
            KEY, unsigned_receipt
        )
        result = snapshot.result
        result["evidence_binding"]["server_scope_receipt_sha256"] = module._digest(
            module._canonical(server_receipt)
        )
        fixture["snapshot"] = _validated_snapshot(
            module,
            result_bytes=module._canonical(result),
            server_scope_bytes=module._canonical(server_receipt) + b"\n",
        )

    with pytest.raises(module.AuthenticationFailure, match=expected_error):
        _authenticate(module, fixture)


@pytest.mark.parametrize(
    "mutation",
    ["schema_float", "hmac_minimum_float", "hmac_maximum_float", "lifetime_float"],
)
def test_authentication_policy_requires_exact_integer_types(
    module: ModuleType,
    mutation: str,
) -> None:
    fixture = _fixture(module)
    policy = json.loads(fixture["authentication_policy_bytes"])
    if mutation == "schema_float":
        policy["schema_version"] = 1.0
    elif mutation == "hmac_minimum_float":
        policy["hmac"]["minimum_key_bytes"] = 32.0
    elif mutation == "hmac_maximum_float":
        policy["hmac"]["maximum_key_bytes"] = 4096.0
    else:
        policy["maximum_lifetime_seconds"] = 900.0
    fixture["authentication_policy_bytes"] = _document_bytes(policy)
    fixture["authentication_policy_sha256"] = hashlib.sha256(
        fixture["authentication_policy_bytes"]
    ).hexdigest()

    with pytest.raises(
        module.AuthenticationFailure, match="authentication_policy.invalid"
    ):
        _authenticate(module, fixture)


def test_disabled_authentication_or_server_policy_cannot_emit_success(
    module: ModuleType,
) -> None:
    fixture = _fixture(module)
    authentication_policy = json.loads(fixture["authentication_policy_bytes"])
    authentication_policy["activation_ready"] = False
    fixture["authentication_policy_bytes"] = _document_bytes(authentication_policy)
    fixture["authentication_policy_sha256"] = hashlib.sha256(
        fixture["authentication_policy_bytes"]
    ).hexdigest()
    with pytest.raises(
        module.AuthenticationFailure, match="authentication_policy.disabled"
    ):
        _authenticate(module, fixture)

    fixture = _fixture(module)
    server_policy = json.loads(fixture["server_scope_policy_bytes"])
    server_policy["activation_ready"] = False
    fixture["server_scope_policy_bytes"] = _document_bytes(server_policy)
    fixture["server_scope_policy_sha256"] = hashlib.sha256(
        fixture["server_scope_policy_bytes"]
    ).hexdigest()
    authentication_policy = json.loads(fixture["authentication_policy_bytes"])
    authentication_policy["server_scope_policy"]["sha256"] = (
        f"sha256:{fixture['server_scope_policy_sha256']}"
    )
    fixture["authentication_policy_bytes"] = _document_bytes(authentication_policy)
    fixture["authentication_policy_sha256"] = hashlib.sha256(
        fixture["authentication_policy_bytes"]
    ).hexdigest()
    with pytest.raises(module.AuthenticationFailure, match="policy.unconfigured"):
        _authenticate(module, fixture)


def test_expired_snapshot_fails_closed(module: ModuleType) -> None:
    fixture = _fixture(module)
    fixture["clock"] = lambda: AUTHENTICATED_AT + timedelta(minutes=11)

    with pytest.raises(module.AuthenticationFailure, match="validated_bundle.stale"):
        _authenticate(module, fixture)


@pytest.mark.parametrize(
    "document_field",
    ["authentication_policy_bytes", "server_scope_policy_bytes"],
)
def test_policy_bytes_must_match_their_claimed_digest(
    module: ModuleType,
    document_field: str,
) -> None:
    fixture = _fixture(module)
    fixture[document_field] += b" "

    with pytest.raises(
        module.AuthenticationFailure,
        match="binding_mismatch|policy.hash_mismatch",
    ):
        _authenticate(module, fixture)


def test_bytes_subclasses_cannot_split_hashed_and_parsed_policy_content(
    module: ModuleType,
) -> None:
    class SplitBytes(bytes):
        decoded: bytes

        def __new__(cls, hashed: bytes, decoded: bytes) -> Any:
            value = super().__new__(cls, hashed)
            value.decoded = decoded
            return value

        def decode(self, *args: Any, **kwargs: Any) -> str:
            return self.decoded.decode(*args, **kwargs)

    fixture = _fixture(module)
    fixture["authentication_policy_bytes"] = SplitBytes(
        POLICY.read_bytes(), fixture["authentication_policy_bytes"]
    )
    fixture["authentication_policy_sha256"] = module.POLICY_SHA256
    with pytest.raises(
        module.AuthenticationFailure, match="authentication_policy.binding_mismatch"
    ):
        _authenticate(module, fixture)

    fixture = _fixture(module)
    authentication_policy = json.loads(fixture["authentication_policy_bytes"])
    authentication_policy["server_scope_policy"]["sha256"] = (
        f"sha256:{module.server_scope.CANONICAL_POLICY_SHA256}"
    )
    fixture["authentication_policy_bytes"] = _document_bytes(authentication_policy)
    fixture["authentication_policy_sha256"] = hashlib.sha256(
        fixture["authentication_policy_bytes"]
    ).hexdigest()
    fixture["server_scope_policy_bytes"] = SplitBytes(
        SERVER_POLICY.read_bytes(), fixture["server_scope_policy_bytes"]
    )
    fixture["server_scope_policy_sha256"] = module.server_scope.CANONICAL_POLICY_SHA256
    with pytest.raises(module.AuthenticationFailure, match="policy.hash_invalid"):
        _authenticate(module, fixture)


def test_authentication_policy_must_pin_the_exact_server_policy_bytes(
    module: ModuleType,
) -> None:
    fixture = _fixture(module)
    authentication_policy = json.loads(fixture["authentication_policy_bytes"])
    authentication_policy["server_scope_policy"]["sha256"] = f"sha256:{'f' * 64}"
    fixture["authentication_policy_bytes"] = _document_bytes(authentication_policy)
    fixture["authentication_policy_sha256"] = hashlib.sha256(
        fixture["authentication_policy_bytes"]
    ).hexdigest()

    with pytest.raises(
        module.AuthenticationFailure,
        match="authentication_policy.invalid",
    ):
        _authenticate(module, fixture)


def test_bytes_subclass_cannot_change_a_snapshot_after_hashing(
    module: ModuleType,
) -> None:
    class SplitBytes(bytes):
        decoded: bytes

        def __new__(cls, hashed: bytes, decoded: bytes) -> Any:
            value = super().__new__(cls, hashed)
            value.decoded = decoded
            return value

        def decode(self, *args: Any, **kwargs: Any) -> str:
            return self.decoded.decode(*args, **kwargs)

    fixture = _fixture(module)
    snapshot = fixture["snapshot"]
    changed_result = snapshot.result
    changed_result["evidence_binding"]["source_sha"] = "f" * 40
    with pytest.raises(
        module.evidence_manifest.EvidenceFailure, match="validated_bundle.invalid"
    ):
        _validated_snapshot(
            module,
            result_bytes=SplitBytes(
                snapshot.result_bytes,
                module._canonical(changed_result),
            ),
            server_scope_bytes=snapshot.server_scope_bytes,
        )


def test_snapshot_projection_mutation_cannot_change_authenticated_bytes(
    module: ModuleType,
) -> None:
    fixture = _fixture(module)
    snapshot = fixture["snapshot"]
    result_copy = snapshot.result
    receipt_copy = snapshot.server_scope_receipt
    result_copy["evidence_binding"]["source_sha"] = "f" * 40
    receipt_copy["source_revision"] = "f" * 40

    result = _authenticate(module, fixture)

    receipt = result["server_scope_authentication_receipt"]
    assert receipt["source_sha"] == SOURCE_SHA
    assert receipt["server_scope_evidence_hmac"] != "f" * 64


def test_collector_capture_time_is_bound_to_manifest_observation(
    module: ModuleType,
) -> None:
    fixture = _fixture(module)
    snapshot = fixture["snapshot"]
    result = snapshot.result
    result["evidence_binding"]["observed_at"] = "2026-09-04T12:00:00Z"
    result["evidence_binding"]["expires_at"] = "2026-09-04T12:15:00Z"
    fixture["snapshot"] = _validated_snapshot(
        module,
        result_bytes=module._canonical(result),
        server_scope_bytes=snapshot.server_scope_bytes,
    )
    fixture["clock"] = lambda: datetime(2026, 9, 4, 12, 5, tzinfo=UTC)

    with pytest.raises(
        module.AuthenticationFailure, match="server_scope.binding_mismatch"
    ):
        _authenticate(module, fixture)


def test_server_policy_source_must_match_authentication_policy_source(
    module: ModuleType,
) -> None:
    fixture = _fixture(module)
    alternate_resource = (
        "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/"
        "resourceGroups/cosmaneura-staging/providers/"
        "Microsoft.DBforPostgreSQL/flexibleServers/cosmaneura-attacker-postgres"
    )
    server_policy = json.loads(fixture["server_scope_policy_bytes"])
    server_policy["source"]["resource_id"] = alternate_resource
    fixture["server_scope_policy_bytes"] = _document_bytes(server_policy)
    fixture["server_scope_policy_sha256"] = hashlib.sha256(
        fixture["server_scope_policy_bytes"]
    ).hexdigest()
    authentication_policy = json.loads(fixture["authentication_policy_bytes"])
    authentication_policy["server_scope_policy"]["sha256"] = (
        f"sha256:{fixture['server_scope_policy_sha256']}"
    )
    fixture["authentication_policy_bytes"] = _document_bytes(authentication_policy)
    fixture["authentication_policy_sha256"] = hashlib.sha256(
        fixture["authentication_policy_bytes"]
    ).hexdigest()
    snapshot = fixture["snapshot"]
    server_receipt = snapshot.server_scope_receipt
    server_receipt["policy_sha256"] = fixture["server_scope_policy_sha256"]
    server_receipt["source_resource_hmac"] = module.server_scope._identity_hmac(
        KEY, "source-resource", alternate_resource
    )
    unsigned_receipt = dict(server_receipt)
    unsigned_receipt.pop("evidence_hmac")
    server_receipt["evidence_hmac"] = module.server_scope.evidence_hmac(
        KEY, unsigned_receipt
    )
    result = snapshot.result
    result["evidence_binding"]["server_scope_receipt_sha256"] = module._digest(
        module._canonical(server_receipt)
    )
    fixture["snapshot"] = _validated_snapshot(
        module,
        result_bytes=module._canonical(result),
        server_scope_bytes=module._canonical(server_receipt) + b"\n",
    )

    with pytest.raises(
        module.AuthenticationFailure, match="server_scope.binding_mismatch"
    ):
        _authenticate(module, fixture)


def test_freshness_clock_is_sampled_once_after_hmac_verification(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(module)
    verified = False
    clock_calls = 0
    original_verify = module.server_scope.verify_evidence_hmac

    def verify(*args: Any, **kwargs: Any) -> Any:
        nonlocal verified
        result = original_verify(*args, **kwargs)
        verified = True
        return result

    def clock() -> datetime:
        nonlocal clock_calls
        assert verified is True
        clock_calls += 1
        return datetime(2026, 9, 3, 12, 15, tzinfo=UTC)

    monkeypatch.setattr(module.server_scope, "verify_evidence_hmac", verify)
    fixture["clock"] = clock

    with pytest.raises(module.AuthenticationFailure, match="validated_bundle.stale"):
        _authenticate(module, fixture)
    assert clock_calls == 1


def test_authentication_policy_parser_rejects_deep_nesting_with_bounded_error(
    module: ModuleType,
) -> None:
    fixture = _fixture(module)
    raw = b"[" * 20_000 + b"]" * 20_000
    fixture["authentication_policy_bytes"] = raw
    fixture["authentication_policy_sha256"] = hashlib.sha256(raw).hexdigest()

    with pytest.raises(
        module.AuthenticationFailure, match="authentication_policy.invalid"
    ):
        _authenticate(module, fixture)


def test_authentication_policy_parser_rejects_oversized_integer_with_bounded_error(
    module: ModuleType,
) -> None:
    fixture = _fixture(module)
    raw = b'{"schema_version":' + b"9" * 5_000 + b"}"
    fixture["authentication_policy_bytes"] = raw
    fixture["authentication_policy_sha256"] = hashlib.sha256(raw).hexdigest()

    with pytest.raises(
        module.AuthenticationFailure, match="authentication_policy.invalid"
    ):
        _authenticate(module, fixture)


def test_snapshot_parser_rejects_oversized_integer_with_bounded_error(
    module: ModuleType,
) -> None:
    fixture = _fixture(module)
    snapshot = fixture["snapshot"]
    fixture["snapshot"] = _validated_snapshot(
        module,
        result_bytes=b'{"schema_version":' + b"9" * 5_000 + b"}",
        server_scope_bytes=snapshot.server_scope_bytes,
    )

    with pytest.raises(module.AuthenticationFailure, match="validated_bundle.invalid"):
        _authenticate(module, fixture)


def test_malformed_immutable_snapshot_bytes_fail_closed(module: ModuleType) -> None:
    fixture = _fixture(module)
    snapshot = fixture["snapshot"]
    fixture["snapshot"] = _validated_snapshot(
        module,
        result_bytes=b"[]",
        server_scope_bytes=snapshot.server_scope_bytes,
    )

    with pytest.raises(module.AuthenticationFailure, match="validated_bundle.invalid"):
        _authenticate(module, fixture)


def test_authenticator_rejects_snapshot_not_minted_by_validator(
    module: ModuleType,
) -> None:
    fixture = _fixture(module)
    validated = fixture["snapshot"]

    class ForgedSnapshot:
        result_bytes = validated.result_bytes
        server_scope_bytes = validated.server_scope_bytes

    fixture["snapshot"] = ForgedSnapshot()
    with pytest.raises(module.AuthenticationFailure, match="validated_bundle.invalid"):
        _authenticate(module, fixture)

    with pytest.raises(TypeError):
        module.evidence_manifest.ValidatedEvidenceBundle(
            result_bytes=validated.result_bytes,
            server_scope_bytes=validated.server_scope_bytes,
        )


def test_policy_is_hash_pinned_and_cli_refuses_before_reading_key(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy, digest = module.load_authentication_policy()
    assert hashlib.sha256(POLICY.read_bytes()).hexdigest() == module.POLICY_SHA256
    assert digest == module.POLICY_SHA256
    assert policy["activation_ready"] is False

    monkeypatch.setattr(
        module.server_scope,
        "read_hmac_key",
        lambda _path: pytest.fail("disabled policy must refuse before key access"),
    )
    assert (
        module.main(
            [
                "--bundle",
                str(tmp_path / "missing-bundle"),
                "--hmac-key-file",
                str(tmp_path / "missing-key"),
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().err) == {
        "errors": ["authentication_policy.disabled"],
        "pass": False,
    }
