"""Exercise real staging configuration projection without cloud access."""

import json
import os
import runpy
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPANION = {
    "AGENT_API_COMPANION_CLIENT_ID": "33333333-3333-3333-3333-333333333333",
    "COMPANION_ENCOUNTER_READ_ENABLED": "true",
    "COMPANION_RECORD_READ_ENABLED": "true",
    "COMPANION_APPOINTMENT_SETUP_ENABLED": "true",
}


def project(release):
    deploy = (ROOT / "infra-staging/deploy.sh").read_text()
    start = deploy.index("# Keep the provider-v7 runtime gates synchronized")
    configure = deploy[start : deploy.index("if ! jq -e '", start)]
    start = deploy.index("COMMON_SECRET_VARS=(")
    contracts = deploy[
        start : deploy.index("verify_effective_configuration_fingerprint()")
    ]
    script = (
        "set -e\n"
        + configure
        + contracts
        + '\nprintf "%s\\n%s\\n" "$ENV_JSON" "$(runtime_contracts_json)"\n'
    )
    # An inherited caller must never become newly admitted just because the
    # deployer's public-variable allowlist now recognizes it.
    inherited = COMPANION | {
        "PYTHON_ENV": "staging",
        "AGENT_API_SCRIBE_RECEIVER_CLIENT_ID": "22222222-2222-2222-2222-222222222222",
        "AGENT_API_SOVEREIGN_CLIENT_IDS": "11111111-1111-1111-1111-111111111111",
    }
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            "PATH": os.environ["PATH"],
            "ENV_JSON": json.dumps(inherited),
            "RELEASE_V2_JSON": json.dumps({"runtime_public": release})
            if release is not None
            else "",
            "ENABLE_PROVIDER_V7": "true",
            "ENABLE_SOVEREIGN_RECORD_GRANTS": "false",
            "STRIPE_INTEGRATION_ENABLED": "false",
            "API_CONTAINER": "cosmaneura-staging-app",
            "WORKER_CONTAINER": "cosmaneura-staging-worker",
            "SCHEDULER_CONTAINER": "cosmaneura-staging-scheduler",
            "CRITICAL_CONTAINERS": "cosmaneura-staging-app cosmaneura-staging-worker cosmaneura-staging-scheduler",
            "STAGING_API_COMMAND": "synthetic-api",
            "STAGING_WORKER_COMMAND": "synthetic-worker",
            "SCHEDULER_COMMAND": "synthetic-scheduler",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    configuration, contracts = map(json.loads, result.stdout.splitlines())
    return {"configuration": configuration, "contracts": contracts}


@pytest.mark.parametrize("release", [None, {}])
def test_omitted_group_does_not_admit_an_inherited_companion(release):
    payload = project(release)
    assert not (COMPANION.keys() & payload["configuration"].keys())
    assert (
        payload["configuration"]["AGENT_API_SOVEREIGN_CLIENT_IDS"]
        == "11111111-1111-1111-1111-111111111111"
    )
    assert (
        payload["configuration"]["AGENT_API_SCRIBE_RECEIVER_CLIENT_ID"]
        == "22222222-2222-2222-2222-222222222222"
    )


@pytest.mark.parametrize("withdrawn", [False, True])
def test_explicit_group_is_in_every_runtime_contract_and_fingerprint(withdrawn):
    release = dict(COMPANION)
    if withdrawn:
        release = {key: "false" for key in release}
        release["AGENT_API_COMPANION_CLIENT_ID"] = ""
    payload = project(release)
    runtime = runpy.run_path(str(ROOT / "scripts/staging_runtime_configuration.py"))
    expected = {key: value for key, value in release.items() if value}
    assert len(payload["contracts"]) == 3
    for contract in payload["contracts"]:
        environment = runtime["desired"](payload["configuration"], contract)[
            "environment"
        ]
        assert {
            key: value for key, value in environment.items() if key in COMPANION
        } == expected
    original = runtime["fingerprint"](payload)
    payload["configuration"]["COMPANION_RECORD_READ_ENABLED"] = str(withdrawn).lower()
    assert runtime["fingerprint"](payload) != original
