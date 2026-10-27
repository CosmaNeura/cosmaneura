import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github/workflows/staging_restore_rehearsal.yml"
)

CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
AZURE_LOGIN = "azure/login@a641126d1b8aa4d1fa005f4f92df94a3a4c4c906"
ATTEST_PROVENANCE = (
    "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8"
)
UPLOAD_ARTIFACT = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
PROVISION_REFUSAL = (
    'echo "::error::Provision is disabled. Activation requires one trusted process '
    "to authenticate server, provider, and GitHub proofs, compose their opaque "
    'seals, and directly invoke safe-submit."\n'
    "exit 1\n"
)
POLICY_ONLY_FOR_CLEANUP = (
    "github.event_name == 'schedule' || inputs.action == 'cleanup'"
)


def _parsed_workflow() -> dict[str, Any]:
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def _job_source(name: str) -> str:
    """Return one job's literal YAML, excluding every neighboring job."""
    lines = WORKFLOW.read_text().splitlines()
    marker = f"  {name}:"
    start = lines.index(marker)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.fullmatch(r"  [A-Za-z0-9_-]+:", lines[index]):
            end = index
            break
    return "\n".join(lines[start:end])


def _uses(job: dict[str, Any]) -> list[str]:
    return [step["uses"] for step in job["steps"] if "uses" in step]


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return needs if isinstance(needs, list) else [needs]


def test_dispatch_surface_is_cleanup_first_and_schedule_is_hourly() -> None:
    parsed = _parsed_workflow()
    triggers = parsed["on"]

    assert set(triggers) == {"workflow_dispatch", "schedule"}
    assert triggers["schedule"] == [{"cron": "17 * * * *"}]

    inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"action", "rehearsal_id"}
    assert inputs["action"]["required"] == "true"
    assert inputs["action"]["default"] == "cleanup"
    assert inputs["action"]["type"] == "choice"
    assert set(inputs["action"]["options"]) == {"provision", "cleanup"}
    assert inputs["rehearsal_id"]["required"] == "false"
    assert inputs["rehearsal_id"]["type"] == "string"


def test_provision_is_one_unconditional_credentialless_refusal() -> None:
    jobs = _parsed_workflow()["jobs"]
    provision = jobs["provision"]
    source = _job_source("provision")

    # This is an exact allowlist for the complete job. Any new key, step, command,
    # permission, environment, or output must receive fresh Tier-2 review.
    assert provision == {
        "needs": ["authorize"],
        "if": "github.event_name == 'workflow_dispatch' && inputs.action == 'provision'",
        "runs-on": ["self-hosted", "Linux", "X64", "cosmaneura-validation-local"],
        "timeout-minutes": "1",
        "permissions": {},
        "steps": [
            {
                "name": "Refuse provision until the trusted in-process coordinator exists",
                "run": PROVISION_REFUSAL,
            }
        ],
    }

    # The dormant job must not acquire credentials or produce portable output.
    for denied in (
        "uses:",
        "environment:",
        "outputs:",
        "GITHUB_OUTPUT",
        "${{ vars.",
        "${{ secrets.",
        "github.token",
        "GH_TOKEN",
        "azure/",
        "AZURE_",
        "actions/",
        "artifact",
        "upload-artifact",
        "attest-build-provenance",
        "STAGING_RESTORE_PROVISION_",
        "staging_restore_envelope.py provision",
    ):
        assert denied not in source


def test_authorize_does_not_mint_a_run_derived_rehearsal_id() -> None:
    authorize = _parsed_workflow()["jobs"]["authorize"]
    source = _job_source("authorize")

    assert "environment" not in authorize
    assert "outputs" not in authorize
    assert [step["name"] for step in authorize["steps"]] == [
        "Require protected repository, ref, and action",
        "Check out policy from exact protected commit",
        "Fail closed while policy contains placeholders",
    ]
    assert [step.get("if") for step in authorize["steps"]] == [
        None,
        POLICY_ONLY_FOR_CLEANUP,
        POLICY_ONLY_FOR_CLEANUP,
    ]
    assert "rehearsal_id" not in source
    assert "GITHUB_OUTPUT" not in source
    assert '"$RUN_ID:$RUN_ATTEMPT"' not in source


def test_only_cleanup_has_environment_credentials_and_mutating_actions() -> None:
    jobs = _parsed_workflow()["jobs"]

    assert jobs["cleanup"]["environment"] == "staging-restore-cleanup"
    for name, job in jobs.items():
        if name != "cleanup":
            assert "environment" not in job
            assert not any(use.startswith("azure/login@") for use in _uses(job))

    assert _uses(jobs["authorize"]) == [CHECKOUT]
    assert _uses(jobs["provision"]) == []
    assert _uses(jobs["cleanup"]) == [
        CHECKOUT,
        AZURE_LOGIN,
        ATTEST_PROVENANCE,
        UPLOAD_ARTIFACT,
    ]

    all_uses = [use for job in jobs.values() for use in _uses(job)]
    assert set(all_uses) == {
        CHECKOUT,
        AZURE_LOGIN,
        ATTEST_PROVENANCE,
        UPLOAD_ARTIFACT,
    }
    assert all_uses.count(CHECKOUT) == 2
    assert all_uses.count(AZURE_LOGIN) == 1
    assert all_uses.count(ATTEST_PROVENANCE) == 1
    assert all_uses.count(UPLOAD_ARTIFACT) == 1

    for job in (jobs["authorize"], jobs["cleanup"]):
        checkout = next(step for step in job["steps"] if step.get("uses") == CHECKOUT)
        assert checkout["with"] == {
            "persist-credentials": "false",
            "ref": "${{ github.sha }}",
        }


def test_cleanup_depends_only_on_authorize_and_retains_both_cleanup_modes() -> None:
    cleanup = _parsed_workflow()["jobs"]["cleanup"]
    source = _job_source("cleanup")
    workflow = WORKFLOW.read_text()

    assert _needs(cleanup) == ["authorize"]
    assert "always()" in cleanup["if"]
    assert "needs.authorize.result == 'success'" in cleanup["if"]
    assert "github.event_name == 'workflow_dispatch'" in cleanup["if"]
    assert "inputs.action == 'cleanup'" in cleanup["if"]
    assert "github.event_name == 'schedule'" in cleanup["if"]
    assert "STAGING_RESTORE_CLEANUP_ENABLED == 'true'" in cleanup["if"]

    assert "staging_restore_envelope.py cleanup" in source
    assert "staging_restore_envelope.py sweep" in source
    assert '--rehearsal-id "$REQUESTED_ID" --force' in source
    assert "needs.provision" not in workflow
    assert "FAILED_ID" not in workflow
    assert "--allow-missing" not in workflow
    assert "staging_restore_envelope.py provision" not in workflow


def test_protected_head_checks_signing_and_retention_remain_exact() -> None:
    parsed = _parsed_workflow()
    authorize_source = _job_source("authorize")
    cleanup = parsed["jobs"]["cleanup"]
    cleanup_source = _job_source("cleanup")
    workflow = WORKFLOW.read_text()

    assert "refs/heads/master" in authorize_source
    assert "github.ref_protected" in authorize_source
    assert "github.triggering_actor" in authorize_source
    assert "git/ref/heads/master" in authorize_source
    assert 'test "$WORKFLOW_SHA" = "$(gh api ' in authorize_source

    assert "Reauthorize protected head after environment approval" in cleanup_source
    assert "compare/${GITHUB_SHA}...${current_master}" in cleanup_source
    assert '"$comparison" = "identical" -o "$comparison" = "ahead"' in cleanup_source
    assert "JohnClem:JohnClem" in cleanup_source

    attest = next(
        step for step in cleanup["steps"] if step.get("uses") == ATTEST_PROVENANCE
    )
    assert "always()" in attest["if"]
    assert attest["with"] == {"subject-path": "evidence/cleanup-envelope.json"}

    upload = next(
        step for step in cleanup["steps"] if step.get("uses") == UPLOAD_ARTIFACT
    )
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "evidence/"
    assert upload["with"]["if-no-files-found"] == "warn"
    assert upload["with"]["retention-days"] == "90"

    assert workflow.count("validate-policy") == 1
    assert workflow.index("validate-policy") < workflow.index(AZURE_LOGIN)
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "staging_restore_safe_submit" not in workflow
    assert "restore-safe-submit-policy.json" not in workflow
