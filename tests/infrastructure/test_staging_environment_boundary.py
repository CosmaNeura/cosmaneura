"""Synthetic tree fixtures only; no network, credentials, or Azure calls."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "staging_environment_boundary_fixture",
    ROOT / "scripts/staging_environment_boundary.py",
)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _staging_tree(root: Path) -> None:
    (root / ".git").mkdir()
    (root / "scripts").mkdir()
    (root / "tests" / "infrastructure").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "infra-staging").mkdir()
    _write(root / "scripts" / "staging_environment_boundary.py", "# self\n")
    _write(
        root / "tests" / "infrastructure" / "test_staging_environment_boundary.py",
        "# self\n",
    )
    _write(
        root / ".env.staging.frontend",
        'export VITE_PYTHON_URL="https://staging-app.cosmaneura.com/"\n',
    )
    _write(
        root / ".github" / "workflows" / "deploy_staging_candidate.yml",
        "name: Deploy Staging Candidate\n",
    )
    _write(
        root / "infra-staging" / "note.txt",
        "expected https://staging-app.cosmaneura.com\n",
    )
    _write(
        root / "tests" / "infrastructure" / "test_staging_fixture.py",
        'HOST = "https://staging-app.cosmaneura.com"\n',
    )
    _write(
        root / "scripts" / "staging_helper.py",
        'URL = "https://staging-chat.cosmaneura.com"\n',
    )


class StagingEnvironmentBoundaryTests(unittest.TestCase):
    def test_clean_staging_tree_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _staging_tree(root)
            self.assertEqual(m.scan_repository(root), [])
            self.assertEqual(m.main(["--root", str(root)]), 0)

    def test_staging_config_naming_live_app_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _staging_tree(root)
            _write(
                root / ".env.staging.backend",
                f"export API_BASE_URL=https://{m.LIVE_APP_HOST}\n",
            )
            findings = m.scan_repository(root)
            self.assertTrue(any(m.LIVE_APP_HOST in item for item in findings))
            self.assertEqual(m.main(["--root", str(root)]), 1)

    def test_staging_workflow_naming_live_clerk_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _staging_tree(root)
            _write(
                root / ".github" / "workflows" / "deploy_staging_candidate.yml",
                f"issuer: https://{m.LIVE_CLERK_HOST}\n",
            )
            findings = m.scan_repository(root)
            self.assertTrue(any(m.LIVE_CLERK_HOST in item for item in findings))

    def test_staging_fixture_with_live_key_prefix_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _staging_tree(root)
            _write(
                root / "tests" / "infrastructure" / "test_staging_keys.py",
                f'KEY = "{m.LIVE_PUBLISHABLE_KEY_PREFIX}invented"\n',
            )
            findings = m.scan_repository(root)
            self.assertTrue(
                any(m.LIVE_PUBLISHABLE_KEY_PREFIX in item for item in findings)
            )

    def test_staging_prefix_hosts_do_not_false_positive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _staging_tree(root)
            _write(
                root / ".env.staging.backend",
                "\n".join(
                    [
                        "export API_BASE_URL=https://staging-app.cosmaneura.com",
                        "export INTAKE_CHAT_DOMAIN=staging-chat.cosmaneura.com",
                        "",
                    ]
                ),
            )
            self.assertEqual(m.scan_repository(root), [])

    def test_production_promote_paths_are_outside_scan_set(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _staging_tree(root)
            _write(
                root / ".env.production.frontend",
                f'export VITE_PYTHON_URL="https://{m.LIVE_APP_HOST}/"\n',
            )
            _write(
                root / ".github" / "workflows" / "build_and_publish_local.yml",
                f"host: {m.LIVE_APP_HOST}\n",
            )
            self.assertEqual(m.scan_repository(root), [])

    def test_current_repository_boundary_holds(self):
        findings = m.scan_repository(ROOT)
        self.assertEqual(
            findings,
            [],
            "staging-scoped paths currently name live production identity:\n"
            + "\n".join(findings),
        )


if __name__ == "__main__":
    unittest.main()
