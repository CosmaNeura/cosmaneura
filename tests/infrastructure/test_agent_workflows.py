from pathlib import Path

ROOT = Path(__file__).parents[2]
ASSESSMENT_WORKFLOW = ROOT / ".github/workflows/agent_pr_assessment.yml"
VALIDATION_WORKFLOW = ROOT / ".github/workflows/agent_handoff_validation.yml"


def _assessment_source() -> str:
    return ASSESSMENT_WORKFLOW.read_text(encoding="utf-8")


def test_assessment_runs_only_as_trusted_manual_master_workflow() -> None:
    source = _assessment_source()

    assert "  workflow_dispatch:" in source
    assert "pull_request_target" not in source
    assert "if: github.ref == 'refs/heads/master'" in source
    assert '"$current_master_sha" != "$GITHUB_SHA"' in source
    assert "persist-credentials: false" in source


def test_assessment_uses_immutable_comparison_and_rechecks_pr() -> None:
    source = _assessment_source()

    assert "compare/$base_sha...$head_sha?per_page=250" in source
    assert "pulls/$REQUESTED_PR/files" not in source
    assert '"$base_ref" != "master"' in source
    assert ".base.sha == $policy_sha" in source
    assert "current_head_sha=$(jq -er '.head.sha'" in source


def test_assessment_pins_identity_agent_and_broker_owned_fields() -> None:
    source = _assessment_source()

    assert "client-id: ${{ vars.FOUNDRY_CLIENT_ID }}" in source
    assert '--version "$AGENT_VERSION"' in source
    assert "--confirm-sanitized" in source
    assert "--draft-output" in source
    assert "policy_version: $policy_version" in source
    assert "foundry_agent_version: $foundry_agent_version" in source
    assert "workflow_run_id: $workflow_run_id" in source
    assert "evidence_fresh_until: $evidence_fresh_until" in source
    assert "evidence_policy: $evidence_pack[0].evidence_policy" in source


def test_validation_workflow_is_secretless_and_runs_all_control_tests() -> None:
    source = VALIDATION_WORKFLOW.read_text(encoding="utf-8")

    assert "id-token: write" not in source
    assert "pull-requests: write" not in source
    assert "tests/infrastructure/test_agent_workflows.py" in source
    assert "tests/infrastructure/test_build_agent_evidence.py" in source
    assert "tests/infrastructure/test_fetch_agent_handoff.py" in source
    assert "tests/infrastructure/test_foundryctl.py" in source
    assert "tests/infrastructure/test_handoffctl.py" in source
