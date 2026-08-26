from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY_WORKFLOW = ROOT / ".github/workflows/build_and_publish.yml"
ACTIVE_WORKFLOW = ROOT / ".github/workflows/build_and_publish_manual.yml"


def test_legacy_workflow_path_is_an_inert_auditable_tombstone() -> None:
    legacy = LEGACY_WORKFLOW.read_text(encoding="utf-8")

    assert "Build & Publish (retired legacy path)" in legacy
    assert ".github/workflows/build_and_publish_manual.yml" in legacy
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
    assert "branches: [master, staging]" in active
    assert "group: Build & Publish-${{ github.ref }}" in active
    assert active.count(".github/workflows/build_and_publish_manual.yml") >= 2
    assert active.count(".github/workflows/build_and_publish.yml") >= 2
