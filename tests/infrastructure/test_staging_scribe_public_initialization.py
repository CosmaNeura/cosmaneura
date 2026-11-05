"""Pure synthetic contract checks. Never import Core application or conftest."""

from __future__ import annotations

import copy
import json
import runpy
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/staging_scribe_public_initialization.py"
M = runpy.run_path(str(SCRIPT))
API, PHASES, LEDGER = M["API"], M["PHASES"], M["LEDGER"]
NOW = datetime(2026, 11, 5, 1, tzinfo=UTC)
PRIVATE = "synthetic-opaque-retained-value\n"
OLD_IMAGE = (
    "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@sha256:" + "1" * 64
)
IMAGE = OLD_IMAGE[:-64] + "2" * 64


@pytest.fixture
def sample():
    public = {
        "ENTRA_TENANT_ID": "44444444-4444-4444-4444-444444444444",
        "AGENT_API_SOVEREIGN_CLIENT_IDS": "33333333-3333-3333-3333-333333333333",
        "AUTHORITY_CLERK_ISSUER": "https://resolved-emu-53.clerk.accounts.dev",
        "MOUNT_CONTEXT_TOKEN_TTL_SECONDS": "300",
        "MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS": "60",
        "MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS": "3600",
        "UNRELATED_ENABLED": "false",
    }
    before = {}
    for name in PHASES:
        before[name] = {
            "name": name,
            "revision": name + "--before",
            "identity": {
                "type": "UserAssigned",
                "identities": {"synthetic": {"clientId": "unchanged"}},
            },
            "configuration": {
                "activeRevisionsMode": "Single",
                "ingress": {"external": False},
                "secrets": [
                    {"name": "session-secret-key"},
                    {"name": "retained-billing-secret"},
                ],
            },
            "secret_values": {
                "session-secret-key": PRIVATE,
                "retained-billing-secret": "synthetic-retained",
            },
            "template": {
                "revisionSuffix": "before",
                "scale": {"minReplicas": 1, "maxReplicas": 3},
                "containers": [
                    {
                        "name": "core",
                        "image": OLD_IMAGE,
                        "command": [],
                        "args": ["unchanged-command"],
                        "resources": {"cpu": 1},
                        "env": [
                            {"name": key, "value": value}
                            for key, value in public.items()
                        ]
                        + [
                            {
                                "name": "SESSION_SECRET_KEY",
                                "secretRef": "session-secret-key",
                            }
                        ],
                    }
                ],
            },
        }
    controls = {
        "SCRIBE_NATIVE_WORKLOAD_ENABLED": "true",
        "SCRIBE_NATIVE_WORKLOAD_AUDIENCE": "11111111-1111-1111-1111-111111111111",
        "SCRIBE_NATIVE_WORKLOAD_CLIENT_ID": "22222222-2222-2222-2222-222222222222",
        "SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED": "true",
        "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED": "true",
        "SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED": "true",
        "SCRIBE_AUTOMATIC_ACTIVATION_ENABLED": "true",
        "SCRIBE_ACTIVATION_PRACTICE_REFS": "practice_0" + "a" * 25,
        "SCRIBE_CANCELLATION_ENABLED": "true",
        LEDGER: "true",
        "SCRIBE_CANCELLATION_GRACE_DAYS": "90",
        "SCRIBE_CANCELLATION_PHI_SCOPE": "core-and-scribe-clinical-records",
        "SCRIBE_CANCELLATION_POLICY_SHA256": "d" * 64,
    }
    bindings = {
        "candidate_sha": "a" * 40,
        "controller_sha": "b" * 40,
        "approval_pr": 967,
        "founder": "JohnClem",
        "identity_sha256": "sha256:" + "c" * 64,
        "schema_before": "ddd6261fa36f",
        "schema_after": "e78845cecca6",
        "candidate_image": IMAGE,
    }
    revisions = {name: name + "--desired" for name in PHASES}
    recovery = {
        "image": IMAGE,
        "source_sha": bindings["candidate_sha"],
        "compatibility_sha256": "sha256:" + "e" * 64,
        "revisions": {name: name + "--closed" for name in PHASES},
    }
    plan = M["prepare_plan"](
        before,
        controls,
        bindings,
        revisions,
        recovery,
        "2026-11-05T00:00:00+00:00",
        "2026-11-06T00:00:00+00:00",
    )
    context = bindings | {
        "schema_revision": bindings["schema_before"],
        "plan_sha256": M["digest"](plan),
    }
    return before, plan, context


def target(before, plan, name, recovery=False):
    section = plan["recovery"] if recovery else plan
    return M["transformed"](
        before[name],
        section["controls"],
        section["image"] if recovery else plan["bindings"]["candidate_image"],
        section["revisions"][name] if recovery else plan["desired"][name]["revision"],
    )


@pytest.mark.parametrize("already_empty", [False, True])
def test_secretref_empty_representation_is_validated_without_rewriting_baseline(
    sample, already_empty
):
    before, _, _ = sample
    state = before[API]
    entry = state["template"]["containers"][0]["env"][-1]
    if already_empty:
        entry["value"] = ""
    original = copy.deepcopy(state)
    assert M["environment"](state)[entry["name"]] == entry
    assert state == original


@pytest.mark.parametrize(
    "value", [None, False, 0, "literal", "secretref:session-secret-key", b""]
)
def test_secretref_never_accepts_nonempty_or_nonstring_literal(sample, value):
    before, _, _ = sample
    entry = before[API]["template"]["containers"][0]["env"][-1]
    entry["value"] = value
    with pytest.raises(ValueError):
        M["environment"](before[API])


@pytest.mark.parametrize("change", ["extra", "unknown-ref", "null-ref", "owned-ref"])
def test_empty_secretref_preserves_closed_ref_and_field_grammar(sample, change):
    before, _, _ = sample
    entry = before[API]["template"]["containers"][0]["env"][-1]
    entry["value"] = ""
    if change == "extra":
        entry["unowned"] = ""
    elif change == "unknown-ref":
        entry["secretRef"] = "unknown-secret"
    elif change == "null-ref":
        entry["secretRef"] = None
    else:
        entry["name"] = LEDGER
        entry["secretRef"] = LEDGER.lower().replace("_", "-")
    with pytest.raises(ValueError):
        M["environment"](before[API])


@pytest.mark.parametrize("name", PHASES)
@pytest.mark.parametrize("recovery", [False, True])
def test_target_matches_cli_empty_secretref_payload_and_preserves_all_other_state(
    sample, name, recovery
):
    before, plan, _ = sample
    state = before[name]
    entries = state["template"]["containers"][0]["env"]
    if name == PHASES[0]:
        entries[-1]["value"] = ""  # The already-updated worker representation.
    entries.insert(0, {"name": "UNRELATED_PUBLIC", "value": "preserve"})
    original = copy.deepcopy(state)
    actual = target(before, plan, name, recovery)
    section = plan["recovery"] if recovery else plan
    selected = M["FIELDS"] if name == API else {LEDGER}
    expected = copy.deepcopy(state)
    expected["revision"] = actual["revision"]
    expected["template"]["revisionSuffix"] = actual["revision"].split("--", 1)[1]
    expected["template"]["containers"][0]["image"] = actual["template"]["containers"][
        0
    ]["image"]
    expected_entries = expected["template"]["containers"][0]["env"]
    # Azure CLI's retained-container update fills missing values before merging
    # the explicit public controls. It does not replace the secret reference.
    for entry in expected_entries:
        entry.setdefault("value", "")
    for key in sorted(selected):
        expected_entries.append({"name": key, "value": section["controls"][key]})
    assert actual == expected
    assert M["state_row"](actual) == M["state_row"](expected)
    assert actual["secret_values"] == original["secret_values"]
    assert actual["configuration"] == original["configuration"]
    assert actual["identity"] == original["identity"]
    assert state == original


@pytest.mark.parametrize("presence", ["null", "absent"])
def test_real_command_null_or_absence_is_preserved_without_normalization(
    sample, presence
):
    before, plan, context = sample
    container = before[API]["template"]["containers"][0]
    if presence == "null":
        container["command"] = None
    else:
        del container["command"]
    plan = M["prepare_plan"](
        before,
        plan["controls"],
        plan["bindings"],
        {name: row["revision"] for name, row in plan["desired"].items()},
        {
            key: plan["recovery"][key]
            for key in ("image", "source_sha", "compatibility_sha256", "revisions")
        },
        plan["created_at"],
        plan["expires_at"],
    )
    context["plan_sha256"] = M["digest"](plan)
    assert M["validate_transition"](plan, before, context, NOW)["enabled"]
    after = target(before, plan, API)["template"]["containers"][0]
    assert ("command" in after) == ("command" in container)
    assert after.get("command") == container.get("command")
    changed = copy.deepcopy(before)
    changed[API]["template"]["containers"][0]["command"] = (
        [] if presence == "null" else None
    )
    with pytest.raises(ValueError):
        M["validate_transition"](plan, changed, context, NOW)


def test_dormant_default_has_no_observation_or_authority_requirement():
    assert M["validate_transition"](None, {}, {}, NOW) == {"enabled": False}


def test_complete_transition_preserves_every_unowned_field_and_raw_absence(sample):
    before, plan, context = sample
    result = M["validate_transition"](plan, before, context, NOW)
    assert result["before_sha256"] != result["desired_sha256"]
    assert result["phase_order"] == list(PHASES)
    for name in PHASES:
        after = target(before, plan, name)
        assert after["secret_values"] == before[name]["secret_values"]
        assert after["configuration"] == before[name]["configuration"]
        assert after["identity"] == before[name]["identity"]
        for key in ("args", "command", "resources"):
            assert (
                after["template"]["containers"][0][key]
                == before[name]["template"]["containers"][0][key]
            )
        assert after["template"]["scale"] == before[name]["template"]["scale"]
        unowned = [
            row
            for row in after["template"]["containers"][0]["env"]
            if row["name"] not in M["FIELDS"]
        ]
        assert unowned == [
            row | {"value": ""} if "secretRef" in row else row
            for row in before[name]["template"]["containers"][0]["env"]
        ]
        assert "SESSION_PREP_HEALTH_ALERTS_ENABLED" not in M["environment"](after)
        owned = set(M["environment"](after)) & M["FIELDS"]
        assert owned == (M["FIELDS"] if name == API else {LEDGER})


@pytest.mark.parametrize(
    "change",
    [
        "private",
        "ref",
        "secret-object",
        "duplicate-env",
        "duplicate-secret",
        "health-false",
        "args",
        "scale",
        "identity",
        "revision",
        "env-order",
    ],
)
def test_baseline_drift_and_normalization_refuse(sample, change):
    before, plan, context = sample
    state = before[API]
    if change == "private":
        state["secret_values"]["session-secret-key"] += "changed"
    elif change == "ref":
        state["template"]["containers"][0]["env"][-1]["secretRef"] = (
            "retained-billing-secret"
        )
    elif change == "secret-object":
        state["configuration"]["secrets"][0]["extra"] = "changed"
    elif change == "duplicate-env":
        state["template"]["containers"][0]["env"].append(
            copy.deepcopy(state["template"]["containers"][0]["env"][0])
        )
    elif change == "duplicate-secret":
        state["configuration"]["secrets"].append({"name": "session-secret-key"})
    elif change == "health-false":
        state["template"]["containers"][0]["env"].append(
            {"name": "SESSION_PREP_HEALTH_ALERTS_ENABLED", "value": "false"}
        )
    elif change == "args":
        state["template"]["containers"][0]["args"] = ["changed"]
    elif change == "scale":
        state["template"]["scale"]["minReplicas"] = 2
    elif change == "identity":
        state["identity"]["type"] = "SystemAssigned"
    elif change == "revision":
        state["revision"] = API + "--foreign"
    else:
        state["template"]["containers"][0]["env"].reverse()
    with pytest.raises((ValueError, KeyError)):
        M["validate_transition"](plan, before, context, NOW)


@pytest.mark.parametrize(
    "key",
    [
        "candidate_sha",
        "controller_sha",
        "founder",
        "approval_pr",
        "identity_sha256",
        "schema_revision",
        "plan_sha256",
    ],
)
def test_context_binding_refusal(sample, key):
    before, plan, context = sample
    context[key] = "wrong"
    with pytest.raises(ValueError):
        M["validate_transition"](plan, before, context, NOW)


@pytest.mark.parametrize(
    "change",
    [
        "partial",
        "unknown",
        "ledger-false",
        "wrong-native-client",
        "expired",
        "recovery-rollback",
    ],
)
def test_plan_contract_refusal(sample, change):
    before, plan, context = sample
    if change == "partial":
        del plan["controls"]["SCRIBE_CANCELLATION_POLICY_SHA256"]
    elif change == "unknown":
        plan["controls"]["STRIPE_BILLING_ENABLED"] = "true"
    elif change == "ledger-false":
        plan["controls"][LEDGER] = "false"
    elif change == "wrong-native-client":
        plan["controls"]["SCRIBE_NATIVE_WORKLOAD_CLIENT_ID"] = (
            "33333333-3333-3333-3333-333333333333"
        )
    elif change == "expired":
        plan["expires_at"] = "2026-11-05T00:30:00+00:00"
    else:
        plan["recovery"]["controls"][LEDGER] = "false"
    context["plan_sha256"] = M["digest"](plan)
    with pytest.raises((ValueError, KeyError)):
        M["validate_transition"](plan, before, context, NOW)


def test_phases_require_actual_schema_and_exact_source_bound_completed_rows(sample):
    before, plan, context = sample
    with pytest.raises(ValueError):
        M["validate_phase"](plan, before, context, [], NOW)
    context["schema_revision"] = plan["bindings"]["schema_after"]
    completed = []
    observed = copy.deepcopy(before)
    for name in PHASES:
        result = M["validate_phase"](plan, observed, context, completed, NOW)
        assert result["next_workload"] == name
        assert set(result["public_updates"]) == (
            M["FIELDS"] if name == API else {LEDGER}
        )
        updated = target(before, plan, name)
        observed[name] = updated
        with pytest.raises(ValueError):
            M["validate_phase"](plan, observed, context, completed, NOW)
        completed.append(
            {"name": name, "plan_sha256": M["digest"](plan), **plan["desired"][name]}
        )
    assert M["validate_phase"](plan, observed, context, completed, NOW)["complete"]
    completed.reverse()
    with pytest.raises(ValueError):
        M["validate_phase"](plan, observed, context, completed, NOW)


def test_api_cannot_be_adopted_before_both_processing_workloads(sample):
    before, plan, context = sample
    context["schema_revision"] = plan["bindings"]["schema_after"]
    observed = copy.deepcopy(before)
    observed[API] = target(before, plan, API)
    with pytest.raises(ValueError):
        M["validate_phase"](plan, observed, context, [], NOW)


@pytest.mark.parametrize("change", ["private", "identity", "unrelated"])
def test_completed_processing_phase_still_refuses_concurrent_drift(sample, change):
    before, plan, context = sample
    context["schema_revision"] = plan["bindings"]["schema_after"]
    name = PHASES[0]
    observed = copy.deepcopy(before)
    observed[name] = target(before, plan, name)
    completed = [
        {"name": name, "plan_sha256": M["digest"](plan), **plan["desired"][name]}
    ]
    if change == "private":
        observed[name]["secret_values"]["session-secret-key"] += "changed"
    elif change == "identity":
        observed[name]["identity"]["type"] = "SystemAssigned"
    else:
        observed[name]["template"]["containers"][0]["env"].append(
            {"name": "SESSION_PREP_HEALTH_ALERTS_ENABLED", "value": "false"}
        )
    with pytest.raises(ValueError):
        M["validate_phase"](plan, observed, context, completed, NOW)


def test_closed_recovery_preserves_read_authority_policy_private_state_and_ledger(
    sample,
):
    before, plan, context = sample
    context["schema_revision"] = plan["bindings"]["schema_after"]
    closed = {name: target(before, plan, name, recovery=True) for name in PHASES}
    result = M["validate_closed_recovery"](plan, closed, context, NOW)
    assert result["ledger_ready"] and not result["raw_template_rollback_allowed"]
    assert plan["recovery"]["controls"]["SCRIBE_NATIVE_WORKLOAD_ENABLED"] == "true"
    assert (
        plan["recovery"]["controls"]["SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED"] == "true"
    )
    for name in PHASES:
        assert closed[name]["identity"] == before[name]["identity"]
        assert closed[name]["secret_values"] == before[name]["secret_values"]
    with pytest.raises(ValueError):
        M["validate_closed_recovery"](plan, before, context, NOW)
    closed[PHASES[0]]["secret_values"]["session-secret-key"] += "changed"
    with pytest.raises(ValueError):
        M["validate_closed_recovery"](plan, closed, context, NOW)


@pytest.mark.parametrize("operation", ["phase", "recovery"])
def test_component_fingerprints_must_match_actual_bound_state(sample, operation):
    before, plan, context = sample
    name = PHASES[0]
    context["schema_revision"] = plan["bindings"]["schema_after"]
    if operation == "phase":
        observed = copy.deepcopy(before)
        observed[name] = target(before, plan, name)
        plan["desired"][name]["private_sha256"] = "sha256:" + "f" * 64
        context["plan_sha256"] = M["digest"](plan)
        completed = [
            {"name": name, "plan_sha256": M["digest"](plan), **plan["desired"][name]}
        ]
        with pytest.raises(ValueError):
            M["validate_phase"](plan, observed, context, completed, NOW)
    else:
        observed = {key: target(before, plan, key, recovery=True) for key in PHASES}
        plan["recovery"]["states"][name]["private_sha256"] = "sha256:" + "f" * 64
        context["plan_sha256"] = M["digest"](plan)
        with pytest.raises(ValueError):
            M["validate_closed_recovery"](plan, observed, context, NOW)


def test_cli_is_content_free_and_duplicate_keys_refuse():
    payload = '{"plan":null,"plan":null,"observed":{},"context":{},"completed":[]}'
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), "--validate-transition"],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert result.stderr.strip() == "staging_scribe_initialization_refused"
    assert result.stdout == ""
    assert PRIVATE not in result.stdout + result.stderr
    valid = json.dumps({"plan": None, "observed": {}, "context": {}, "completed": []})
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), "--validate-transition"],
        input=valid,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0 and json.loads(result.stdout) == {"enabled": False}
