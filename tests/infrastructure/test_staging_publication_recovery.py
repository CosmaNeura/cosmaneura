"""Offline execution of publication recovery boundaries; no provider calls."""

import importlib.util
import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/deploy_staging_candidate.yml"
SPEC = importlib.util.spec_from_file_location(
    "publication_recovery", ROOT / "scripts/verify_staging_publication_recovery.py"
)
assert SPEC and SPEC.loader
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


def step_script(name: str) -> str:
    text = WORKFLOW.read_text().split("      - name: " + name + "\n", 1)[1]
    text = re.split(r"\n      - (?:name:|uses:)", text, maxsplit=1)[0]
    return textwrap.dedent(text.split("        run: |\n", 1)[1])


class PublicationShellTest(unittest.TestCase):
    def run_publication(
        self,
        *,
        recovery=False,
        legacy=False,
        wrong_config=False,
        existing=False,
        wrong_prefix=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "docker"
            binary.write_text(
                "#!/usr/bin/env python3\n"
                + textwrap.dedent("""
                import json, os, sys
                args = sys.argv[1:]
                with open(os.environ['TRACE'], 'a') as stream:
                    stream.write(json.dumps(args) + '\\n')
                if args[:2] == ['image', 'inspect']:
                    pass
                elif args[:3] == ['buildx', 'imagetools', 'inspect']:
                    if '--format' in args:
                        print(os.environ['DIGEST'])
                    elif '--raw' in args:
                        assert '@' + os.environ['DIGEST'] in args[3]
                        print(json.dumps({'schemaVersion': 2, 'config': {'digest': os.environ['CONFIG']}}))
                    elif os.environ['EXISTING'] == 'true':
                        print('already exists')
                    else:
                        print('manifest unknown')
                        raise SystemExit(1)
                elif args[0] == 'push':
                    prefix = '' if os.environ['LEGACY'] == 'true' else os.environ['PREFIX'] + ': '
                    print(prefix + 'digest: ' + os.environ['DIGEST'] + ' size: 2835')
                elif args[0] != 'tag':
                    raise SystemExit('unexpected Docker operation')
            """)
            )
            binary.chmod(0o700)
            env = {
                **os.environ,
                "PATH": directory + os.pathsep + os.environ["PATH"],
                "CANDIDATE_SHA": RECOVERY.CANDIDATE,
                "REGISTRY_HOST": "cosmaneurastagingregistry.azurecr.io",
                "RECOVERY_IMAGE_DIGEST": RECOVERY.DIGEST if recovery else "",
                "EXPECTED_IMAGE_ID": RECOVERY.IMAGE_ID,
                "RUNNER_TEMP": directory,
                "GITHUB_OUTPUT": str(root / "outputs"),
                "TRACE": str(root / "trace"),
                "DIGEST": RECOVERY.DIGEST,
                "CONFIG": "sha256:" + "0" * 64 if wrong_config else RECOVERY.IMAGE_ID,
                "EXISTING": str(existing).lower(),
                "LEGACY": str(legacy).lower(),
                "PREFIX": "0" * 40 if wrong_prefix else RECOVERY.CANDIDATE,
            }
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    step_script(
                        "Publish candidate SHA tag and resolve immutable digest"
                    ),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            trace = [
                json.loads(line) for line in (root / "trace").read_text().splitlines()
            ]
            output = (
                (root / "outputs").read_text() if (root / "outputs").exists() else ""
            )
            return result.returncode, trace, output

    def test_actual_tag_prefixed_push_line_and_legacy_line(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                code, trace, output = self.run_publication(legacy=legacy)
                self.assertEqual(code, 0)
                self.assertTrue(any(args[0] == "push" for args in trace))
                self.assertIn("image_digest=" + RECOVERY.DIGEST, output)

    def test_original_recovery_reads_immutable_manifest_without_push(self):
        code, trace, output = self.run_publication(recovery=True)
        self.assertEqual(code, 0)
        self.assertFalse(any(args[0] in {"tag", "push"} for args in trace))
        self.assertIn("image_digest=" + RECOVERY.DIGEST, output)

    def test_recovery_archive_config_mismatch_stops_without_outputs_or_push(self):
        code, trace, output = self.run_publication(recovery=True, wrong_config=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(output, "")
        self.assertFalse(any(args[0] in {"tag", "push"} for args in trace))

    def test_ordinary_existing_tag_guard_and_wrong_push_prefix_still_fail(self):
        code, trace, output = self.run_publication(existing=True)
        self.assertNotEqual(code, 0)
        self.assertFalse(any(args[0] in {"tag", "push"} for args in trace))
        self.assertEqual(output, "")
        code, _, output = self.run_publication(wrong_prefix=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(output, "")


class BackendAggregateTest(unittest.TestCase):
    def test_recovery_skips_aggregate_but_normal_failure_still_aggregates(self):
        app = WORKFLOW.read_text().split("  app:\n", 1)[1].split("\n  web:", 1)[0]
        self.assertIn("needs: [authorize, backend-quality, backend-tests]", app)
        matched = re.search(r"if: \$\{\{ (.+) \}\}", app)
        assert matched is not None
        expression = matched.group(1)
        for recovered, should_run in (("true", False), ("", True)):
            substituted = (
                expression.replace("always()", "True")
                .replace("needs.authorize.outputs.recovery_verified", repr(recovered))
                .replace("&&", "and")
            )
            self.assertEqual(eval(substituted, {"__builtins__": {}}), should_run)
        result = subprocess.run(
            [
                "bash",
                "-c",
                step_script("Require every backend prerequisite to succeed"),
            ],
            env={
                **os.environ,
                "BACKEND_QUALITY_RESULT": "failure",
                "BACKEND_TESTS_RESULT": "success",
            },
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)


class RecoveryProofTest(unittest.TestCase):
    def fixture(self):
        original = subprocess.check_output(
            [
                "git",
                "show",
                RECOVERY.CONTROL + ":.github/workflows/deploy_staging_candidate.yml",
            ],
            cwd=ROOT,
        )
        names = [
            "Authorize immutable staging candidate",
            "Candidate backend quality",
            "Candidate Frontend",
            "Candidate Backend",
            "Resolve reviewed public staging build configuration",
            "Build exact candidate without protected credentials",
        ]
        names += [
            f"Candidate backend tests ({name.decode()})"
            for name in re.findall(rb"          - shard: ([a-z0-9-]+)\n", original)
        ]
        rows = [
            {
                "name": name,
                "id": index,
                "run_id": RECOVERY.RUN,
                "run_attempt": 1,
                "conclusion": "success",
            }
            for index, name in enumerate(names)
        ]
        rows += [
            {
                "name": "Publish credential-free candidate archive",
                "id": 124200892393,
                "run_id": RECOVERY.RUN,
                "run_attempt": 1,
                "conclusion": "failure",
                "steps": [
                    {
                        "name": "Publish candidate SHA tag and resolve immutable digest",
                        "conclusion": "failure",
                    }
                ],
            },
            {
                "name": "Deploy exact candidate to persistent staging",
                "run_id": RECOVERY.RUN,
                "run_attempt": 1,
                "conclusion": "skipped",
            },
            {
                "name": "Record complete staging workflow attempt",
                "run_id": RECOVERY.RUN,
                "run_attempt": 1,
                "conclusion": "failure",
            },
        ]
        env = {
            "CANDIDATE_SHA": RECOVERY.CANDIDATE,
            "APPROVAL_PR_NUMBER": "906",
            "SYNTHETIC_ONLY_CONFIRMED": "true",
            "SYNTHETIC_EVIDENCE_SHA256": "sha256:" + "1" * 64,
            "STAGING_IDENTITY_FINGERPRINTS_SHA256": "sha256:" + "2" * 64,
            "STAGING_EFFECTIVE_CONFIGURATION_SHA256": "sha256:" + "3" * 64,
            "RECOVERY_DIGEST": "sha256:" + "4" * 64,
            "EXPECTED_SCHEMA_BEFORE": "3d40e4e90e05",
            "EXPECTED_SCHEMA_AFTER": "3d40e4e90e05",
        }
        requested = {
            key: value
            for key, value in zip(
                [
                    "candidate_sha",
                    "approval_pr_number",
                    "synthetic_only_confirmed",
                    "synthetic_evidence_sha256",
                    "staging_identity_fingerprints_sha256",
                    "staging_effective_configuration_sha256",
                    "recovery_digest",
                    "expected_schema_before",
                    "expected_schema_after",
                ],
                env.values(),
            )
        }
        requested["synthetic_only_confirmed"] = True
        frontend = b'{"public_test_fixture":true}\n'
        attempt = {
            "workflow_control_sha": RECOVERY.CONTROL,
            "github": {
                "repository": RECOVERY.REPOSITORY,
                "ref": "refs/heads/master",
                "ref_protected": True,
                "actor": "JohnClem",
                "triggering_actor": "JohnClem",
                "run_id": RECOVERY.RUN,
                "run_attempt": 1,
            },
            "requested": requested,
            "jobs": {
                "authorization": "success",
                "backend": "success",
                "frontend": "success",
                "public_configuration": "success",
                "credentialless_image_build": "success",
                "publication": "failure",
                "deployment": "skipped",
            },
            "available_evidence": {
                "build_archive_sha256": RECOVERY.ARCHIVE,
                "build_image_id": RECOVERY.IMAGE_ID,
                "frontend_config_sha256": "sha256:"
                + RECOVERY.hashlib.sha256(frontend).hexdigest(),
                "observed_source_sha": None,
                "image_digest": None,
            },
        }
        publication = {
            "recorded_at": "2026-10-30T17:09:08Z",
            "workflow_control_sha": RECOVERY.CONTROL,
            "source_sha": RECOVERY.CANDIDATE,
            "image": {"digest": None, "reference": None},
            "publication_outcome": "failure",
            "run_id": RECOVERY.RUN,
            "run_attempt": 1,
        }
        run = {
            "id": RECOVERY.RUN,
            "head_sha": RECOVERY.CONTROL,
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "failure",
            "event": "workflow_dispatch",
            "head_branch": "master",
            "path": ".github/workflows/deploy_staging_candidate.yml",
            "actor": {"login": "JohnClem"},
            "triggering_actor": {"login": "JohnClem"},
        }
        log = (
            f"2026-10-30T17:08:08Z EXPECTED_ARCHIVE_SHA256: {RECOVERY.ARCHIVE}\n"
            f"2026-10-30T17:08:08Z EXPECTED_IMAGE_ID: {RECOVERY.IMAGE_ID}\n"
            f"2026-10-30T17:09:08Z {RECOVERY.CANDIDATE}: digest: {RECOVERY.DIGEST} size: 2835\n"
            "2026-10-30T17:09:08Z ##[error]The published candidate did not resolve to an immutable digest.\n"
        )
        selection = {
            "run_id": RECOVERY.RUN,
            "run_attempt": 1,
            "image_digest": RECOVERY.DIGEST,
        }
        return env, [
            selection,
            attempt,
            publication,
            frontend,
            run,
            {"jobs": rows, "total_count": len(rows)},
            original,
            log,
        ]

    def test_complete_original_proof_preserves_failed_publication(self):
        env, args = self.fixture()
        with patch.dict(os.environ, env):
            result = RECOVERY.validate(*args)
        self.assertTrue(result["all_original_candidate_jobs_verified"])
        self.assertTrue(result["original_publication_still_failed"])
        self.assertEqual(result["registry_mutations"], 0)

    def test_changed_digest_or_failed_original_validation_is_rejected(self):
        for changed in ("digest", "validation"):
            env, args = self.fixture()
            if changed == "digest":
                args[0]["image_digest"] = "sha256:" + "0" * 64
            else:
                args[5]["jobs"][0]["conclusion"] = "failure"
            with patch.dict(os.environ, env), self.assertRaises(ValueError):
                RECOVERY.validate(*args)


if __name__ == "__main__":
    unittest.main()
