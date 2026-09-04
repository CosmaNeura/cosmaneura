from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LOCALIAS_WORKFLOWS = (
    ".github/workflows/pull_request_checks.yml",
    ".github/workflows/build_and_publish_manual.yml",
    ".github/workflows/deploy_staging_candidate.yml",
)


def test_localias_install_is_version_and_digest_pinned() -> None:
    installer = (ROOT / "scripts/install_pinned_localias.sh").read_text(
        encoding="utf-8"
    )
    assert 'LOCALIAS_VERSION="v3.0.0+commit.43c3619"' in installer
    assert (
        'LOCALIAS_SHA256="e7e8ae3a6cf2b1d7b637b6d871ea615b66509096f35ed8f4cc2c370a812193aa"'
        in installer
    )
    assert "releases/latest" not in installer
    assert "sha256sum --check --strict" in installer

    for workflow_path in LOCALIAS_WORKFLOWS:
        workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
        assert workflow.count("bash scripts/install_pinned_localias.sh") == 1
        assert workflow.count("bash scripts/start_pinned_localias_ci.sh") == 1
        assert "iloveitaly/github-action-localias@" not in workflow

    starter = (ROOT / "scripts/start_pinned_localias_ci.sh").read_text(encoding="utf-8")
    assert 'bash "${LOCALIAS_SCRIPT_DIR}/install_pinned_localias.sh"' in starter
    assert starter.count("sha256sum --check --strict") == 2
    assert (
        'localias_config="$("${LOCALIAS_INSTALL_PATH}" debug config --print)"'
        in starter
    )
    assert 'if [[ -z "${localias_domains}" ]]' in starter
    assert 'done <<<"${localias_domains}"' in starter
    assert "done < <(" not in starter
    assert "/usr/bin/pip install" not in starter

    cursor_setup = (ROOT / ".cursor/system-setup.sh").read_text(encoding="utf-8")
    assert (
        'CURSOR_SETUP_DIR="$(cd -P "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"'
        in cursor_setup
    )
    assert (
        'bash "$CURSOR_SETUP_DIR/../scripts/install_pinned_localias.sh"' in cursor_setup
    )
    assert "releases/latest" not in cursor_setup


def test_backend_ci_installs_only_default_suite_host_dependencies() -> None:
    workflow = (ROOT / ".github/workflows/build_and_publish_manual.yml").read_text(
        encoding="utf-8"
    )
    backend = workflow[workflow.index("  app:\n") : workflow.index("  web:\n")]
    setup = backend[
        backend.index("      - uses: ./.github/actions/common-setup") : backend.index(
            "      - name: Install SHA-verified Localias"
        )
    ]
    assert "iloveitaly/github-action-localias@" not in backend
    apt_package_lines = [
        line.strip()
        for line in setup.splitlines()
        if line.strip().startswith("apt-packages:")
    ]

    # `just` explicitly uses zsh, while localias invokes `certutil`, supplied
    # by libnss3-tools, to populate Chromium's NSS trust database.
    # Poppler and ffmpeg remain application runtime/opt-in live-test deps; an
    # exact assertion keeps them out of the default Backend host setup.
    assert apt_package_lines == ["apt-packages: zsh libnss3-tools"]
