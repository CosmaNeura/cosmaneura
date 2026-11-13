"""Static drift check for the T-CORE-PIN production live settings pin.

Fails when production_live_settings.tf drops or changes a pinned live value,
or when main.tf / rollout modules stop projecting the required keys.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra"
PIN_TF = INFRA / "production_live_settings.tf"
MAIN_TF = INFRA / "main.tf"
CANCELLATION_TF = INFRA / "scribe_cancellation_rollout.tf"
NATIVE_TF = INFRA / "scribe_native_rollout.tf"
BUNDLE_TF = INFRA / "scribe_core_bundle.tf"
SECRETLESS = INFRA / "tests" / "secretless_plan.tftest.hcl"

# Live non-secret snapshot 2026-11-13 14:07 ET (bot store; not copied into repo).
PINNED = {
    "audience": "af957741-0342-498b-8496-e93706af9e99",
    "client_id": "c7bae168-fbf5-4658-b0d9-dd4b5373a1be",
    "service_base_url": "https://app.cosmaneura.com",
    "service_scope": "api://57264fe1-6a4a-4946-8412-e0be18805977/.default",
    "upload_origin": "https://cosmaneurascribeprod.blob.core.windows.net",
    "policy_sha256": "0855d28a58941adcf5d8899bb1bcfc670070e24e2b25674fbe5235a77a5f00d8",
    "receiver_contract_sha256": (
        "97008da096194099a55fa8c1f0d8e32fb0cbf3791802a8fa719f3b3f6940e19a"
    ),
    "phi_scope": "core-and-scribe-clinical-records",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _hcl_object_block(source: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}\s*=\s*\{{", source)
    assert match is not None, f"missing object assignment for {name}"
    start = match.end() - 1
    depth = 0
    for index, char in enumerate(source[start:], start=start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unclosed object for {name}")


def _assignment_value(block: str, key: str) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*(.+?)\s*$", block)
    assert match is not None, f"missing assignment {key}"
    return match.group(1).strip()


def test_production_live_settings_pin_defaults_on_and_tests_disable_it() -> None:
    pin = _read(PIN_TF)
    secretless = _read(SECRETLESS)

    assert 'variable "use_production_live_settings_pin"' in pin
    assert (
        "default     = true"
        in pin[pin.index('variable "use_production_live_settings_pin"') :]
    )
    assert "use_production_live_settings_pin = false" in secretless


def test_production_live_settings_locals_match_live_snapshot() -> None:
    source = _read(PIN_TF)

    assert "production_live_enable_scribe_workflow       = true" in source or (
        "production_live_enable_scribe_workflow" in source and "= true" in source
    )
    assert f'= "{PINNED["service_base_url"]}"' in source
    assert f'= "{PINNED["service_scope"]}"' in source

    workload = _hcl_object_block(
        source, "production_live_scribe_native_workload_rollout"
    )
    assert _assignment_value(workload, "enabled") == "true"
    assert _assignment_value(workload, "audience") == f'"{PINNED["audience"]}"'
    assert _assignment_value(workload, "client_id") == f'"{PINNED["client_id"]}"'
    assert _assignment_value(workload, "provider_context_enabled") == "true"
    assert _assignment_value(workload, "existing_patient_visits_enabled") == "true"
    assert _assignment_value(workload, "native_visit_actions_v2_enabled") == "true"

    capture = _hcl_object_block(
        source, "production_live_scribe_native_production_rollout"
    )
    assert _assignment_value(capture, "capture_enabled") == "true"
    assert _assignment_value(capture, "capture_notes_enabled") == "true"
    assert PINNED["upload_origin"] in _assignment_value(
        capture, "capture_upload_origins"
    )

    cancellation = _hcl_object_block(
        source, "production_live_scribe_cancellation_production_rollout"
    )
    assert _assignment_value(cancellation, "ledger_ready") == "true"
    assert _assignment_value(cancellation, "enabled") == "true"
    assert _assignment_value(cancellation, "grace_days") == "90"
    assert _assignment_value(cancellation, "phi_scope") == f'"{PINNED["phi_scope"]}"'
    assert (
        _assignment_value(cancellation, "policy_sha256")
        == f'"{PINNED["policy_sha256"]}"'
    )

    workflow_client = _hcl_object_block(
        source, "production_live_scribe_workflow_client_production_rollout"
    )
    assert _assignment_value(workflow_client, "identity_reuse_enabled") == "true"
    assert _assignment_value(workflow_client, "http_pool_enabled") == "true"

    bundle = _hcl_object_block(source, "production_live_scribe_core_bundle")
    assert _assignment_value(bundle, "api_enabled") == "true"
    assert _assignment_value(bundle, "captured_worker_enabled") == "true"
    assert _assignment_value(bundle, "attach_existing_translation_identity") == "true"
    assert (
        _assignment_value(bundle, "receiver_contract_sha256")
        == f'"{PINNED["receiver_contract_sha256"]}"'
    )

    api_json = json.loads(
        json.loads(_assignment_value(bundle, "api_configuration_json"))
    )
    assert api_json["schema_version"] == 1 and api_json["enabled"] is True
    receiver = json.loads(
        json.loads(_assignment_value(bundle, "receiver_configuration_json"))
    )
    assert receiver["authority_backend"] == "core-provider-context-v1"
    assert receiver["enabled"] is True


def test_effective_locals_wire_pin_into_rollout_modules() -> None:
    native = _read(NATIVE_TF)
    cancellation = _read(CANCELLATION_TF)
    bundle = _read(BUNDLE_TF)
    main = _read(MAIN_TF)
    pin = _read(PIN_TF)

    assert "local.effective_scribe_native_workload_rollout" in native
    assert "local.effective_scribe_native_production_rollout" in native
    assert "local.effective_scribe_cancellation_production_rollout" in cancellation
    assert "local.effective_scribe_workflow_client_production_rollout" in cancellation
    assert "local.effective_scribe_core_bundle" in bundle
    assert "local.effective_enable_scribe_workflow" in main
    assert "local.effective_scribe_service_base_url" in main
    assert "local.effective_scribe_service_scope" in main
    assert "var.use_production_live_settings_pin" in pin

    for key in (
        "SCRIBE_CANCELLATION_LEDGER_READY",
        "SCRIBE_CANCELLATION_ENABLED",
        "SCRIBE_CANCELLATION_GRACE_DAYS",
        "SCRIBE_CANCELLATION_PHI_SCOPE",
        "SCRIBE_CANCELLATION_POLICY_SHA256",
        "SCRIBE_WORKFLOW_IDENTITY_REUSE_ENABLED",
        "SCRIBE_WORKFLOW_HTTP_POOL_ENABLED",
    ):
        assert key in cancellation
        assert f'"{key}"' in _read(INFRA / "variables.tf")


def test_required_env_keys_remain_wired_to_each_workload() -> None:
    main = _read(MAIN_TF)
    app = main[main.index('resource "azurerm_container_app" "app"') :]
    app = app[: app.index('resource "azurerm_container_app" "scheduler"')]
    worker = main[main.index('resource "azurerm_container_app" "worker"') :]
    scheduler = main[main.index('resource "azurerm_container_app" "scheduler"') :]
    scheduler = scheduler[
        : scheduler.index('resource "azurerm_container_app" "worker"')
    ]

    assert "local.scribe_cancellation_api_public_values" in app
    assert "local.scribe_workflow_client_api_public_values" in app
    assert "local.scribe_cancellation_shared_public_values" in worker
    assert "local.scribe_cancellation_shared_public_values" in scheduler
    assert "local.scribe_workflow_client_api_public_values" not in worker
    assert "local.scribe_workflow_client_api_public_values" not in scheduler
    assert main.count("local.scribe_cancellation_api_public_values") == 1
    assert main.count("local.scribe_cancellation_shared_public_values") == 2


def test_staging_is_untouched_by_production_live_pin() -> None:
    assert not (ROOT / "infra-staging" / "production_live_settings.tf").exists()
    assert "use_production_live_settings_pin" not in _read(
        ROOT / "infra-staging" / "variables.tf"
    )
