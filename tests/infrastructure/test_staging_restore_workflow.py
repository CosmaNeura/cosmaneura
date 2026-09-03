from pathlib import Path

import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github/workflows/staging_restore_rehearsal.yml"
)


def test_workflow_is_protected_manual_and_cleanup_is_default_off() -> None:
    parsed = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    triggers = parsed["on"]
    assert set(triggers) == {"workflow_dispatch", "schedule"}
    assert triggers["schedule"] == [{"cron": "17 * * * *"}]
    jobs = parsed["jobs"]
    assert "environment" not in jobs["authorize"]
    assert jobs["provision"]["environment"] == "staging-restore-provision"
    assert jobs["cleanup"]["environment"] == "staging-restore-cleanup"
    permissions = {"attestations": "write", "contents": "read", "id-token": "write"}
    assert jobs["provision"]["permissions"] == permissions
    assert jobs["cleanup"]["permissions"] == permissions
    workflow = WORKFLOW.read_text()
    assert "refs/heads/master" in workflow
    assert "github.ref_protected" in workflow
    assert "github.triggering_actor" in workflow
    assert '"$RUN_ID:$RUN_ATTEMPT"' in workflow
    assert workflow.count("Reauthorize protected head after environment approval") == 2
    assert workflow.count("validate-policy") == 1
    assert workflow.index("validate-policy") < workflow.index("azure/login@")
    assert workflow.count("STAGING_RESTORE_CLEANUP_ENABLED == 'true'") == 3
    assert "compare/${GITHUB_SHA}...${current_master}" in workflow
    assert '"$comparison" = "identical" -o "$comparison" = "ahead"' in workflow
    assert "needs.provision.result != 'success'" in workflow
    assert "--allow-missing" in workflow and "--force" in workflow
    assert "actions/checkout@fbc6f399" in workflow
    assert "azure/login@f5d393ae" in workflow
    assert "actions/attest-build-provenance@e8998f949" in workflow
    assert "if: always()" in workflow
    assert "pull_request:" not in workflow and "push:" not in workflow
