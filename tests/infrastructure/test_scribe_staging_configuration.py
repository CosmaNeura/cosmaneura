"""Qualify native staging admission using pure controls and actual Bash projection."""

import itertools
import json
import os
import runpy
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "scripts/staging_release_contract.py"
m = runpy.run_path(str(CONTROL))
existing = runpy.run_path(
    str(ROOT / "tests/infrastructure/test_staging_release_contract.py")
)
project = runpy.run_path(
    str(ROOT / "tests/infrastructure/test_companion_staging_configuration.py")
)["project"]
NATIVE = {
    "SCRIBE_NATIVE_WORKLOAD_ENABLED": "true",
    "SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED": "true",
    "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED": "true",
    "SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED": "true",
    "SCRIBE_NATIVE_WORKLOAD_AUDIENCE": "44444444-4444-4444-4444-444444444444",
    "SCRIBE_NATIVE_WORKLOAD_CLIENT_ID": "22222222-2222-2222-2222-222222222222",
}
ACTIVATION = {
    "SCRIBE_AUTOMATIC_ACTIVATION_ENABLED": "true",
    "SCRIBE_ACTIVATION_PRACTICE_REFS": "practice_00000000000000000000000001",
}
CANCELLATION = {
    "SCRIBE_CANCELLATION_ENABLED": "true",
    "SCRIBE_CANCELLATION_LEDGER_READY": "true",
    "SCRIBE_CANCELLATION_GRACE_DAYS": "90",
    "SCRIBE_CANCELLATION_PHI_SCOPE": "core-and-scribe-clinical-records",
    "SCRIBE_CANCELLATION_POLICY_SHA256": "a" * 64,
}
NEW = NATIVE | ACTIVATION | CANCELLATION
BINDING = {
    "ENTRA_TENANT_ID": "55555555-5555-5555-5555-555555555555",
    "AGENT_API_SOVEREIGN_CLIENT_IDS": "11111111-1111-1111-1111-111111111111",
    "AUTHORITY_CLERK_ISSUER": "https://resolved-emu-53.clerk.accounts.dev",
    "MOUNT_CONTEXT_TOKEN_TTL_SECONDS": "900",
    "MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS": "300",
    "MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS": "28800",
}


def cli(candidate, mode="--validate-runtime-public"):
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(CONTROL), mode],
        input=json.dumps(candidate),
        capture_output=True,
        text=True,
        timeout=10,
        env={},
        check=False,
    )


def withdrawn():
    result = dict(NEW)
    for key in m["NATIVE_FLAGS"] | {
        "SCRIBE_AUTOMATIC_ACTIVATION_ENABLED",
        "SCRIBE_CANCELLATION_ENABLED",
    }:
        result[key] = "false"
    for key in m["NATIVE_FIELDS"] - m["NATIVE_FLAGS"]:
        result[key] = ""
    result["SCRIBE_ACTIVATION_PRACTICE_REFS"] = ""
    # Closing intake never closes the migrated processing fence.
    return result


@pytest.mark.parametrize("selected", list(itertools.product([False, True], repeat=3)))
def test_groups_are_independently_optional_and_do_not_mutate_legacy_plans(selected):
    candidate = existing["companion_runtime"]()
    before = dict(candidate)
    m["validate_runtime"](candidate)
    assert candidate == before
    dark = withdrawn()
    for admitted, group in zip(
        selected, (m["NATIVE_FIELDS"], m["ACTIVATION_FIELDS"], m["CANCELLATION_FIELDS"])
    ):
        if admitted:
            candidate.update({key: dark[key] for key in group})
    before = dict(candidate)
    result = cli(candidate)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == before == candidate


@pytest.mark.parametrize("withdraw", [False, True])
def test_enabled_native_groups_and_closed_intake_preserve_exact_values(withdraw):
    candidate = existing["runtime"]() | (withdrawn() if withdraw else NEW)
    result = cli(candidate)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == candidate
    binding = cli(
        (withdrawn() if withdraw else NEW) | BINDING, "--validate-native-binding"
    )
    assert (binding.returncode, binding.stdout, binding.stderr) == (0, "", "")


@pytest.mark.parametrize("missing", sorted(NEW))
def test_partial_groups_fail_without_emitting_input(missing):
    candidate = existing["runtime"]() | NEW
    del candidate[missing]
    result = cli(candidate)
    assert (result.returncode, result.stdout, result.stderr) == (
        1,
        "",
        "staging_release_contract_refused\n",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"SCRIBE_NATIVE_WORKLOAD_ENABLED": "TRUE"},
        {"SCRIBE_NATIVE_WORKLOAD_ENABLED": "false"},
        {"SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED": "false"},
        {"SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED": "false"},
        {"SCRIBE_NATIVE_WORKLOAD_AUDIENCE": "00000000-0000-0000-0000-000000000000"},
        {"SCRIBE_NATIVE_WORKLOAD_CLIENT_ID": "22222222222222222222222222222222"},
        {"SCRIBE_AUTOMATIC_ACTIVATION_ENABLED": True},
        {"SCRIBE_ACTIVATION_PRACTICE_REFS": ""},
        {"SCRIBE_ACTIVATION_PRACTICE_REFS": "*"},
        {
            "SCRIBE_ACTIVATION_PRACTICE_REFS": ",".join(
                "practice_" + f"{i:026d}" for i in range(33)
            )
        },
        {
            "SCRIBE_ACTIVATION_PRACTICE_REFS": ACTIVATION[
                "SCRIBE_ACTIVATION_PRACTICE_REFS"
            ]
            + ","
            + ACTIVATION["SCRIBE_ACTIVATION_PRACTICE_REFS"]
        },
        {"SCRIBE_ACTIVATION_PRACTICE_REFS": "practice_80000000000000000000000001"},
        {"SCRIBE_ACTIVATION_PRACTICE_REFS": " practice_00000000000000000000000001"},
        {"SCRIBE_CANCELLATION_LEDGER_READY": "false"},
        {"SCRIBE_CANCELLATION_GRACE_DAYS": "29"},
        {"SCRIBE_CANCELLATION_GRACE_DAYS": "91"},
        {"SCRIBE_CANCELLATION_GRACE_DAYS": "090"},
        {"SCRIBE_CANCELLATION_PHI_SCOPE": "shared-login"},
        {"SCRIBE_CANCELLATION_POLICY_SHA256": "sha256:" + "a" * 64},
        {"SCRIBE_CANCELLATION_POLICY_SHA256": "A" * 64},
        {"SCRIBE_BUNDLE_API_ENABLED": "true"},
        {"DATABASE_URL": "synthetic-do-not-emit"},
    ],
)
def test_native_runtime_refuses_incomplete_authority_unbounded_cohorts_and_policy(
    changes,
):
    result = cli(existing["runtime"]() | NEW | changes)
    assert (result.returncode, result.stdout, result.stderr) == (
        1,
        "",
        "staging_release_contract_refused\n",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"ENTRA_TENANT_ID": "00000000-0000-0000-0000-000000000000"},
        {"SCRIBE_NATIVE_WORKLOAD_CLIENT_ID": BINDING["AGENT_API_SOVEREIGN_CLIENT_IDS"]},
        {"AUTHORITY_CLERK_ISSUER": "https://production.example"},
        {"MOUNT_CONTEXT_TOKEN_TTL_SECONDS": "299"},
        {"MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS": "900"},
        {"MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS": "899"},
        {"DATABASE_URL": "synthetic-do-not-emit"},
    ],
)
def test_native_binding_refuses_tool_caller_overlap_and_wrong_environment(changes):
    result = cli(NEW | BINDING | changes, "--validate-native-binding")
    assert (result.returncode, result.stdout, result.stderr) == (
        1,
        "",
        "staging_release_contract_refused\n",
    )


@pytest.mark.parametrize("release", [None, {}])
def test_omission_never_admits_inherited_native_access_or_activation(release):
    payload = project(release, NEW)
    assert not (NEW.keys() & payload["configuration"].keys())


@pytest.mark.parametrize("withdraw", [False, True])
def test_actual_projection_keeps_native_intake_api_only_and_ledger_fences_everywhere(
    withdraw,
):
    values = withdrawn() if withdraw else NEW
    payload = project(existing["runtime"]() | values)
    runtime = runpy.run_path(str(ROOT / "scripts/staging_runtime_configuration.py"))
    expected = {key: value for key, value in values.items() if value}
    baseline = runtime["fingerprint"](payload)
    for contract in payload["contracts"]:
        environment = runtime["desired"](payload["configuration"], contract)[
            "environment"
        ]
        actual = {key: value for key, value in environment.items() if key in NEW}
        if contract["name"] == "cosmaneura-staging-app":
            assert actual == expected
        else:
            assert actual == {"SCRIBE_CANCELLATION_LEDGER_READY": "true"}
    for key, value in values.items():
        altered = dict(payload["configuration"], **{key: value + "-unreviewed"})
        assert runtime["fingerprint"](payload | {"configuration": altered}) != baseline


def test_actual_native_binding_projection_excludes_unrelated_configuration():
    source = (ROOT / "infra-staging/deploy.sh").read_text()
    start = source.index(
        "# Bind native admission to the existing staging public identity configuration."
    )
    block = source[start : source.index("\nemit_github_output()", start)]
    # Use the real projection but replace its validator with an argv-fixed,
    # local observer. No resolver or provider is invoked.
    observer = "import json,sys; print(json.dumps(sorted(json.load(sys.stdin))))"
    block = block.replace(
        '"$REPOSITORY_ROOT/scripts/staging_release_contract.py" '
        + "\\\n\t--validate-native-binding",
        "-c " + shlex.quote(observer),
    )

    result = subprocess.run(
        ["bash", "-ec", block],
        env={
            "PATH": os.environ["PATH"],
            "PYTHON_BIN": sys.executable,
            "ENV_JSON": json.dumps(
                NEW | BINDING | {"DATABASE_URL": "synthetic-do-not-emit"}
            ),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == sorted(NEW | BINDING)
    assert "synthetic-do-not-emit" not in result.stdout + result.stderr


@pytest.mark.parametrize("valid", [True, False])
def test_actual_deployer_guard_validates_merged_public_identity_before_mutation(valid):
    source = (ROOT / "infra-staging/deploy.sh").read_text()
    start = source.index(
        "# Bind native admission to the existing staging public identity configuration."
    )
    block = source[start : source.index("\nemit_github_output()", start)]
    configuration = NEW | BINDING
    if not valid:
        configuration["SCRIBE_NATIVE_WORKLOAD_CLIENT_ID"] = BINDING[
            "AGENT_API_SOVEREIGN_CLIENT_IDS"
        ]
    result = subprocess.run(
        ["bash", "-ec", block],
        env={
            "PATH": os.environ["PATH"],
            "PYTHON_BIN": sys.executable,
            "REPOSITORY_ROOT": str(ROOT),
            "ENV_JSON": json.dumps(
                configuration | {"DATABASE_URL": "synthetic-do-not-emit"}
            ),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == (0 if valid else 1)
    assert result.stdout == ""
    assert result.stderr == (
        ""
        if valid
        else "staging_release_contract_refused\nStaging native public configuration binding was refused.\n"
    )
    assert "synthetic-do-not-emit" not in result.stdout + result.stderr
