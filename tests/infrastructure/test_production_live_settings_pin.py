"""Drift guard for the T-CORE-PIN production live settings pin.

Uses the names-only live fixture (no values) and asserts Terraform still
projects every live name, keeps the reviewed false flags, and keeps the
pinned JSON/timeout contracts parseable. No tautologies.
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
SESSION_PREP_TF = INFRA / "session_prep_rollout.tf"
CORE_CAB_TF = INFRA / "core_cab_runtime.tf"
VARIABLES_TF = INFRA / "variables.tf"
SECRETLESS = INFRA / "tests" / "secretless_plan.tftest.hcl"
NAMES_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "production_live_env_names.json"
)

FALSE_NATIVE_FLAGS = (
    "capture_long_visits_enabled",
    "capture_recovery_enabled",
    "capture_source_enabled",
    "capture_working_transcripts_enabled",
    "combined_session_preparation_enabled",
    "encounter_creation_enabled",
    "encounter_prep_enabled",
    "zoom_durable_capture_bindings_enabled",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _names_fixture() -> dict[str, list[str]]:
    payload = json.loads(NAMES_FIXTURE.read_text(encoding="utf-8"))
    assert set(payload) == {"app", "worker", "scheduler"}
    for container, names in payload.items():
        assert names == sorted(names), f"{container} names must be sorted"
        assert names == sorted(set(names)), f"{container} names must be unique"
    return payload


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


def _default_string_list(variable_name: str) -> set[str]:
    source = _read(VARIABLES_TF)
    match = re.search(
        rf'variable "{re.escape(variable_name)}"\s*\{{.*?default\s*=\s*\[(.*?)\]',
        source,
        flags=re.S,
    )
    assert match is not None, f"missing default list for {variable_name}"
    return set(re.findall(r'"([A-Z0-9_]+)"', match.group(1)))


def _container_blocks() -> dict[str, str]:
    main = _read(MAIN_TF)
    app = main[main.index('resource "azurerm_container_app" "app"') :]
    app = app[: app.index('resource "azurerm_container_app" "scheduler"')]
    scheduler = main[main.index('resource "azurerm_container_app" "scheduler"') :]
    scheduler = scheduler[
        : scheduler.index('resource "azurerm_container_app" "worker"')
    ]
    worker = main[main.index('resource "azurerm_container_app" "worker"') :]
    return {"app": app, "worker": worker, "scheduler": scheduler}


def _env_tokens(source: str) -> set[str]:
    return set(re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", source))


def _projected_names(container: str) -> set[str]:
    blocks = _container_blocks()
    body = blocks[container]
    projected: set[str] = set(re.findall(r'name\s*=\s*"([A-Z0-9_]+)"', body))

    local_sources = {
        "scribe_native_api_public_values": NATIVE_TF,
        "zoom_durable_public_values": NATIVE_TF,
        "scribe_bundle_api_values": BUNDLE_TF,
        "scribe_bundle_worker_values": BUNDLE_TF,
        "scribe_bundle_scheduler_values": BUNDLE_TF,
        "scribe_cancellation_api_public_values": CANCELLATION_TF,
        "scribe_cancellation_shared_public_values": CANCELLATION_TF,
        "scribe_workflow_client_api_public_values": CANCELLATION_TF,
        "session_prep_shared_public_values": SESSION_PREP_TF,
        "session_prep_authority_runtime_public_values": SESSION_PREP_TF,
        "session_prep_api_public_values": SESSION_PREP_TF,
        "core_cab_public_values": CORE_CAB_TF,
        "core_cab_public_defaults": CORE_CAB_TF,
    }

    for local_name, path in local_sources.items():
        if f"local.{local_name}" not in body:
            continue
        projected |= _env_tokens(_read(path))

    if "var.python_public_vars" in body:
        projected |= _default_string_list("python_public_vars")
    if "var.python_billing_public_vars" in body:
        projected |= _default_string_list("python_billing_public_vars")
    if "var.python_secret_vars" in body:
        projected |= _default_string_list("python_secret_vars")
    if "var.python_api_only_secret_vars" in body:
        projected |= _default_string_list("python_api_only_secret_vars")
    if "var.python_billing_secret_vars" in body:
        projected |= _default_string_list("python_billing_secret_vars")
    if "var.python_billing_api_only_secret_vars" in body:
        projected |= _default_string_list("python_billing_api_only_secret_vars")

    return projected


def test_fixture_names_are_projected_per_container() -> None:
    fixture = _names_fixture()
    for container, names in fixture.items():
        projected = _projected_names(container)
        missing = sorted(set(names) - projected)
        assert missing == [], f"{container} missing Terraform projections: {missing}"


def test_pin_defaults_on_and_secretless_disables_it() -> None:
    pin = _read(PIN_TF)
    secretless = _read(SECRETLESS)
    assert 'variable "use_production_live_settings_pin"' in pin
    assert "default     = true" in pin
    assert "use_production_live_settings_pin = false" in secretless
    assert "production_live_effective_settings_valid" in pin
    assert "production_live_pin_inputs_remain_default" in pin


def test_false_native_flags_stay_off_including_capture_recovery() -> None:
    capture = _hcl_object_block(
        _read(PIN_TF), "production_live_scribe_native_production_rollout"
    )
    for flag in FALSE_NATIVE_FLAGS:
        assert _assignment_value(capture, flag) == "false", flag
    assert _assignment_value(capture, "capture_enabled") == "true"
    assert _assignment_value(capture, "capture_notes_enabled") == "true"


def test_pinned_json_blobs_and_timeout_parse() -> None:
    source = _read(PIN_TF)
    bundle = _hcl_object_block(source, "production_live_scribe_core_bundle")

    api = json.loads(json.loads(_assignment_value(bundle, "api_configuration_json")))
    receiver = json.loads(
        json.loads(_assignment_value(bundle, "receiver_configuration_json"))
    )
    speech = json.loads(
        json.loads(_assignment_value(bundle, "speech_configuration_json"))
    )
    translation = json.loads(
        json.loads(_assignment_value(bundle, "translation_configuration_json"))
    )

    assert api["schema_version"] == 1 and api["enabled"] is True
    assert receiver["authority_backend"] == "core-provider-context-v1"
    assert receiver["enabled"] is True
    assert speech["schema_version"] == 1 and speech["enabled"] is True
    assert translation["schema_version"] == 1 and translation["enabled"] is True
    assert (
        _assignment_value(source, "production_live_scribe_service_timeout_seconds")
        == "60"
    )


def test_worker_only_and_shared_bundle_names_match_fixture() -> None:
    fixture = _names_fixture()
    worker_only = sorted(
        set(fixture["worker"]) - set(fixture["app"]) - set(fixture["scheduler"])
    )
    assert worker_only == [
        "AZURE_DEID_AUTH_MODE",
        "AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID",
        "SCRIBE_BUNDLE_SPEECH_CONFIGURATION_JSON",
        "SCRIBE_BUNDLE_TRANSLATION_CONFIGURATION_JSON",
    ]
    assert "SCRIBE_BUNDLE_CAPTURED_WORKER_ENABLED" in fixture["worker"]
    assert "SCRIBE_BUNDLE_CAPTURED_WORKER_ENABLED" in fixture["scheduler"]
    assert "SCRIBE_BUNDLE_CAPTURED_WORKER_ENABLED" not in fixture["app"]


def test_staging_untouched() -> None:
    assert not (ROOT / "infra-staging" / "production_live_settings.tf").exists()
    assert "use_production_live_settings_pin" not in _read(
        ROOT / "infra-staging" / "variables.tf"
    )
