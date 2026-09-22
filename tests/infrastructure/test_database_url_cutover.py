from __future__ import annotations

import io
import json
from typing import Any

import pytest

from infra import database_url_cutover as cutover

STRONG_PASSWORD = "Aa1!" * 8


def _workload_resource(secret_ref: str) -> dict[str, Any]:
    return {
        "properties": {
            "configuration": {
                "secrets": [
                    {"name": "database-url"},
                    {"name": "database-url-v2"},
                ]
            },
            "template": {
                "containers": [
                    {
                        "env": [
                            {"name": "OTHER", "value": "safe"},
                            {
                                "name": "DATABASE_URL",
                                "secretRef": secret_ref,
                            },
                        ],
                        "name": "only-container",
                    }
                ]
            },
        }
    }


def test_cutover_sequence_is_deliberate() -> None:
    assert [workload.label for workload in cutover.WORKLOADS] == [
        "worker",
        "scheduler",
        "api",
        "migration",
    ]


def test_database_url_must_be_scoped_application_login() -> None:
    cutover.validate_database_url(
        f"postgresql://cosmaneura_app:{STRONG_PASSWORD}@"
        "cosmaneura-prod-postgres.postgres.database.azure.com:5432/"
        "production?sslmode=require",
        expected_database="production",
        expected_login_role="cosmaneura_app",
        expected_host=cutover.PRODUCTION_DATABASE_HOST,
    )

    with pytest.raises(cutover.CutoverError, match="unexpected login"):
        cutover.validate_database_url(
            f"postgresql://server_admin:{STRONG_PASSWORD}@"
            "cosmaneura-prod-postgres.postgres.database.azure.com/"
            "production?sslmode=require",
            expected_database="production",
            expected_login_role="cosmaneura_app",
            expected_host=cutover.PRODUCTION_DATABASE_HOST,
        )

    with pytest.raises(cutover.CutoverError, match="unexpected host"):
        cutover.validate_database_url(
            f"postgresql://cosmaneura_app:{STRONG_PASSWORD}@"
            "different-server.postgres.database.azure.com/"
            "production?sslmode=require",
            expected_database="production",
            expected_login_role="cosmaneura_app",
            expected_host=cutover.PRODUCTION_DATABASE_HOST,
        )

    with pytest.raises(cutover.CutoverError, match="exactly sslmode"):
        cutover.validate_database_url(
            f"postgresql://cosmaneura_app:{STRONG_PASSWORD}@"
            "cosmaneura-prod-postgres.postgres.database.azure.com/"
            "production?sslmode=require&options=-crole%3Dserver_admin",
            expected_database="production",
            expected_login_role="cosmaneura_app",
            expected_host=cutover.PRODUCTION_DATABASE_HOST,
        )

    with pytest.raises(cutover.CutoverError, match="invalid"):
        cutover.validate_database_url(
            f"postgresql://cosmaneura_app:{STRONG_PASSWORD}@"
            "cosmaneura-prod-postgres.postgres.database.azure.com:not-a-port/"
            "production?sslmode=require",
            expected_database="production",
            expected_login_role="cosmaneura_app",
            expected_host=cutover.PRODUCTION_DATABASE_HOST,
        )


def test_database_url_is_read_only_from_env_or_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CUTOVER_DATABASE_URL", raising=False)
    monkeypatch.setattr(cutover.sys, "stdin", io.StringIO("postgresql://safe\n"))
    assert (
        cutover.read_database_url(env_name="CUTOVER_DATABASE_URL", from_stdin=True)
        == "postgresql://safe"
    )


def test_migration_secret_bundle_is_strict_and_selector_bound() -> None:
    new_url = (
        f"postgresql://cosmaneura_app:{STRONG_PASSWORD}@"
        "cosmaneura-prod-postgres.postgres.database.azure.com/"
        "production?sslmode=require"
    )
    values = cutover.read_migration_secret_bundle(
        io.StringIO(json.dumps({"database-url-v2": new_url}))
    )
    cutover.validate_migration_secret_bundle(values, selected_secret="database-url-v2")

    with pytest.raises(cutover.CutoverError, match="fields"):
        cutover.read_migration_secret_bundle(
            io.StringIO(json.dumps({"database-url": "must-not-be-replaced"}))
        )
    with pytest.raises(cutover.CutoverError, match="unavailable"):
        cutover.validate_migration_secret_bundle({}, selected_secret="database-url-v2")


def test_az_subprocess_receives_no_database_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    new_secret = "postgresql://new-secret-must-not-reach-az"
    admin_secret = "postgresql://admin-secret-must-not-reach-az"
    monkeypatch.setenv("COSMANEURA_DATABASE_URL_V2", new_secret)
    monkeypatch.setenv("DATABASE_URL_V2", new_secret)
    monkeypatch.setenv("DATABASE_URL", admin_secret)

    class Result:
        returncode = 0
        stdout = "safe-output\n"
        stderr = ""

    def fake_run(command: list[str], **kwargs: Any) -> Result:
        assert all(new_secret not in argument for argument in command)
        assert all(admin_secret not in argument for argument in command)
        assert "COSMANEURA_DATABASE_URL_V2" not in kwargs["env"]
        assert "DATABASE_URL_V2" not in kwargs["env"]
        assert "DATABASE_URL" not in kwargs["env"]
        return Result()

    monkeypatch.setattr(cutover.subprocess, "run", fake_run)

    assert cutover._run_az(("account", "show")) == "safe-output"


def test_database_secret_reference_fails_closed() -> None:
    assert cutover.database_secret_reference(_workload_resource("database-url")) == (
        "database-url",
        "only-container",
    )
    duplicate = _workload_resource("database-url")
    duplicate["properties"]["template"]["containers"][0]["env"].append(
        {"name": "DATABASE_URL", "secretRef": "other"}
    )
    with pytest.raises(cutover.CutoverError, match="must have one"):
        cutover.database_secret_reference(duplicate)


def test_status_secret_names_do_not_require_secret_values() -> None:
    assert cutover.configured_secret_names(_workload_resource("database-url")) == {
        "database-url",
        "database-url-v2",
    }


def test_revision_verification_rejects_a_concurrent_latest_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workload = cutover.WORKLOADS[0]
    resource = _workload_resource("database-url-v2")
    resource["properties"]["latestRevisionName"] = f"{workload.name}--concurrent-update"
    monkeypatch.setattr(
        cutover,
        "show_workload",
        lambda *args, **kwargs: resource,
    )
    moments = iter((0.0, 1.0, 31.0))
    monkeypatch.setattr(cutover.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(cutover.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        cutover,
        "_run_az",
        lambda arguments: (_ for _ in ()).throw(
            AssertionError("wrong revision must not be inspected")
        ),
    )

    with pytest.raises(cutover.CutoverError, match="timed out"):
        cutover.verify_revision(
            workload,
            subscription_id="00000000-0000-0000-0000-000000000000",
            resource_group="cosmaneura-prod",
            expected_secret="database-url-v2",
            expected_revision_name=f"{workload.name}--requested-cutover",
            timeout_seconds=30,
        )


def test_reference_update_skips_already_selected_nonrevisioned_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = cutover.WORKLOADS[-1]
    assert not migration.revisioned
    resource = _workload_resource("database-url")
    calls: list[tuple[str, ...]] = []

    def fake_run(arguments: tuple[str, ...] | list[str]) -> str:
        calls.append(tuple(arguments))
        return json.dumps(resource)

    monkeypatch.setattr(cutover, "_run_az", fake_run)

    assert (
        cutover.update_database_reference(
            migration,
            subscription_id="00000000-0000-0000-0000-000000000000",
            resource_group="cosmaneura-prod",
            secret_name="database-url",
            revision_suffix="unused",
        )
        is None
    )
    assert len(calls) == 1
    assert "show" in calls[0]
    assert "update" not in calls[0]


def test_reference_update_requires_selected_secret_on_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = cutover.WORKLOADS[-1]
    resource = _workload_resource("database-url")
    resource["properties"]["configuration"]["secrets"] = [{"name": "database-url-v2"}]
    calls: list[tuple[str, ...]] = []

    def fake_run(arguments: tuple[str, ...] | list[str]) -> str:
        calls.append(tuple(arguments))
        return json.dumps(resource)

    monkeypatch.setattr(cutover, "_run_az", fake_run)

    with pytest.raises(cutover.CutoverError, match="selected database secret"):
        cutover.update_database_reference(
            migration,
            subscription_id="00000000-0000-0000-0000-000000000000",
            resource_group="cosmaneura-prod",
            secret_name="database-url",
            revision_suffix="unused",
        )
    assert len(calls) == 1
    assert "update" not in calls[0]


def test_all_workload_cli_calls_pin_the_confirmed_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription = "00000000-0000-0000-0000-000000000000"
    workload = cutover.WORKLOADS[0]
    revision_suffix = "d2-test-jm-n"
    revision_name = f"{workload.name}--{revision_suffix}"
    resource = _workload_resource("database-url-v2")
    resource["properties"]["latestRevisionName"] = revision_name
    calls: list[tuple[str, ...]] = []

    def fake_run(arguments: tuple[str, ...] | list[str]) -> str:
        call = tuple(arguments)
        calls.append(call)
        if "revision" in call:
            return json.dumps(
                {
                    "properties": {
                        "healthState": "Healthy",
                        "provisioningState": "Provisioned",
                        "runningState": "Running",
                    }
                }
            )
        if "show" in call:
            return json.dumps(resource)
        return ""

    monkeypatch.setattr(cutover, "_run_az", fake_run)
    assert (
        cutover.update_database_reference(
            workload,
            subscription_id=subscription,
            resource_group="cosmaneura-prod",
            secret_name="database-url-v2",
            revision_suffix=revision_suffix,
        )
        == revision_name
    )
    cutover.verify_revision(
        workload,
        subscription_id=subscription,
        resource_group="cosmaneura-prod",
        expected_secret="database-url-v2",
        expected_revision_name=revision_name,
        timeout_seconds=30,
    )

    assert calls
    assert all(
        "--subscription" in call
        and call[call.index("--subscription") + 1] == subscription
        for call in calls
    )


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_arm_secret_upsert_preserves_old_and_unrelated_secrets() -> None:
    new_value = "postgresql://credential-only-in-request-body"
    responses = iter(
        (
            {"etag": '"safe-etag"'},
            {
                "value": [
                    {"name": "database-url", "value": "old-value"},
                    {"name": "unrelated", "value": "keep-value"},
                ]
            },
            {},
            {
                "value": [
                    {"name": "database-url", "value": "old-value"},
                    {"name": "unrelated", "value": "keep-value"},
                    {"name": "database-url-v2", "value": new_value},
                ]
            },
        )
    )
    requests: list[Any] = []

    def opener(request: Any, *, timeout: int) -> FakeResponse:
        assert timeout == 120
        requests.append(request)
        return FakeResponse(next(responses))

    arm = cutover.ArmClient("opaque-token", opener=opener)
    arm.upsert_versioned_secret(
        "/subscriptions/test/resourceGroups/test/providers/Microsoft.App/"
        "containerApps/test",
        old_name="database-url",
        new_name="database-url-v2",
        new_value=new_value,
    )

    assert [request.method for request in requests] == ["GET", "POST", "PATCH", "POST"]
    assert all(new_value not in request.full_url for request in requests)
    patch_body = json.loads(requests[2].data)
    assert patch_body["properties"]["configuration"]["secrets"] == [
        {"name": "database-url", "value": "old-value"},
        {"name": "unrelated", "value": "keep-value"},
        {"name": "database-url-v2", "value": new_value},
    ]


def test_arm_reconcile_updates_named_values_and_preserves_unrelated() -> None:
    replacements = {
        "database-url-v2": "application-value",
    }
    responses = iter(
        (
            {"etag": '"safe-etag"'},
            {
                "value": [
                    {"name": "database-url", "value": "old-rollback-value"},
                    {"name": "unrelated", "value": "keep-value"},
                ]
            },
            {},
            {
                "value": [
                    {"name": "database-url", "value": "old-rollback-value"},
                    {"name": "unrelated", "value": "keep-value"},
                    {
                        "name": "database-url-v2",
                        "value": replacements["database-url-v2"],
                    },
                ]
            },
        )
    )
    requests: list[Any] = []

    def opener(request: Any, *, timeout: int) -> FakeResponse:
        requests.append(request)
        return FakeResponse(next(responses))

    arm = cutover.ArmClient("opaque-token", opener=opener)
    arm.reconcile_secret_values(
        "/subscriptions/test/resourceGroups/test/providers/Microsoft.App/jobs/test",
        replacements=replacements,
        required_names={"database-url"},
    )

    assert [request.method for request in requests] == ["GET", "POST", "PATCH", "POST"]
    patch_body = json.loads(requests[2].data)
    assert patch_body["properties"]["configuration"]["secrets"] == [
        {"name": "database-url", "value": "old-rollback-value"},
        {"name": "unrelated", "value": "keep-value"},
        {"name": "database-url-v2", "value": replacements["database-url-v2"]},
    ]


def test_arm_reconcile_required_only_needs_no_etag_or_mutation() -> None:
    requests: list[Any] = []

    def opener(request: Any, *, timeout: int) -> FakeResponse:
        requests.append(request)
        return FakeResponse(
            {
                "value": [
                    {"name": "database-url", "value": "old-rollback-value"},
                    {"name": "unrelated", "value": "keep-value"},
                ]
            }
        )

    arm = cutover.ArmClient("opaque-token", opener=opener)
    arm.reconcile_secret_values(
        "/subscriptions/test/resourceGroups/test/providers/Microsoft.App/jobs/test",
        replacements={},
        required_names={"database-url"},
    )

    assert [request.method for request in requests] == ["POST"]
    assert "/listSecrets?" in requests[0].full_url


def test_arm_reconcile_required_only_fails_when_rollback_secret_is_missing() -> None:
    requests: list[Any] = []

    def opener(request: Any, *, timeout: int) -> FakeResponse:
        requests.append(request)
        return FakeResponse({"value": [{"name": "unrelated", "value": "keep-value"}]})

    arm = cutover.ArmClient("opaque-token", opener=opener)
    with pytest.raises(cutover.CutoverError, match="rollback secret is missing"):
        arm.reconcile_secret_values(
            "/subscriptions/test/resourceGroups/test/providers/Microsoft.App/jobs/test",
            replacements={},
            required_names={"database-url"},
        )

    assert [request.method for request in requests] == ["POST"]


def test_arm_reconcile_mutation_still_requires_etag() -> None:
    requests: list[Any] = []

    def opener(request: Any, *, timeout: int) -> FakeResponse:
        requests.append(request)
        return FakeResponse({"etag": None})

    arm = cutover.ArmClient("opaque-token", opener=opener)
    with pytest.raises(cutover.CutoverError, match="ETag is missing"):
        arm.reconcile_secret_values(
            "/subscriptions/test/resourceGroups/test/providers/Microsoft.App/jobs/test",
            replacements={"database-url-v2": "application-value"},
            required_names={"database-url"},
        )

    assert [request.method for request in requests] == ["GET"]


class FakeArm:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, str, str, str]] = []
        self.reconciliations: list[tuple[str, dict[str, str], set[str]]] = []

    def upsert_versioned_secret(
        self,
        resource_id: str,
        *,
        old_name: str,
        new_name: str,
        new_value: str,
    ) -> None:
        self.upserts.append((resource_id, old_name, new_name, new_value))

    def list_secrets(self, resource_id: str) -> list[dict[str, str]]:
        return [{"name": "database-url"}, {"name": "database-url-v2"}]

    def reconcile_secret_values(
        self,
        resource_id: str,
        *,
        replacements: dict[str, str],
        required_names: set[str] | None = None,
    ) -> None:
        self.reconciliations.append(
            (resource_id, replacements, required_names or set())
        )


def test_operator_installs_secret_without_subprocess_arguments() -> None:
    arm = FakeArm()
    operator = cutover.CutoverOperator(
        arm=arm,  # type: ignore[arg-type]
        subscription_id="00000000-0000-0000-0000-000000000000",
        resource_group="cosmaneura-prod",
        old_secret_name="database-url",
        new_secret_name="database-url-v2",
        timeout_seconds=300,
    )
    secret_value = "postgresql://credential-that-must-not-be-an-argv"

    operator.install_versioned_secret(secret_value)

    assert len(arm.upserts) == len(cutover.WORKLOADS)
    assert all(item[3] == secret_value for item in arm.upserts)


def test_migration_reconcile_passes_only_secret_names_to_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arm = FakeArm()
    operator = cutover.CutoverOperator(
        arm=arm,  # type: ignore[arg-type]
        subscription_id="00000000-0000-0000-0000-000000000000",
        resource_group="cosmaneura-prod",
        old_secret_name="database-url",
        new_secret_name="database-url-v2",
        timeout_seconds=300,
    )
    values = {
        "database-url-v2": "postgresql://new-secret",
    }
    calls: list[tuple[str, str]] = []

    def update(workload: cutover.Workload, **kwargs: Any) -> None:
        serialized = json.dumps(kwargs)
        assert values["database-url-v2"] not in serialized
        calls.append((workload.label, kwargs["secret_name"]))
        return None

    monkeypatch.setattr(cutover, "update_database_reference", update)
    monkeypatch.setattr(cutover, "verify_revision", lambda *args, **kwargs: None)

    operator.reconcile_migration_job(values, selected_secret="database-url-v2")

    assert "database-url" not in values
    assert calls == [("migration", "database-url-v2")]
    assert arm.reconciliations[0][1] == values
    assert arm.reconciliations[0][2] == {"database-url"}


def test_migration_reconcile_refuses_to_replace_old_rollback_value() -> None:
    arm = FakeArm()
    operator = cutover.CutoverOperator(
        arm=arm,  # type: ignore[arg-type]
        subscription_id="00000000-0000-0000-0000-000000000000",
        resource_group="cosmaneura-prod",
        old_secret_name="database-url",
        new_secret_name="database-url-v2",
        timeout_seconds=300,
    )

    with pytest.raises(cutover.CutoverError, match="rollback secret"):
        operator.reconcile_migration_job(
            {"database-url": "postgresql://different-old-value"},
            selected_secret="database-url",
        )

    assert arm.reconciliations == []


def test_apply_rolls_back_completed_steps_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = cutover.CutoverOperator(
        arm=FakeArm(),  # type: ignore[arg-type]
        subscription_id="00000000-0000-0000-0000-000000000000",
        resource_group="cosmaneura-prod",
        old_secret_name="database-url",
        new_secret_name="database-url-v2",
        timeout_seconds=300,
    )
    monkeypatch.setattr(operator, "preflight_old_state", lambda: None)
    monkeypatch.setattr(operator, "install_versioned_secret", lambda value: None)
    calls: list[tuple[str, str]] = []

    def switch(workload: cutover.Workload, secret_name: str) -> None:
        calls.append((workload.label, secret_name))
        if workload.label == "scheduler" and secret_name == "database-url-v2":
            raise cutover.CutoverError("synthetic failure")

    monkeypatch.setattr(operator, "_switch", switch)

    with pytest.raises(cutover.CutoverError, match="rolled back"):
        operator.apply("opaque-secret")

    assert calls == [
        ("worker", "database-url-v2"),
        ("scheduler", "database-url-v2"),
        ("scheduler", "database-url"),
        ("worker", "database-url"),
    ]


def test_explicit_rollback_attempts_every_workload_after_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = cutover.CutoverOperator(
        arm=FakeArm(),  # type: ignore[arg-type]
        subscription_id="00000000-0000-0000-0000-000000000000",
        resource_group="cosmaneura-prod",
        old_secret_name="database-url",
        new_secret_name="database-url-v2",
        timeout_seconds=300,
    )
    monkeypatch.setattr(
        operator,
        "status",
        lambda: [
            {"state": "new", "workload": workload.label}
            for workload in cutover.WORKLOADS
        ],
    )
    calls: list[str] = []

    def switch(workload: cutover.Workload, secret_name: str) -> None:
        assert secret_name == "database-url"
        calls.append(workload.label)
        if workload.label == "scheduler":
            raise cutover.CutoverError("synthetic rollback failure")

    monkeypatch.setattr(operator, "_switch", switch)

    with pytest.raises(cutover.CutoverError, match="incomplete"):
        operator.rollback()

    assert calls == ["migration", "api", "scheduler", "worker"]


def test_default_main_is_read_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    subscription = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(cutover, "azure_account_subscription", lambda: subscription)
    monkeypatch.setattr(
        cutover.ArmClient,
        "from_azure_cli",
        classmethod(lambda cls: FakeArm()),
    )
    monkeypatch.setattr(
        cutover.CutoverOperator,
        "status",
        lambda self: [{"state": "old", "workload": "worker"}],
    )
    monkeypatch.setattr(
        cutover,
        "read_database_url",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("secret read")),
    )

    assert cutover.main(["--subscription-id", subscription]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "dry-run"
    assert output["result"] == "planned"


def test_mutation_requires_exact_scope_confirmation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    subscription = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(
        cutover,
        "azure_account_subscription",
        lambda: (_ for _ in ()).throw(AssertionError("Azure called")),
    )

    assert cutover.main(["--subscription-id", subscription, "--apply"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "confirmation_mismatch"


def test_apply_requires_operational_acknowledgements(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    subscription = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(
        cutover,
        "azure_account_subscription",
        lambda: (_ for _ in ()).throw(AssertionError("Azure called")),
    )

    assert (
        cutover.main(
            [
                "--subscription-id",
                subscription,
                "--apply",
                "--confirm-subscription",
                subscription,
                "--confirm-resource-group",
                "cosmaneura-prod",
            ]
        )
        == 2
    )
    assert (
        json.loads(capsys.readouterr().out)["code"]
        == "operational_acknowledgement_missing"
    )
