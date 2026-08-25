from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _shell_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index("\n}", start)
    return source[start : end + 2]


def _run_worker_drain(
    deploy_path: str,
    image_variable: str,
    *,
    revisions: object,
    active_revisions_mode: str = "Single",
    replica_count: str = "0",
) -> subprocess.CompletedProcess[str]:
    drain_function = _shell_function(
        (ROOT / deploy_path).read_text(encoding="utf-8"),
        "wait_for_incompatible_worker_replicas_to_drain",
    )
    script = f"""
set -u
RESOURCE_GROUP=test-resource-group
{image_variable}=registry.example/cosmaneura
az() {{
    if [[ "$1" == "containerapp" && "$2" == "show" ]]; then
        printf '%s\\n' "$TEST_ACTIVE_REVISIONS_MODE"
    elif [[ "$1" == "containerapp" && "$2" == "revision" && "$3" == "list" ]]; then
        printf '%s\\n' "$TEST_REVISIONS_JSON"
    elif [[ "$1" == "containerapp" && "$2" == "replica" && "$3" == "list" ]]; then
        printf 'replica-list-called\\n' >&2
        printf '%s\\n' "$TEST_REPLICA_COUNT"
    else
        printf 'unexpected az call: %s\\n' "$*" >&2
        return 97
    fi
}}
{drain_function}
wait_for_incompatible_worker_replicas_to_drain test-worker rollout-sha
"""
    environment = os.environ.copy()
    environment.update(
        {
            "TEST_ACTIVE_REVISIONS_MODE": active_revisions_mode,
            "TEST_REPLICA_COUNT": replica_count,
            "TEST_REVISIONS_JSON": json.dumps(revisions),
        }
    )
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("deploy_path", "image_variable"),
    [
        ("infra/deploy.sh", "PRODUCTION_IMAGE"),
        ("infra-staging/deploy.sh", "STAGING_IMAGE"),
    ],
)
def test_deploy_blocks_api_until_old_worker_replicas_are_drained(
    deploy_path: str,
    image_variable: str,
) -> None:
    deploy = (ROOT / deploy_path).read_text()

    drain_function = _shell_function(
        deploy,
        "wait_for_incompatible_worker_replicas_to_drain",
    )

    assert "properties.configuration.activeRevisionsMode" in drain_function
    assert '[[ "$active_revisions_mode" != "Single" ]]' in drain_function
    assert "az containerapp revision list" in drain_function
    assert "az containerapp replica list" in drain_function
    assert f'expected_image="${{{image_variable}}}:${{git_sha}}"' in drain_function
    assert ".image == $expected_image" in drain_function
    assert '"$expected_revision_count" -eq 0' in drain_function
    assert ".image != $expected_image" in drain_function
    assert '[[ "$remaining_replicas" -eq 0 ]]' in drain_function

    worker_check = deploy.index('check_deployment "$container" "$GIT_SHA"')
    drain_call = deploy.index(
        'wait_for_incompatible_worker_replicas_to_drain "$container" "$GIT_SHA"',
        worker_check,
    )
    api_deploy = deploy.index('initiate_deployment "$API_CONTAINER"', drain_call)
    assert worker_check < drain_call < api_deploy


@pytest.mark.parametrize(
    ("deploy_path", "image_variable"),
    [
        ("infra/deploy.sh", "PRODUCTION_IMAGE"),
        ("infra-staging/deploy.sh", "STAGING_IMAGE"),
    ],
)
@pytest.mark.parametrize(
    "revisions",
    [
        [],
        [{"name": "old-worker", "image": "registry.example/cosmaneura:old-sha"}],
    ],
    ids=["empty-revision-list", "expected-image-missing"],
)
def test_worker_drain_fails_closed_without_the_expected_revision(
    deploy_path: str,
    image_variable: str,
    revisions: object,
) -> None:
    result = _run_worker_drain(
        deploy_path,
        image_variable,
        revisions=revisions,
    )

    assert result.returncode != 0
    assert "Expected worker image" in result.stdout
    assert "refusing to deploy the API" in result.stdout
    assert "replica-list-called" not in result.stderr


@pytest.mark.parametrize(
    ("deploy_path", "image_variable"),
    [
        ("infra/deploy.sh", "PRODUCTION_IMAGE"),
        ("infra-staging/deploy.sh", "STAGING_IMAGE"),
    ],
)
def test_worker_drain_requires_single_revision_mode(
    deploy_path: str,
    image_variable: str,
) -> None:
    result = _run_worker_drain(
        deploy_path,
        image_variable,
        revisions=[],
        active_revisions_mode="Multiple",
    )

    assert result.returncode != 0
    assert "requires Single active revision mode" in result.stdout
    assert "replica-list-called" not in result.stderr


@pytest.mark.parametrize(
    ("deploy_path", "image_variable"),
    [
        ("infra/deploy.sh", "PRODUCTION_IMAGE"),
        ("infra-staging/deploy.sh", "STAGING_IMAGE"),
    ],
)
def test_worker_drain_accepts_the_expected_revision_after_old_replicas_drain(
    deploy_path: str,
    image_variable: str,
) -> None:
    result = _run_worker_drain(
        deploy_path,
        image_variable,
        revisions=[
            {
                "name": "new-worker",
                "image": "registry.example/cosmaneura:rollout-sha",
            },
            {"name": "old-worker", "image": "registry.example/cosmaneura:old-sha"},
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "All worker replicas on older images have drained." in result.stdout
    assert "replica-list-called" in result.stderr


def test_staging_health_check_inspects_the_latest_revision_by_name() -> None:
    deploy = (ROOT / "infra-staging/deploy.sh").read_text()
    function_start = deploy.index("check_deployment()")
    function_end = deploy.index("\n}\n", function_start)
    check_function = deploy[function_start:function_end]

    assert "properties.latestRevisionName" in check_function
    assert "az containerapp revision show" in check_function
    assert '"[0].properties.template.containers[0].image"' not in check_function
    assert '"$health_state" == "Healthy"' in check_function
    assert '"$provisioning_state" == "Provisioned"' in check_function
