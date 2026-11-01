"""Exercise the hosted-to-migration source binding without credentials or Azure."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "infra-staging/deploy.sh"
CANDIDATE = "c" * 40
IMAGE = "sha256:" + "d" * 64
SCHEMA = "a" * 12


def _function(name: str) -> str:
    source = DEPLOY.read_text()
    start = source.index(name + "() {")
    return source[start : source.index("\n}", start) + 2]


def _run(tmp_path: Path, overrides: dict[str, str] | None = None):
    # Substitute only the existing authenticated metadata boundary. The real
    # verifier's founder/master/PR/graph gates are covered by its focused suite.
    # Execute the actual shell consumer and record exactly what it requests.
    cli = tmp_path / "metadata_boundary"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        "args=sys.argv[1:]\n"
        "open(os.environ['CALLS'],'w').write(json.dumps(args))\n"
        "if os.environ.get('RESULT_MODE')=='failure': raise SystemExit(2)\n"
        "result={'approval_pr_number':931,'candidate_sha':'c'*40,'master_sha':'b'*40,'schema_head':'a'*12}\n"
        "mode=os.environ.get('RESULT_MODE')\n"
        "if mode=='candidate': result['candidate_sha']='e'*40\n"
        "if mode=='pr': result['approval_pr_number']=932\n"
        "if mode=='schema': result['schema_head']='f'*12\n"
        "if mode=='master': result['master_sha']='short'\n"
        "if mode=='extra': result['extra']='unaccepted'\n"
        "print(json.dumps(result))\n"
    )
    cli.chmod(0o700)
    env = {
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
        "PYTHON_BIN": str(cli),
        "REPOSITORY_ROOT": str(ROOT),
        "STAGING_DEPLOYMENT_IDENTITY_MODE": "service-principal",
        "GIT_SHA": CANDIDATE,
        "CANDIDATE_SHA": CANDIDATE,
        "STAGING_IMAGE_DIGEST": IMAGE,
        "IMAGE_DIGEST": IMAGE,
        "STAGING_APPROVAL_PR_NUMBER": "931",
        "EXPECTED_SCHEMA_BEFORE": "1" * 12,
        "EXPECTED_SCHEMA_AFTER": SCHEMA,
        "WORKFLOW_SHA": "b" * 40,
        "CALLS": str(tmp_path / "calls.json"),
    } | (overrides or {})
    script = (
        "set -eu\nFOUNDER_CANDIDATE_SHA=historical\n"
        "FOUNDER_CANDIDATE_IMAGE_DIGEST=historical\n"
        "FOUNDER_EXPECTED_SCHEMA_AFTER=historical\nFOUNDER_APPROVAL_PR_NUMBER=385\n"
        "verify_founder_release_source() { return 23; }\n"
        + _function("verify_staging_release_source")
        + "\nverify_staging_release_source || exit $?\n"
        + 'printf "BOUND=%s,%s,%s,%s\\n" "$FOUNDER_CANDIDATE_SHA" "$FOUNDER_CANDIDATE_IMAGE_DIGEST" "$FOUNDER_APPROVAL_PR_NUMBER" "$FOUNDER_EXPECTED_SCHEMA_AFTER"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    calls = (
        json.loads((tmp_path / "calls.json").read_text())
        if (tmp_path / "calls.json").exists()
        else []
    )
    return result, calls


def test_current_candidate_binds_migration_selectors_after_exact_source_success(
    tmp_path: Path,
) -> None:
    result, calls = _run(tmp_path)
    assert result.returncode == 0
    assert f"BOUND={CANDIDATE},{IMAGE},931,{SCHEMA}" in result.stdout
    assert calls == [
        "-I",
        "-S",
        "-B",
        "scripts/verify_staging_candidate.py",
        "--reverify",
        "--candidate-sha",
        CANDIDATE,
        "--approval-pr-number",
        "931",
        "--expected-schema-after",
        SCHEMA,
        "--workflow-sha",
        "b" * 40,
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"GIT_SHA": "e" * 40},
        {"GIT_SHA": "c" * 39, "CANDIDATE_SHA": "c" * 39},
        {"STAGING_IMAGE_DIGEST": "sha256:" + "e" * 64},
        {"STAGING_APPROVAL_PR_NUMBER": "0"},
        {"STAGING_APPROVAL_PR_NUMBER": ""},
        {"EXPECTED_SCHEMA_BEFORE": "short"},
        {"EXPECTED_SCHEMA_AFTER": "short"},
        {"WORKFLOW_SHA": ""},
        {"STAGING_DEPLOYMENT_IDENTITY_MODE": "unexpected"},
    ],
)
def test_input_drift_refuses_before_source_lookup(
    tmp_path: Path, overrides: dict[str, str]
) -> None:
    result, calls = _run(tmp_path, overrides)
    assert result.returncode != 0 and not calls and "BOUND=" not in result.stdout


@pytest.mark.parametrize(
    "mode", ["failure", "candidate", "pr", "schema", "master", "extra"]
)
def test_failed_or_mismatched_source_never_binds_migrations(
    tmp_path: Path, mode: str
) -> None:
    result, calls = _run(tmp_path, {"RESULT_MODE": mode})
    assert result.returncode != 0 and calls and "BOUND=" not in result.stdout


def test_founder_branch_still_delegates_to_existing_strict_verifier(
    tmp_path: Path,
) -> None:
    result, calls = _run(tmp_path, {"STAGING_DEPLOYMENT_IDENTITY_MODE": "founder-user"})
    assert result.returncode == 23 and not calls and "BOUND=" not in result.stdout


def test_hosted_workflow_hands_off_the_validated_release_inputs() -> None:
    source = (ROOT / ".github/workflows/deploy_staging_candidate.yml").read_text()
    step = source.split(
        "      - name: Deploy candidate digest with protected-master controls\n", 1
    )[1].split("\n      - name:", 1)[0]
    assert (
        "STAGING_APPROVAL_PR_NUMBER: ${{ needs.authorize.outputs.approval_pr_number }}"
        in step
    )
    assert (
        "GH_TOKEN: ${{ github.token }}" in step
        and "WORKFLOW_SHA: ${{ github.sha }}" in step
    )
    assert "IMAGE_DIGEST: ${{ needs.publish.outputs.image_digest }}" in step
    assert 'bash infra-staging/deploy.sh "$CANDIDATE_SHA" "$IMAGE_DIGEST"' in step
