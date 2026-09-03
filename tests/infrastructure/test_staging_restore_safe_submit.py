from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/staging_restore_safe_submit.py"
ENVELOPE_POLICY = ROOT / "contracts/staging-rehearsal/v1/restore-envelope-policy.json"
SAFE_POLICY = ROOT / "contracts/staging-rehearsal/v1/restore-safe-submit-policy.json"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("staging_restore_safe_submit", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


safe_submit = _load_module()
TEST_NOW = datetime(2026, 9, 3, 12, 1, tzinfo=UTC)


def _replace_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _replace_placeholders(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_replace_placeholders(nested) for nested in value]
    if isinstance(value, str) and value.startswith("CONFIGURE_EXACT_"):
        return "configured-for-pure-fake-test"
    return value


def _config(**changes: Any) -> Any:
    envelope = _replace_placeholders(json.loads(ENVELOPE_POLICY.read_text()))
    envelope["trusted_evidence_binding_implemented"] = True
    policy = json.loads(SAFE_POLICY.read_text())
    policy["activation"] = {
        "workflow_integrated": True,
        "trusted_evidence_binding_implemented": True,
    }
    config = safe_submit.config_from_policies(envelope, policy, executable=True)
    defaults = {
        "poll_interval_seconds": 0,
        "intent_poll_attempts": 2,
        "restore_poll_attempts": 2,
        "cleanup_poll_attempts": 2,
    }
    defaults.update(changes)
    return replace(config, **defaults)


def _request() -> Any:
    created = datetime(2026, 9, 3, 12, tzinfo=UTC)
    return safe_submit.RestoreRequest(
        rehearsal_id="012345abcdef",
        restore_time=created - timedelta(minutes=5),
        created_at=created,
        cleanup_not_after=created + timedelta(hours=6),
        repository="CosmaNeura/cosmaneura",
        workflow_sha="a" * 40,
        run_id="901",
        run_attempt="1",
    )


def _engine(transport: Any, **config_changes: Any) -> Any:
    return safe_submit.SafeRestoreSubmitter(
        transport,
        _config(**config_changes),
        sleeper=lambda _: None,
        clock=lambda: TEST_NOW,
    )


class FakeArm:
    def __init__(self) -> None:
        self.deployments: dict[str, dict[str, Any]] = {}
        self.target: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] = []
        self.restore_put_unknown = False
        self.deployment_put_unknown = False
        self.delete_unknown = False
        self.deployment_running_reads: dict[str, int] = {}
        self.deployment_unknown_reads: dict[str, int] = {}
        self.patch_status = 202
        self.restore_public_access = "Disabled"

    def _deployment_response(self, resource_id: str) -> Any:
        record = self.deployments[resource_id]
        state = "Succeeded"
        if self.deployment_running_reads.get(resource_id, 0):
            self.deployment_running_reads[resource_id] -= 1
            state = "Running"
        return safe_submit.ArmResponse(
            200,
            {
                "id": resource_id,
                "name": resource_id.rsplit("/", 1)[-1],
                "type": "Microsoft.Resources/deployments",
                "properties": {
                    "provisioningState": state,
                    "parameters": deepcopy(record),
                },
            },
        )

    def request(
        self,
        method: str,
        resource_id: str,
        *,
        api_version: str,
        body: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: int,
    ) -> Any:
        self.calls.append(
            {
                "method": method,
                "resource_id": resource_id,
                "api_version": api_version,
                "body": deepcopy(body),
                "headers": dict(headers or {}),
                "timeout_seconds": timeout_seconds,
            }
        )
        if "/providers/Microsoft.Resources/deployments/" in resource_id:
            if method == "GET":
                if self.deployment_unknown_reads.get(resource_id, 0):
                    self.deployment_unknown_reads[resource_id] -= 1
                    raise safe_submit.ArmOutcomeUnknown
                if resource_id not in self.deployments:
                    return safe_submit.ArmResponse(404)
                return self._deployment_response(resource_id)
            if method == "PUT":
                assert body is not None
                parameters = body["properties"]["parameters"]
                if resource_id not in self.deployments:
                    self.deployments[resource_id] = deepcopy(parameters)
                if self.deployment_put_unknown:
                    raise safe_submit.ArmOutcomeUnknown
                return safe_submit.ArmResponse(202)
        if method == "GET":
            if self.target is None:
                return safe_submit.ArmResponse(404)
            return safe_submit.ArmResponse(200, deepcopy(self.target))
        if method == "PUT":
            if self.restore_put_unknown:
                raise safe_submit.ArmOutcomeUnknown
            assert body is not None
            self.materialize(
                resource_id, body=body, public_access=self.restore_public_access
            )
            return safe_submit.ArmResponse(202)
        if method == "PATCH":
            if self.target is None:
                return safe_submit.ArmResponse(404)
            if self.patch_status in {200, 202, 204}:
                self.target["properties"]["network"]["publicNetworkAccess"] = "Disabled"
            return safe_submit.ArmResponse(self.patch_status)
        if method == "DELETE":
            if self.delete_unknown:
                raise safe_submit.ArmOutcomeUnknown
            self.target = None
            return safe_submit.ArmResponse(202)
        raise AssertionError(f"unhandled fake ARM request: {method} {resource_id}")

    def materialize(
        self,
        resource_id: str,
        *,
        body: dict[str, Any] | None = None,
        tags: dict[str, str] | None | object = ...,  # type: ignore[assignment]
        source_server_resource_id: str | None = None,
        public_access: str = "Disabled",
        state: str = "Ready",
        include_create_fields: bool = True,
        created_by: str = "configured-for-pure-fake-test",
    ) -> None:
        assert body is not None or source_server_resource_id is not None
        name = resource_id.rsplit("/", 1)[-1]
        properties: dict[str, Any]
        if body:
            properties = deepcopy(body["properties"])
        elif include_create_fields:
            properties = {
                "createMode": "PointInTimeRestore",
                "sourceServerResourceId": source_server_resource_id,
                "pointInTimeUTC": "2026-09-03T11:55:00Z",
                "network": {"publicNetworkAccess": public_access},
            }
        else:
            properties = {"network": {"publicNetworkAccess": public_access}}
        properties["state"] = state
        properties.setdefault("network", {})["publicNetworkAccess"] = public_access
        target: dict[str, Any] = {
            "id": resource_id,
            "name": name,
            "type": "Microsoft.DBforPostgreSQL/flexibleServers",
            "location": "northcentralus",
            "etag": '"target-etag"',
            "properties": properties,
            "systemData": {
                "createdAt": "2026-09-03T12:00:00.123456Z",
                "createdBy": created_by,
                "createdByType": "Application",
            },
        }
        if tags is ...:
            target["tags"] = deepcopy(body["tags"] if body else {})
        elif tags is not None:
            target["tags"] = deepcopy(tags)
        self.target = target


def _target_id(engine: Any, request: Any) -> str:
    return engine._build_intent(request)["target_server_resource_id"]


def _mutations(fake: FakeArm, method: str) -> list[dict[str, Any]]:
    return [
        call
        for call in fake.calls
        if call["method"] == method
        and "/providers/Microsoft.Resources/deployments/" not in call["resource_id"]
    ]


@pytest.mark.parametrize(
    "payload",
    [b"{", b"x" * 1_048_577],
    ids=["malformed-json", "oversized-body"],
)
def test_successful_mutation_with_unreadable_response_is_unknown(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    class HttpResponse:
        status = 202
        headers: dict[str, str] = {"x-ms-request-id": "test-request"}

        def __enter__(self) -> HttpResponse:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def read(self, _: int) -> bytes:
            return payload

    transport = safe_submit.AzureArmTransport()
    monkeypatch.setattr(transport, "_access_token", lambda _: "test-token")
    monkeypatch.setattr(
        safe_submit.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: HttpResponse(),
    )
    with pytest.raises(safe_submit.ArmOutcomeUnknown):
        transport.request(
            "PUT",
            "/subscriptions/test/resourceGroups/test/providers/Test/type/name",
            api_version="2024-08-01",
            body={"properties": {}},
            timeout_seconds=30,
        )


def test_canonical_contract_is_pinned_and_dormant() -> None:
    envelope, policy = safe_submit.validate_canonical_contracts()
    assert envelope["trusted_evidence_binding_implemented"] is False
    assert tuple(sorted(safe_submit._placeholder_values(envelope))) == (
        safe_submit.EXPECTED_PLACEHOLDERS
    )
    assert policy["activation"] == {
        "workflow_integrated": False,
        "trusted_evidence_binding_implemented": False,
    }
    with pytest.raises(safe_submit.SafeSubmitError, match="safe_submit_not_activated"):
        safe_submit.config_from_policies(envelope, policy, executable=True)


def test_accepted_restore_persists_both_markers_before_exact_arm_put() -> None:
    fake = FakeArm()
    engine = _engine(fake)
    outcome = engine.submit(_request())
    assert outcome.status == "ready"
    assert outcome.cleanup_required is False
    writes = [call for call in fake.calls if call["method"] == "PUT"]
    assert len(writes) == 3
    assert all(
        "/providers/Microsoft.Resources/deployments/" in call["resource_id"]
        for call in writes[:2]
    )
    restore = writes[2]
    assert restore["api_version"] == "2024-08-01"
    assert restore["headers"] == {"If-None-Match": "*"}
    assert restore["timeout_seconds"] == 30
    assert restore["body"]["properties"]["network"] == {
        "publicNetworkAccess": "Disabled"
    }
    assert len(fake.deployments) == 2


def test_unknown_submission_never_claims_absence_or_completion() -> None:
    fake = FakeArm()
    fake.restore_put_unknown = True
    engine = _engine(fake)
    outcome = engine.submit(_request())
    assert outcome.status == "pending_late_materialization"
    assert outcome.target_observed is False
    assert outcome.cleanup_required is True
    assert len(fake.deployments) == 3
    assert len(_mutations(fake, "PUT")) == 1
    assert _mutations(fake, "DELETE") == []
    retry = engine.submit(_request())
    assert retry.status == "pending_late_materialization"
    assert len(_mutations(fake, "PUT")) == 1


def test_expired_fresh_intent_is_rejected_before_any_target_access() -> None:
    fake = FakeArm()
    engine = safe_submit.SafeRestoreSubmitter(
        fake,
        _config(),
        sleeper=lambda _: None,
        clock=lambda: datetime(2026, 9, 3, 18, tzinfo=UTC),
    )
    with pytest.raises(safe_submit.SafeSubmitError, match="intent_expired"):
        engine.submit(_request())
    assert all(
        "/providers/Microsoft.Resources/deployments/" in call["resource_id"]
        for call in fake.calls
    )
    assert [call["method"] for call in fake.calls] == ["GET"]


def test_expiry_during_restore_polling_persists_cleanup_and_deletes_target() -> None:
    fake = FakeArm()
    before_expiry = TEST_NOW
    at_expiry = datetime(2026, 9, 3, 18, tzinfo=UTC)
    times = iter(
        [before_expiry, before_expiry, before_expiry, before_expiry, at_expiry]
    )
    engine = safe_submit.SafeRestoreSubmitter(
        fake, _config(), sleeper=lambda _: None, clock=lambda: next(times)
    )
    outcome = engine.submit(_request())
    assert outcome.status == "cleaned"
    assert outcome.cleanup_required is False
    assert len(fake.deployments) == 3
    assert len(_mutations(fake, "PUT")) == 1
    assert len(_mutations(fake, "DELETE")) == 1
    assert fake.target is None


def test_expiry_during_final_cleanup_read_blocks_target_put() -> None:
    fake = FakeArm()
    before_expiry = TEST_NOW
    at_expiry = datetime(2026, 9, 3, 18, tzinfo=UTC)
    times = iter([before_expiry, before_expiry, before_expiry, at_expiry])
    engine = safe_submit.SafeRestoreSubmitter(
        fake, _config(), sleeper=lambda _: None, clock=lambda: next(times)
    )
    outcome = engine.submit(_request())
    assert outcome.status == "pending_late_materialization"
    assert outcome.cleanup_required is True
    assert len(fake.deployments) == 3
    assert _mutations(fake, "PUT") == []


def test_cleanup_tombstone_recheck_immediately_before_put_wins_race() -> None:
    class CleanupRaceArm(FakeArm):
        def __init__(self) -> None:
            super().__init__()
            self.cleanup_marker_id = ""
            self.cleanup_parameters: dict[str, Any] = {}
            self.cleanup_reads = 0

        def request(self, *args: Any, **kwargs: Any) -> Any:
            method, resource_id = args
            if method == "GET" and resource_id == self.cleanup_marker_id:
                self.cleanup_reads += 1
                if self.cleanup_reads == 2:
                    self.deployments[resource_id] = deepcopy(self.cleanup_parameters)
            return super().request(*args, **kwargs)

    fake = CleanupRaceArm()
    engine = _engine(fake)
    intent = engine._build_intent(_request())
    digest = f"sha256:{safe_submit._sha256(safe_submit._canonical(intent))}"
    fake.cleanup_marker_id = engine._cleanup_marker_id(intent["rehearsal_id"])
    fake.cleanup_parameters = deepcopy(
        engine._deployment_body(engine._cleanup_record(intent, digest))["properties"][
            "parameters"
        ]
    )
    outcome = engine.submit(_request())
    assert outcome.status == "pending_late_materialization"
    assert outcome.cleanup_required is True
    assert fake.cleanup_reads >= 2
    assert _mutations(fake, "PUT") == []


def test_cleanup_tombstone_created_during_put_wins_after_dispatch() -> None:
    class CleanupDuringPutArm(FakeArm):
        def __init__(self) -> None:
            super().__init__()
            self.cleanup_marker_id = ""
            self.cleanup_parameters: dict[str, Any] = {}

        def request(self, *args: Any, **kwargs: Any) -> Any:
            method, resource_id = args
            if (
                method == "PUT"
                and "/providers/Microsoft.Resources/deployments/" not in resource_id
            ):
                self.deployments[self.cleanup_marker_id] = deepcopy(
                    self.cleanup_parameters
                )
            return super().request(*args, **kwargs)

    fake = CleanupDuringPutArm()
    engine = _engine(fake)
    intent = engine._build_intent(_request())
    digest = f"sha256:{safe_submit._sha256(safe_submit._canonical(intent))}"
    fake.cleanup_marker_id = engine._cleanup_marker_id(intent["rehearsal_id"])
    fake.cleanup_parameters = deepcopy(
        engine._deployment_body(engine._cleanup_record(intent, digest))["properties"][
            "parameters"
        ]
    )
    outcome = engine.submit(_request())
    assert outcome.status == "cleaned"
    assert outcome.cleanup_required is False
    assert len(_mutations(fake, "PUT")) == 1
    assert len(_mutations(fake, "DELETE")) == 1
    assert fake.target is None


def test_retry_reconciles_the_exact_resource_without_a_second_restore_put() -> None:
    fake = FakeArm()
    engine = _engine(fake)
    first = engine.submit(_request())
    second = engine.submit(_request())
    assert first.status == second.status == "ready"
    assert first.target_resource_id == second.target_resource_id
    assert len(_mutations(fake, "PUT")) == 1
    assert len(fake.deployments) == 2


def test_cleanup_tombstone_prevents_a_retry_from_recreating_the_target() -> None:
    fake = FakeArm()
    engine = _engine(fake)
    ready = engine.submit(_request())
    assert ready.status == "ready"
    cleaned = engine.cleanup_intent("012345abcdef")
    assert cleaned.status == "cleaned"
    retry = engine.submit(_request())
    assert retry.status == "pending_late_materialization"
    assert retry.cleanup_required is True
    assert len(_mutations(fake, "PUT")) == 1
    assert len(fake.deployments) == 3


def test_running_intent_deployment_is_polled_instead_of_replaced() -> None:
    fake = FakeArm()
    engine = _engine(fake, intent_poll_attempts=3)
    intent = engine._build_intent(_request())
    marker_id = engine._intent_marker_id("012345abcdef")
    fake.deployments[marker_id] = deepcopy(
        engine._deployment_body(intent)["properties"]["parameters"]
    )
    fake.deployment_running_reads[marker_id] = 2
    assert engine._ensure_deployment(marker_id, intent) == intent
    marker_calls = [call for call in fake.calls if call["resource_id"] == marker_id]
    assert [call["method"] for call in marker_calls] == ["GET", "GET", "GET"]


def test_cleanup_polls_running_intent_and_submission_records() -> None:
    fake = FakeArm()
    engine = _engine(fake, intent_poll_attempts=3)
    assert engine.submit(_request()).status == "ready"
    intent_id = engine._intent_marker_id("012345abcdef")
    submission_id = engine._submission_marker_id("012345abcdef")
    fake.deployment_running_reads[intent_id] = 2
    fake.deployment_running_reads[submission_id] = 2
    assert engine.cleanup_intent("012345abcdef").status == "cleaned"
    assert len(_mutations(fake, "DELETE")) == 1


@pytest.mark.parametrize("tags", [None, {}, {"rehearsal-id": "012345abcdef"}])
def test_late_materialization_without_complete_tags_is_isolated_and_deleted(
    tags: dict[str, str] | None,
) -> None:
    fake = FakeArm()
    fake.restore_put_unknown = True
    engine = _engine(fake)
    pending = engine.submit(_request())
    assert pending.status == "pending_late_materialization"
    fake.restore_put_unknown = False
    fake.materialize(
        pending.target_resource_id,
        tags=tags,
        source_server_resource_id=engine.config.source_server_id,
        public_access="Enabled",
        state="Creating",
        include_create_fields=False,
    )
    cleaned = engine.cleanup_intent("012345abcdef")
    assert cleaned.status == "cleaned"
    assert cleaned.target_observed is True
    assert len(_mutations(fake, "PATCH")) >= 1
    assert len(_mutations(fake, "DELETE")) == 1
    assert fake.target is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda target: target["properties"].update(
            sourceServerResourceId="/subscriptions/wrong/source"
        ),
        lambda target: target.setdefault("tags", {}).update(
            {"rehearsal-id": "ffffffffffff"}
        ),
        lambda target: target.update(type="Microsoft.Storage/storageAccounts"),
        lambda target: target["systemData"].update(createdBy="wrong-principal"),
    ],
)
def test_cleanup_refuses_a_wrong_resource_before_any_mutation(mutate: Any) -> None:
    fake = FakeArm()
    fake.restore_put_unknown = True
    engine = _engine(fake)
    pending = engine.submit(_request())
    fake.materialize(
        pending.target_resource_id,
        tags=None,
        source_server_resource_id=engine.config.source_server_id,
        public_access="Enabled",
    )
    assert fake.target is not None
    mutate(fake.target)
    with pytest.raises(safe_submit.SafeSubmitError):
        engine.cleanup_intent("012345abcdef")
    assert _mutations(fake, "PATCH") == []
    assert _mutations(fake, "DELETE") == []


def test_cleanup_without_a_durable_intent_is_fail_closed() -> None:
    fake = FakeArm()
    engine = _engine(fake)
    target_id = _target_id(engine, _request())
    fake.materialize(
        target_id,
        tags=None,
        source_server_resource_id=engine.config.source_server_id,
        public_access="Enabled",
    )
    with pytest.raises(safe_submit.SafeSubmitError, match="intent_record_missing"):
        engine.cleanup_intent("012345abcdef")
    assert _mutations(fake, "PATCH") == []
    assert _mutations(fake, "DELETE") == []


def test_isolation_refusal_still_attempts_guarded_deletion() -> None:
    fake = FakeArm()
    fake.restore_put_unknown = True
    engine = _engine(fake)
    pending = engine.submit(_request())
    fake.materialize(
        pending.target_resource_id,
        tags=None,
        source_server_resource_id=engine.config.source_server_id,
        public_access="Enabled",
        include_create_fields=False,
    )
    fake.patch_status = 403
    cleaned = engine.cleanup_intent("012345abcdef")
    assert cleaned.status == "cleaned"
    assert len(_mutations(fake, "PATCH")) == 1
    assert len(_mutations(fake, "DELETE")) == 1


def test_submit_enters_cleanup_immediately_on_definite_isolation_refusal() -> None:
    fake = FakeArm()
    fake.restore_public_access = "Enabled"
    fake.patch_status = 403
    engine = _engine(fake, restore_poll_attempts=3)
    outcome = engine.submit(_request())
    assert outcome.status == "cleaned"
    assert len(_mutations(fake, "PATCH")) == 2
    assert len(_mutations(fake, "DELETE")) == 1


def test_unknown_submission_record_never_claims_prepared_only() -> None:
    fake = FakeArm()
    engine = _engine(fake)
    assert engine.submit(_request()).status == "ready"
    fake.target = None
    submission_id = engine._submission_marker_id("012345abcdef")
    fake.deployment_unknown_reads[submission_id] = 2
    outcome = engine.cleanup_intent("012345abcdef")
    assert outcome.status == "submission_record_unknown"
    assert outcome.cleanup_required is True
    assert len(fake.deployments) == 3
    assert _mutations(fake, "DELETE") == []


def test_unproven_intent_persistence_blocks_restore_submission() -> None:
    class LostDeploymentArm(FakeArm):
        def request(self, *args: Any, **kwargs: Any) -> Any:
            method, resource_id = args
            if (
                method == "PUT"
                and "/providers/Microsoft.Resources/deployments/" in resource_id
            ):
                self.calls.append(
                    {
                        "method": method,
                        "resource_id": resource_id,
                        "api_version": kwargs["api_version"],
                        "body": deepcopy(kwargs.get("body")),
                        "headers": dict(kwargs.get("headers") or {}),
                        "timeout_seconds": kwargs["timeout_seconds"],
                    }
                )
                raise safe_submit.ArmOutcomeUnknown
            return super().request(*args, **kwargs)

    fake = LostDeploymentArm()
    engine = _engine(fake)
    with pytest.raises(
        safe_submit.SafeSubmitError, match="intent_persistence_unproven"
    ):
        engine.submit(_request())
    assert _mutations(fake, "PUT") == []
