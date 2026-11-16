"""Offline execution of publication recovery boundaries; no provider calls."""

import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
import zipfile
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

    def test_reader039_recovery_uses_exact_digest_and_config_without_tag_or_push(self):
        p = RECOVERY.READER039
        with (
            patch.object(RECOVERY, "CANDIDATE", p["candidate"]),
            patch.object(RECOVERY, "DIGEST", p["digest"]),
            patch.object(RECOVERY, "IMAGE_ID", p["image_id"]),
        ):
            code, trace, output = self.run_publication(recovery=True)
            self.assertEqual(code, 0)
            self.assertFalse(any(args[0] in {"tag", "push"} for args in trace))
            self.assertIn("image_digest=" + p["digest"], output)
            code, trace, output = self.run_publication(recovery=True, wrong_config=True)
            self.assertNotEqual(code, 0)
            self.assertEqual(output, "")
            self.assertFalse(any(args[0] in {"tag", "push"} for args in trace))


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


# Authenticated original-run public receipts, normalized job metadata and small ZIPs.
# No chart data, recordings, login secrets or image archives are embedded.
READER039_PUBLIC_FIXTURES = {
    "artifacts": [
        {
            "created_at": "2026-11-01T19:38:41Z",
            "digest": "sha256:c98770388c630b6b3d0db583ec525a87153a7b68dd4d1c8fad8452e0d5227c8e",
            "expired": False,
            "expires_at": "2027-01-30T18:56:59Z",
            "id": 13098460690,
            "name": "canonical-staging-publication-039c69c8d20bd07a9c6c0c8a7860ff411646fb18-41339202271-1",
            "size_in_bytes": 674,
            "workflow_run": {
                "head_branch": "master",
                "head_repository_id": 881573489,
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 41339202271,
                "repository_id": 881573489,
            },
        },
        {
            "created_at": "2026-11-01T19:39:54Z",
            "digest": "sha256:abdf234900a0abec7a31230b092779a516ee2be2c0fcbdf335027472cd97ab18",
            "expired": False,
            "expires_at": "2027-01-30T18:56:59Z",
            "id": 13098426212,
            "name": "canonical-staging-release-039c69c8d20bd07a9c6c0c8a7860ff411646fb18-41339202271-1",
            "size_in_bytes": 5406,
            "workflow_run": {
                "head_branch": "master",
                "head_repository_id": 881573489,
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 41339202271,
                "repository_id": 881573489,
            },
        },
        {
            "created_at": "2026-11-01T19:40:05Z",
            "digest": "sha256:bf4615b806ced84ef030a39a892e2df1e38649180c1457750e7dc771cf0d7543",
            "expired": False,
            "expires_at": "2027-01-30T18:56:59Z",
            "id": 13097906691,
            "name": "canonical-staging-attempt-41339202271-1",
            "size_in_bytes": 1345,
            "workflow_run": {
                "head_branch": "master",
                "head_repository_id": 881573489,
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 41339202271,
                "repository_id": 881573489,
            },
        },
        {
            "created_at": "2026-11-01T19:33:47Z",
            "digest": "sha256:ae63d0e7fbc7a286bfc313b25376e82fe4ddefad59bd7ca7a380fff97b008c40",
            "expired": False,
            "expires_at": "2026-11-02T19:33:39Z",
            "id": 13097364215,
            "name": "canonical-staging-image-039c69c8d20bd07a9c6c0c8a7860ff411646fb18-41339202271-1",
            "size_in_bytes": 1550201694,
            "workflow_run": {
                "head_branch": "master",
                "head_repository_id": 881573489,
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 41339202271,
                "repository_id": 881573489,
            },
        },
        {
            "created_at": "2026-11-01T19:29:19Z",
            "digest": "sha256:920953791a5ea727be482937a9b689af62791dba5d26f06550da7400de616b09",
            "expired": False,
            "expires_at": "2026-11-02T19:29:18Z",
            "id": 13096594916,
            "name": "canonical-staging-frontend-config-41339202271-1",
            "size_in_bytes": 607,
            "workflow_run": {
                "head_branch": "master",
                "head_repository_id": 881573489,
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 41339202271,
                "repository_id": 881573489,
            },
        },
    ],
    "jobs": {
        "jobs": [
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945407329,
                "name": "Authorize immutable staging candidate",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441285,
                "name": "Candidate Frontend",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441343,
                "name": "Candidate backend quality",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441657,
                "name": "Candidate backend tests (infrastructure-controller)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441684,
                "name": "Candidate backend tests (infrastructure-sanitizer-foundation)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441719,
                "name": "Candidate backend tests (application)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441725,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-source-state-authority)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441747,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-source-state-context)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441763,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-observer-runtime)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441774,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-evidence-integrity)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441781,
                "name": "Candidate backend tests (infrastructure-sanitizer-observer)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441787,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-observer-connection-plan-state)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441805,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-observer-connection-plan)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441813,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-source-state-integrity)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441814,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-observer-output)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441841,
                "name": "Candidate backend tests (infrastructure-general)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441842,
                "name": "Candidate backend tests (infrastructure-sanitizer-evidence)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441859,
                "name": "Candidate backend tests (infrastructure-sanitizer-executor)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441861,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-observer-connection-plan-output)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441873,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-bootstrap-binding-tamper)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441881,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-data-contract-executor)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441896,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-bootstrap-binding)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441901,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-source-snapshot)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441904,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-bootstrap-binding-state)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441917,
                "name": "Candidate backend tests (infrastructure-receipt-io)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441918,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-bootstrap-binding-duplicate)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441924,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-observer-connection-plan-contract)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441926,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-bootstrap-binding-failure)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441939,
                "name": "Candidate backend tests (integration)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441951,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-observer-integrity)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441952,
                "name": "Candidate backend tests (infrastructure-restore)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441960,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-bootstrap-binding-digest)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945441970,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-bootstrap-binding-output)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124945442079,
                "name": "Candidate backend tests "
                "(infrastructure-sanitizer-bootstrap-binding-ancestry)",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124952681113,
                "name": "Candidate Backend",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124952934506,
                "name": "Resolve reviewed public staging build configuration",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124953736343,
                "name": "Build exact candidate without protected credentials",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
            },
            {
                "conclusion": "success",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124954881082,
                "name": "Publish credential-free candidate archive",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
                "steps": [
                    {
                        "conclusion": "success",
                        "name": "Verify and load candidate image archive without "
                        "executing it",
                    },
                    {
                        "conclusion": "success",
                        "name": "Reverify candidate before immutable image publication",
                    },
                    {
                        "conclusion": "success",
                        "name": "Publish candidate SHA tag and resolve immutable "
                        "digest",
                    },
                    {
                        "conclusion": "success",
                        "name": "Upload immutable publication evidence",
                    },
                ],
            },
            {
                "conclusion": "failure",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124956114501,
                "name": "Deploy exact candidate to persistent staging",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
                "steps": [
                    {
                        "conclusion": "failure",
                        "name": "Deploy candidate digest with protected-master "
                        "controls",
                    },
                    {
                        "conclusion": "failure",
                        "name": "Require staging health smoke to pass",
                    },
                ],
            },
            {
                "conclusion": "failure",
                "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
                "id": 124956413581,
                "name": "Record complete staging workflow attempt",
                "run_attempt": 1,
                "run_id": 41339202271,
                "status": "completed",
                "steps": [
                    {
                        "conclusion": "failure",
                        "name": "Require a complete successful staging attempt",
                    }
                ],
            },
        ],
        "total_count": 40,
    },
    "run": {
        "actor": {"login": "JohnClem"},
        "conclusion": "failure",
        "created_at": "2026-11-01T18:56:58Z",
        "event": "workflow_dispatch",
        "head_branch": "master",
        "head_sha": "f6a8bd753c93fdf94c45d25b2649679570544a24",
        "id": 41339202271,
        "path": ".github/workflows/deploy_staging_candidate.yml",
        "run_attempt": 1,
        "status": "completed",
        "triggering_actor": {"login": "JohnClem"},
        "updated_at": "2026-11-01T19:40:08Z",
    },
    "zips": {
        "attempt": "UEsDBBQACAAIAAKdYV0AAAAAAAAAAAAAAAAKAAAAU0hBMjU2U1VNUwXB2w3AIAgAwP9O4QQNUAEZB0Q/mvSR1P3TO5dKhqMlTQ5oRzI5YU8OOBijsnDrYqZpDJjTCafGzKrqMCRK8bXG9a79/J57+wFQSwcIW1+TUUkAAABPAAAAUEsDBBQACAAIAAKdYV0AAAAAAAAAAAAAAAAMAAAAYXR0ZW1wdC5qc29urVU7cxtHDO71KzisY3vfD1WZcZciVao0N1gAS551vGN29+TIHv/3zB1FkTIlV+l2Bt/iw+sDvt9tNtvCOBVi6qBt7zdbJZT7IOUHIf+S8d6Ie2H+3v62AL9O5SEP09cOp7GVaejqHpYf2UFI5K3GqDPlaNBYUjYpZ6Lz0XphjQFlTl4K/zNzbUzb+833u81ms9kijNQTND57FDqiixhIiUTCQ0SHAgP44ETORkpnXE4yrB43my0cj2V6hKE7lm6cD4nL4iVqcwbUp7HtufXYTePwtCSQ+3JYY2hl5hsUP/bEI64BKesWb6fXfZLKcPQxGq1tzM4omyh4p6zS2mc0OkSKIinOASlCkhiNRB2ERYoBXiJqsOvHXbfwtL49dbkfd1yOpR9bvaUNEYRMHklHhUiZpZVSiqitEtkRKSVyBsdWG49eJRNiVMFZQu1ixvQzLefM2PpHPtViNxdo/TTeEsvkREjotUHhpUZ0OSp2ioPL0meSIkFGFyWhJqlBamRPlJ11yhh+adEyZY9cnjrqd1zbFQMkHxXHlBGJFBsLOQYdbbTCgssgFUq0wTgjFWUBQkaNWQvNWmKw/szA/x4ZG1NXcc8H6BLnqfBCpMkINhwFC/seGnI7TY2WKinpOSUw27vN5sc6tY8wLBO6lKjwXJm6XKZDV+ax65cpGudhWIFfplQvkw1z20+l/7b+XHOeEbnWcxQJ8IFHesOSyzS2t03HOQ09vm7cGzAsvM4WDAPX2vUH2HGX5n543+d7roiPw/R04HHtW4Z+mAtfigOP0A+QBn7RzaUC5zyew70dMKWcAs5EWREEJrTopMzWAwAL68lETZp1sCJqLx0aA5AoBCShTXiR+JpZBwX3y1Tf0CzqUW6ZSqZInoCFV+CEUc44jCggUCKpMSibrY3eUiSnEiYQrCm8pjkVs6drhYogJLCyJnmbomLDgtgIH1hFp2J02jFFpTB6Bk5aOlAJVIDknY3qTHByfaMSCgBWKWkScpYRmEIMAcE4aY3I2WaXck5JgpHeswvSZUK2QTsrkeVr94Uzl+dGbXGqBxh5LvC8HQrv+trK00f4NhfG8rGfPl1AV8/f/+fgplS5PC6inOaC53Nw1tayvn6S9q3prOMry+3quTK+Xn9f6jr/V/bl6A0TUH0m/SVkJX8DUQ/Tw1L0Og9rT49QK9M56z3D0Pa4Z3zo9q0du9qgzfV0jMVlgQ4MlU/HF7BduVsEyXTR465v+zldNFj4ONW+TeVpQX9e2vfnT528sOQFUzjXT3sGqp8OUJeKXuzdsUxt3Z2vrydgm9YN+se0Hz8PfDj/aaXf7bgsZ+ddzMsiNVLrqIRSXl6ZoDU+HJdk5ZLl3Y+7/wBQSwcIb0n/oP4DAAC7CAAAUEsBAi0DFAAIAAgAAp1hXVtfk1FJAAAATwAAAAoAAAAAAAAAAAAgAICBAAAAAFNIQTI1NlNVTVNQSwECLQMUAAgACAACnWFdb0n/oP4DAAC7CAAADAAAAAAAAAAAACAAgIGBAAAAYXR0ZW1wdC5qc29uUEsFBgAAAAACAAIAcgAAALkEAAAAAA==",
        "frontend": "UEsDBBQACAAIAKibYV0AAAAAAAAAAAAAAAAbAAAAc3RhZ2luZy1mcm9udGVuZC1idWlsZC5qc29ubY9Nb6MwAETv+zN8bhw+mpRwWgpuoKE2NQ5dcrEcQoA0gIVNtclq//uq0qaNqt7nzbz5AzAJEEc4Ay5QWlRNV4EbQBKEvSTijynBPPFYCFzQy7ITsoEH1XfgBmQRQ9yPEV3xZH0fRz5foRy4QL5yXSrNizY7b63f1ublWW9eTDNm63PcYrX59TjE7cMhtxbmtqPnuKXHXfN8aYww81aI+6HHeECevAh/ik2KWmhY9KoVXTkOAhZ9e+ESkrKQLHlIUgZcUGstlTudjgo2UPZK1331Xfy/c11wZWYNwXuKrVcz2aIUj0fMzJ0/5Mvt4XAix5gafiBz7B8+SnIWEszXNL5avLgKKb+oTi9cijCjOQ9SfMXttvZsbwjbEfOFc7czzJlwDMOy7YUoTNvZ/+xvZ4ZjLUzHnluO4dgWbLqqVBqOCqqy08MJNv30PWTbxsJwrNmtZTgfmyRDFEVLzJPYYw+EPnFvzUJCI5ZzhL37GAXABXoYy28QSrIoQPTL1c97E9W/lUPZVN3k+n8tpDxVg1BqcmfPd8VMzGHXD7ou3n3FcVRQnMehLPpOi6YrByGlgk0P/v74B1BLBwgG2WWitwEAAJkCAABQSwECLQMUAAgACACom2FdBtllorcBAACZAgAAGwAAAAAAAAAAACAAgIEAAAAAc3RhZ2luZy1mcm9udGVuZC1idWlsZC5qc29uUEsFBgAAAAABAAEASQAAAAACAAAAAA==",
        "publication": "UEsDBBQACAAIANScYV0AAAAAAAAAAAAAAAAKAAAAU0hBMjU2U1VNUwXB2w2AIAwAwH+nYAKDtFAYh74SjAETdX/voqZmtRN6io3bQeBIKlYdAYULaUIEK1mbSXQWSFwIcjHumrWGcH98DenvWHM/nzW3H1BLBwjqsfEBSwAAAFMAAABQSwMEFAAIAAgA1JxhXQAAAAAAAAAAAAAAABAAAABwdWJsaWNhdGlvbi5qc29urZFBjt0gEET3PgXyOjMBDBi8yiGyysZqN40/GhtGgPOVjObukf0zSQ6QXUtdr7pU/dYx1hfCXDz5GVo/sV5yaZ6EeOLiq3DTYCfFv/WfTuE9l5ew5fuMObWSt7ne4CSCAbv4UQ/ohuCDU6i0l3qRRjkzOj1yrRRI9XCp+ShIHywfHBqH1ku+eD6CQ4McLYzW8BCUEEaZsAj7YOMOK/UTe+sYY6z3caV6ha43kNpM3gJoKYVakIJwQN46axGUEVrxEHQwSwjLIkCJcSRjhQkeSdvBaIEkritXJYEKJTxv9ZjrDomOArXBGtNaaI21lR/P8PMohOU55s9/Rf+MX/5XrI6x96uA12PZIkKLOc35aJj3K2I9EKnWR0nnP79TIT+Hkve5HGmOvp9YOrbtEgCe/MlddvU2J7rPCMlHD41+u3xgSgyDk1zKUfxZQGu0v57Ni+69+wVQSwcI60PH41UBAABGAgAAUEsBAi0DFAAIAAgA1JxhXeqx8QFLAAAAUwAAAAoAAAAAAAAAAAAgAICBAAAAAFNIQTI1NlNVTVNQSwECLQMUAAgACADUnGFd60PH41UBAABGAgAAEAAAAAAAAAAAACAAgIGDAAAAcHVibGljYXRpb24uanNvblBLBQYAAAAAAgACAHYAAAAWAgAAAAA=",
        "release": "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",
    },
}


class Reader039RecoveryTest(unittest.TestCase):
    def fixture(self):
        saved = copy.deepcopy(READER039_PUBLIC_FIXTURES)
        zips = {
            kind: base64.b64decode(raw, validate=True)
            for kind, raw in saved["zips"].items()
        }
        decoded = {}
        for kind, raw in zips.items():
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                decoded[kind] = {
                    name: archive.read(name) for name in archive.namelist()
                }
        p = RECOVERY.READER039
        original = subprocess.check_output(
            [
                "git",
                "show",
                p["control"] + ":.github/workflows/deploy_staging_candidate.yml",
            ],
            cwd=ROOT,
        )
        values = {
            "selection": {
                "run_id": 41339202271,
                "run_attempt": 1,
                "image_digest": "sha256:d8aa52214bcef19aed8988ca461540ff5f6bffbb1a4177e6816fdce583651ce1",
            },
            "attempt": json.loads(decoded["attempt"]["attempt.json"]),
            "publication": json.loads(decoded["publication"]["publication.json"]),
            "request": json.loads(decoded["release"]["request.json"]),
            "release": json.loads(decoded["release"]["release.json"]),
            "frontend": decoded["frontend"]["staging-frontend-build.json"],
            "run": saved["run"],
            "jobs": saved["jobs"],
            "original_workflow": original,
        }
        requested = values["attempt"]["requested"]
        names = {
            "candidate_sha": "CANDIDATE_SHA",
            "approval_pr_number": "APPROVAL_PR_NUMBER",
            "synthetic_evidence_sha256": "SYNTHETIC_EVIDENCE_SHA256",
            "staging_identity_fingerprints_sha256": "STAGING_IDENTITY_FINGERPRINTS_SHA256",
            "staging_effective_configuration_sha256": "STAGING_EFFECTIVE_CONFIGURATION_SHA256",
            "recovery_digest": "RECOVERY_DIGEST",
            "expected_schema_before": "EXPECTED_SCHEMA_BEFORE",
            "expected_schema_after": "EXPECTED_SCHEMA_AFTER",
        }
        env = {key: requested[name] for name, key in names.items()}
        env["SYNTHETIC_ONLY_CONFIRMED"] = "true"
        runtime = values["request"]["staging_runtime_public"] | {
            "SCRIBE_ENCOUNTER_CREATION_ENABLED": "true",
            "COMPANION_SESSION_PREP_ENABLED": "true",
        }
        env["STAGING_RUNTIME_PUBLIC_JSON"] = json.dumps(runtime)
        env["PUBLICATION_RECOVERY_JSON"] = json.dumps(values["selection"])
        return saved, zips, values, env

    def test_authenticated_actual_reader_profile_and_outcomes_are_preserved(self):
        saved, zips, values, env = self.fixture()
        p = RECOVERY.READER039
        self.assertEqual(p["run"], 41339202271)
        self.assertEqual(p["control"], "f6a8bd753c93fdf94c45d25b2649679570544a24")
        self.assertEqual(p["candidate"], "039c69c8d20bd07a9c6c0c8a7860ff411646fb18")
        self.assertEqual(
            p["archive"],
            "sha256:01b7266562ed9d7dae072a6042646c9c0a8dbd13c825f55975d9d62bcba0e3d8",
        )
        self.assertEqual(
            p["image_id"],
            "sha256:80801ae254b75b92e4e0de4078e296299636ed922c97eaeb316a2ba28ab76592",
        )
        before = copy.deepcopy(values)
        for kind, raw in zips.items():
            metadata = next(
                item
                for item in saved["artifacts"]
                if item["id"] == p["artifacts"][kind]["id"]
            )
            self.assertEqual(
                "sha256:" + hashlib.sha256(raw).hexdigest(), metadata["digest"]
            )
            self.assertEqual(p["artifacts"][kind]["digest"], metadata["digest"])
        with patch.dict(os.environ, env):
            result = RECOVERY.validate_reader039(**values)
        self.assertTrue(result["all_original_candidate_jobs_verified"])
        self.assertTrue(result["original_publication_still_successful"])
        self.assertTrue(result["original_deployment_still_failed"])
        self.assertEqual(
            (result["original_runtime_keys"], result["current_runtime_keys"]), (24, 26)
        )
        self.assertEqual(
            (result["registry_mutations"], result["deployment_operations"]), (0, 0)
        )
        self.assertEqual(values, before)

    def test_unknown_partial_duplicate_or_typed_selector_refuses(self):
        _, _, original, env = self.fixture()
        selections = [
            None,
            {},
            {
                "run_id": 41444049668,
                "run_attempt": 1,
                "image_digest": RECOVERY.READER039["digest"],
            },
        ]
        selections.extend(
            original["selection"] | {key: value}
            for key, value in [
                ("run_id", 41090712649),
                ("run_attempt", True),
                ("run_attempt", 1.0),
                ("run_attempt", 2),
                ("image_digest", "sha256:" + "0" * 64),
                ("extra", "value"),
            ]
        )
        with patch.dict(os.environ, env):
            for selection in selections:
                with self.subTest(selection=selection):
                    values = copy.deepcopy(original)
                    values["selection"] = selection
                    with self.assertRaises((ValueError, AttributeError)):
                        RECOVERY.validate_reader039(**values)
        with self.assertRaises(ValueError):
            RECOVERY.parse('{"run_id":41339202271,"run_id":41339202271}')

    def test_source_request_identity_fullcfg_schema_and_runtime_drift_refuse(self):
        _, _, original, env = self.fixture()
        for key in [
            "CANDIDATE_SHA",
            "APPROVAL_PR_NUMBER",
            "SYNTHETIC_ONLY_CONFIRMED",
            "SYNTHETIC_EVIDENCE_SHA256",
            "STAGING_IDENTITY_FINGERPRINTS_SHA256",
            "STAGING_EFFECTIVE_CONFIGURATION_SHA256",
            "RECOVERY_DIGEST",
            "EXPECTED_SCHEMA_BEFORE",
            "EXPECTED_SCHEMA_AFTER",
        ]:
            with self.subTest(key=key), patch.dict(os.environ, env | {key: "wrong"}):
                with self.assertRaises(ValueError):
                    RECOVERY.validate_reader039(**original)
        runtime = json.loads(env["STAGING_RUNTIME_PUBLIC_JSON"])
        variants = [
            original["request"]["staging_runtime_public"],
            runtime | {"OTHER": "true"},
        ]
        for key in [
            "SCRIBE_ENCOUNTER_CREATION_ENABLED",
            "COMPANION_SESSION_PREP_ENABLED",
        ]:
            for bad in [True, "false", None, "invalid"]:
                variants.append(runtime | {key: bad})
            variants.append({k: v for k, v in runtime.items() if k != key})
        for altered in variants:
            with (
                self.subTest(runtime=altered),
                patch.dict(
                    os.environ,
                    env | {"STAGING_RUNTIME_PUBLIC_JSON": json.dumps(altered)},
                ),
            ):
                with self.assertRaises(ValueError):
                    RECOVERY.validate_reader039(**original)

    def test_tampered_history_validation_producer_or_publication_refuses(self):
        _, _, original, env = self.fixture()
        mutations = [
            ("run", "head_sha", "0" * 40),
            ("run", "run_attempt", 2),
            ("run", "conclusion", "success"),
            ("run", "event", "push"),
            ("run", "head_branch", "other"),
            ("run", "path", "other.yml"),
            ("attempt", "workflow_control_sha", "0" * 40),
            ("publication", "publication_outcome", "failure"),
            ("publication", "recovered_from_run_id", 41444049668),
            (
                "request",
                "staging_runtime_public",
                json.loads(env["STAGING_RUNTIME_PUBLIC_JSON"]),
            ),
            ("release", "schema", {"before": "3d40e4e90e05", "after": "312b217ebba4"}),
            ("release", "deployment_outcome", "success"),
            ("release", "observed_source_sha", RECOVERY.READER039["candidate"]),
        ]
        with patch.dict(os.environ, env):
            for root, key, value in mutations:
                values = copy.deepcopy(original)
                values[root][key] = value
                with self.subTest(root=root, key=key), self.assertRaises(ValueError):
                    RECOVERY.validate_reader039(**values)
            for key in [
                "image_digest",
                "build_archive_sha256",
                "build_image_id",
                "frontend_config_sha256",
                "schema_before",
            ]:
                values = copy.deepcopy(original)
                values["attempt"]["available_evidence"][key] = "wrong"
                with self.subTest(evidence=key), self.assertRaises(ValueError):
                    RECOVERY.validate_reader039(**values)
            for index, row in enumerate(original["jobs"]["jobs"]):
                values = copy.deepcopy(original)
                values["jobs"]["jobs"][index]["conclusion"] = "cancelled"
                with self.subTest(job=row["name"]), self.assertRaises(ValueError):
                    RECOVERY.validate_reader039(**values)
            values = copy.deepcopy(original)
            values["jobs"]["jobs"].pop()
            values["jobs"]["total_count"] -= 1
            with self.assertRaises(ValueError):
                RECOVERY.validate_reader039(**values)
            values = copy.deepcopy(original)
            values["original_workflow"] += b"changed"
            with self.assertRaises(ValueError):
                RECOVERY.validate_reader039(**values)

    def test_authenticated_artifact_custody_and_members_refuse_tampering(self):
        saved, zips, _, _ = self.fixture()
        spec = RECOVERY.READER039["artifacts"]["attempt"]
        base = next(item for item in saved["artifacts"] if item["id"] == spec["id"])
        variants = [
            base | {key: value}
            for key, value in [
                ("id", 1),
                ("digest", "sha256:" + "0" * 64),
                ("name", "other"),
                ("expired", True),
            ]
        ]
        for key, value in [
            ("id", 1),
            ("head_sha", "0" * 40),
            ("repository_id", 1),
            ("head_repository_id", 1),
            ("head_branch", "other"),
        ]:
            variants.append(
                base | {"workflow_run": base["workflow_run"] | {key: value}}
            )
        for metadata in variants:
            with (
                self.subTest(metadata=metadata),
                patch.object(
                    RECOVERY, "gh", return_value=json.dumps(metadata).encode()
                ),
            ):
                with self.assertRaises(ValueError):
                    RECOVERY.reader039_artifact("attempt")
        for mutation in [b"changed", zips["attempt"] + b"changed"]:
            with patch.object(
                RECOVERY, "gh", side_effect=[json.dumps(base).encode(), mutation]
            ):
                with self.assertRaises(ValueError):
                    RECOVERY.reader039_artifact("attempt")
        with patch.object(
            RECOVERY, "gh", side_effect=[json.dumps(base).encode(), zips["attempt"]]
        ):
            files = RECOVERY.reader039_artifact("attempt")
        self.assertEqual(set(files), {"attempt.json", "SHA256SUMS"})

    def test_main_reads_only_original_custody_and_emits_no_mutation_authority(self):
        saved, zips, values, env = self.fixture()
        trace = []

        def fake_gh(path, *, binary=False):
            trace.append((path, binary))
            if path == "actions/runs/41339202271":
                return json.dumps(saved["run"]).encode()
            if path == "actions/runs/41339202271/attempts/1/jobs?per_page=100":
                return json.dumps(saved["jobs"]).encode()
            for kind, spec in RECOVERY.READER039["artifacts"].items():
                if path == f"actions/artifacts/{spec['id']}":
                    return json.dumps(
                        next(
                            item
                            for item in saved["artifacts"]
                            if item["id"] == spec["id"]
                        )
                    ).encode()
                if path == f"actions/artifacts/{spec['id']}/zip":
                    self.assertTrue(binary)
                    self.assertNotEqual(kind, "image")
                    return zips[kind]
            self.fail("unexpected remote operation")

        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                with (
                    patch.dict(
                        os.environ,
                        env | {"GITHUB_OUTPUT": str(Path(directory) / "outputs")},
                    ),
                    patch.object(RECOVERY, "gh", side_effect=fake_gh),
                    patch.object(
                        RECOVERY.subprocess,
                        "check_output",
                        return_value=values["original_workflow"],
                    ),
                ):
                    RECOVERY.main()
                result = json.loads(
                    Path(
                        "staging-publication-recovery-evidence/recovery.json"
                    ).read_text()
                )
                self.assertEqual(
                    result["candidate_sha"], RECOVERY.READER039["candidate"]
                )
                output = Path("outputs").read_text()
                self.assertIn("recovery_run_id=41339202271\n", output)
                self.assertIn("recovery_image_id=sha256:80801ae", output)
                self.assertEqual(
                    (result["registry_mutations"], result["deployment_operations"]),
                    (0, 0),
                )
            finally:
                os.chdir(previous)
        self.assertEqual(len([item for item in trace if item[1]]), 4)
        self.assertFalse(
            any(
                f"/{RECOVERY.READER039['artifacts']['image']['id']}/zip" in path
                for path, _ in trace
            )
        )
