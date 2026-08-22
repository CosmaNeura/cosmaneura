from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_backend_ci_installs_only_default_suite_host_dependencies() -> None:
    workflow = (ROOT / ".github/workflows/build_and_publish.yml").read_text(
        encoding="utf-8"
    )
    backend = workflow[workflow.index("  app:\n") : workflow.index("  web:\n")]
    setup = backend[
        backend.index("      - uses: ./.github/actions/common-setup") : backend.index(
            "      - uses: iloveitaly/github-action-localias@master"
        )
    ]
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
