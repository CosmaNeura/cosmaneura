from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
LEGACY_WORKFLOW = ROOT / ".github/workflows/build_and_publish.yml"
ACTIVE_WORKFLOW = ROOT / ".github/workflows/build_and_publish_local.yml"


def test_legacy_workflow_path_is_an_inert_auditable_tombstone() -> None:
    legacy = LEGACY_WORKFLOW.read_text(encoding="utf-8")

    assert "Build & Publish (retired legacy path)" in legacy
    assert ".github/workflows/build_and_publish_local.yml" in legacy
    assert "workflow_dispatch:" in legacy
    assert "permissions: {}" in legacy

    for deployment_capability in (
        "push:",
        "environment: production",
        "environment: staging",
        "id-token: write",
        "secrets.",
        "vars.",
        "azure/login",
        "build_push",
        "deploy.sh",
    ):
        assert deployment_capability not in legacy


def test_active_workflow_uses_the_new_registration_path() -> None:
    active = ACTIVE_WORKFLOW.read_text(encoding="utf-8")

    assert active.startswith("name: Build & Publish (manual)\n")
    parsed = yaml.load(active, Loader=yaml.BaseLoader)
    assert set(parsed["on"]) == {"workflow_dispatch"}
    assert "group: Build & Publish-${{ github.ref }}" in active
    assert active.count(".github/workflows/build_and_publish_local.yml") >= 2
    assert active.count(".github/workflows/build_and_publish.yml") >= 2


@pytest.mark.parametrize(
    "workflow_path",
    sorted((ROOT / ".github/workflows").glob("*.yml")),
    ids=lambda path: path.name,
)
def test_every_workflow_job_has_a_finite_timeout(workflow_path: Path) -> None:
    parsed = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)
    for name, job in parsed["jobs"].items():
        timeout = job.get("timeout-minutes", "")
        assert timeout.isdigit(), f"{workflow_path.name}:{name}: missing timeout"
        assert 1 <= int(timeout) <= 60, f"{workflow_path.name}:{name}: unbounded job"
