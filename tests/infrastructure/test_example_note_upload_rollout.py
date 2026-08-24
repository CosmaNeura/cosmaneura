from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


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

    function_start = deploy.index("wait_for_incompatible_worker_replicas_to_drain()")
    function_end = deploy.index("\n}\n", function_start)
    drain_function = deploy[function_start:function_end]

    assert "az containerapp revision list" in drain_function
    assert "az containerapp replica list" in drain_function
    assert f'expected_image="${{{image_variable}}}:${{git_sha}}"' in drain_function
    assert ".image != $expected_image" in drain_function
    assert '[[ "$remaining_replicas" -eq 0 ]]' in drain_function

    worker_check = deploy.index('check_deployment "$container" "$GIT_SHA"')
    drain_call = deploy.index(
        'wait_for_incompatible_worker_replicas_to_drain "$container" "$GIT_SHA"',
        worker_check,
    )
    api_deploy = deploy.index('initiate_deployment "$API_CONTAINER"', drain_call)
    assert worker_check < drain_call < api_deploy


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
