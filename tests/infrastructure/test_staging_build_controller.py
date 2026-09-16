"""Invented build inputs; actual file binding, mocked GitHub/engine/build calls."""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "build_controller_test_subject", ROOT / "scripts/staging_build_controller.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="cosmaneura-build-test-", dir="/private/tmp"
        )
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        self.parent = Path("/private/tmp") / (
            "cosmaneura-staging-build-" + uuid.uuid4().hex
        )
        now = datetime.now(UTC)
        self.plan = {
            "version": 1,
            "action": "build_selected_staging_candidate",
            "actor": "delegated_assistant",
            "candidate_sha": m.CANDIDATE,
            "candidate_pr": 385,
            "controller_sha": "2" * 40,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=2)).isoformat(),
            "build_seconds": 60,
            "module_sha256s": {},
            "github_user_sha256": "3" * 64,
            "source_archive_sha256": m.sha(b"invented-source"),
            "controller_pr": 426,
            "controller_reviewed_head": "4" * 40,
            "controller_review_id": 100,
            "controller_check_id": 101,
            "controller_review_sha256": "5" * 64,
            "engine": {"id_sha256": "6" * 64, "socket_device": 1, "socket_inode": 2},
            "attempt_parent": str(self.parent),
        }
        for name in m.MODULES:
            body = b"# invented module, never imported\n"
            (self.root / "scripts" / name).write_bytes(body)
            self.plan["module_sha256s"][name] = m.sha(body)
        self.put(
            "selection",
            {
                "founder_selected": True,
                "candidate_pr": 385,
                "candidate_sha": m.CANDIDATE,
            },
        )
        self.put(
            "delegation",
            {
                "actor": "delegated_assistant",
                "staging_build_approved": True,
                "candidate_sha": m.CANDIDATE,
                "production_approved": False,
            },
        )
        config = {
            name: "invented-public"
            for name in (
                "VITE_CLERK_PUBLIC_KEY",
                "VITE_SENTRY_DSN",
                "VITE_POSTHOG_HOST",
                "VITE_POSTHOG_KEY",
            )
        }
        config["VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"] = "false"
        self.put("frontend", config)
        self.put(
            "frontend_origin",
            {
                "complete": True,
                "public_fields_only": True,
                "configuration_sha256": self.plan["frontend"]["sha256"],
                "fields": [
                    {"name": name, "sha256": m.sha(value.encode())}
                    for name, value in config.items()
                    if name != "VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"
                ],
            },
        )
        self.migration_bodies = [
            b"invented migration " + revision.encode() for revision in m.REVISIONS
        ]
        outcome = {
            "result": "PASS",
            "source_revision": "89c31f0ae275",
            "target_revision": m.REVISIONS[-1],
            "actual_online_migrations": [
                {"revision": revision, "source_sha256": m.sha(body)}
                for revision, body in zip(
                    m.REVISIONS, self.migration_bodies, strict=True
                )
            ],
        }
        for key in (
            "committed_revision_verified",
            "original_rows_preserved",
            "original_constraints_indexes_triggers_preserved",
            "backfill_and_baa_invariants_passed",
            "reader_select_and_four_negative_access_checks_passed",
        ):
            outcome[key] = True
        self.put(
            "rehearsal",
            {
                "complete": True,
                "archive_unchanged": True,
                "owned_containers_stopped": True,
                "actor": "delegated_assistant",
                "runner_sha256": "a" * 64,
                "worker_sha256": "b" * 64,
                "plan_sha256": "c" * 64,
                "rehearsal": outcome,
            },
        )
        self.put("native_archive", b"invented-native-archive")

    def tearDown(self):
        self.temp.cleanup()
        if self.parent.exists():
            shutil.rmtree(self.parent)

    def put(self, name, value):
        body = value if isinstance(value, bytes) else m.canonical(value)
        path = self.root / (name + ".json")
        path.write_bytes(body)
        path.chmod(0o600)
        self.plan[name] = {"path": str(path), "sha256": m.sha(body)}

    def invoke(self, *, check_only=False):
        raw = m.canonical(self.plan)
        path = self.root / "plan.json"
        path.write_bytes(raw)
        return m.run(path, m.sha(raw), self.root, check_only=check_only)

    def fake_modules(
        self,
        *,
        failure=False,
        marker=False,
        changed_source=False,
        wrong_migration=False,
    ):
        source = Mock()
        source.collect_build_source.return_value = types.SimpleNamespace(
            source_archive=b"invented-source", protection_sha256="policy"
        )
        source.BuildSourceReader.return_value.protections.return_value = "policy"
        if changed_source:
            source.BuildSourceReader.return_value.final_boundaries.side_effect = (
                RuntimeError("invented drift")
            )
        inputs = Mock()
        bodies = [b"wrong migration"] if wrong_migration else self.migration_bodies
        inputs.source_entries.return_value = [
            ("migrations/versions/" + str(i) + ".py", body, False)
            for i, body in enumerate(bodies)
        ]
        build_root = self.parent / "staging-build-invented"

        def prepare(*args, **kwargs):
            (build_root / "artifacts").mkdir(parents=True)
            return types.SimpleNamespace(root=build_root)

        def write(path, raw, mode):
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "wb"
            ) as stream:
                stream.write(raw)

        inputs.prepare_build_inputs.side_effect = prepare
        inputs.write_exclusive.side_effect = write
        executor = Mock()
        executor.MAX_IMAGE = 10000

        def build(*args, **kwargs):
            kwargs["validate_authority"]()
            archive = build_root / "artifacts/image.tar"
            archive.write_bytes(b"invented image archive")
            if failure:
                raise RuntimeError("invented failure after output")
            if marker:
                (build_root / "artifacts/build-failure.json").write_text("{}")
            return types.SimpleNamespace(
                path=archive,
                sha256=m.sha(archive.read_bytes()),
                size=archive.stat().st_size,
                image_id="sha256:" + "7" * 64,
                metadata_path=build_root / "artifacts/build-result.json",
                public_assets_sha256="8" * 64,
            )

        executor.build_candidate.side_effect = build
        executor.file_sha.side_effect = lambda path, maximum: (
            m.sha(path.read_bytes()),
            path.stat().st_size,
        )
        return {
            "staging_build_source": source,
            "staging_human_build_inputs": inputs,
            "staging_human_build_executor": executor,
            "verify_staging_candidate": Mock(),
        }

    def test_read_only_preflight_never_imports_or_starts_build(self):
        with patch.object(m, "load_modules") as load:
            value = self.invoke(check_only=True)
        self.assertTrue(value["local_bindings_passed"])
        self.assertFalse(value["authenticated_source_checked"])
        load.assert_not_called()
        self.assertFalse(self.parent.exists())

    def test_changed_helper_refuses_before_any_import(self):
        (self.root / "scripts/staging_build_source.py").write_text(
            "raise RuntimeError('must not run')"
        )
        with (
            patch.object(m, "load_modules") as load,
            self.assertRaises(m.BuildControllerRefused),
        ):
            self.invoke()
        load.assert_not_called()
        self.assertFalse(self.parent.exists())

    def test_full_build_budget_and_ten_minute_margin_required_before_import(self):
        self.plan["build_seconds"] = 600
        self.plan["expires_at"] = (
            datetime.now(UTC) + timedelta(seconds=900)
        ).isoformat()
        with (
            patch.object(m, "load_modules") as load,
            self.assertRaises(m.BuildControllerRefused),
        ):
            self.invoke()
        load.assert_not_called()
        self.assertFalse(self.parent.exists())

    def test_symlink_and_writable_input_refused(self):
        original = Path(self.plan["frontend"]["path"])
        link = self.root / "alias"
        link.symlink_to(original)
        with self.assertRaises(m.BuildControllerRefused):
            m.read_bound(link, self.plan["frontend"]["sha256"], 16384)
        original.chmod(0o666)
        with self.assertRaises(m.BuildControllerRefused):
            m.read_bound(original, self.plan["frontend"]["sha256"], 16384)

    def test_invalid_scope_and_expiry_refused(self):
        for key, value in (
            ("candidate_sha", "a" * 40),
            ("candidate_pr", True),
            ("actor", "human"),
            ("build_seconds", 3601),
            ("controller_pr", True),
            ("attempt_parent", "/private/tmp/other"),
            ("expires_at", (datetime.now(UTC) - timedelta(seconds=1)).isoformat()),
        ):
            with self.subTest(key=key), self.assertRaises(m.BuildControllerRefused):
                m.validate_plan(self.plan | {key: value})
        with self.assertRaises(m.BuildControllerRefused):
            m.record(b'{"version":1,"version":1}')

    def test_changed_source_stops_before_workspace_preparation(self):
        modules = self.fake_modules(changed_source=True)
        with (
            patch.object(m, "load_modules", return_value=modules),
            self.assertRaises(RuntimeError),
        ):
            self.invoke()
        modules["staging_human_build_inputs"].prepare_build_inputs.assert_not_called()
        modules["staging_human_build_executor"].build_candidate.assert_not_called()
        self.assertFalse(
            json.loads((self.parent / "controller-result.json").read_bytes())[
                "complete"
            ]
        )

    def test_unrehearsed_migration_stops_before_attempt(self):
        modules = self.fake_modules(wrong_migration=True)
        with (
            patch.object(m, "load_modules", return_value=modules),
            self.assertRaises(m.BuildControllerRefused),
        ):
            self.invoke()
        self.assertFalse(self.parent.exists())

    def test_partial_image_and_failed_call_are_not_success(self):
        modules = self.fake_modules(failure=True)
        with (
            patch.object(m, "load_modules", return_value=modules),
            self.assertRaises(RuntimeError),
        ):
            self.invoke()
        value = json.loads((self.parent / "controller-result.json").read_bytes())
        self.assertFalse(value["complete"])
        self.assertEqual(value["phase"], "build")
        self.assertNotIn("image_id", value)

    def test_failure_marker_overrides_returned_archive(self):
        modules = self.fake_modules(marker=True)
        with (
            patch.object(m, "load_modules", return_value=modules),
            self.assertRaises(m.BuildControllerRefused),
        ):
            self.invoke()
        self.assertFalse(
            json.loads((self.parent / "controller-result.json").read_bytes())[
                "complete"
            ]
        )

    def test_success_preserves_actor_source_artifact_and_false_publish_authority(self):
        modules = self.fake_modules()
        with patch.object(m, "load_modules", return_value=modules):
            value = self.invoke()
        self.assertTrue(value["complete"])
        self.assertEqual(value["actor"], "delegated_assistant")
        self.assertEqual(value["candidate_sha"], m.CANDIDATE)
        self.assertFalse(
            value["publication_authorized"] or value["deployment_authorized"]
        )
        self.assertEqual(value["module_sha256s"], self.plan["module_sha256s"])
        self.assertEqual(
            value["image_archive_sha256"],
            m.sha(Path(value["image_archive"]).read_bytes()),
        )
        self.assertGreaterEqual(
            modules[
                "staging_build_source"
            ].BuildSourceReader.return_value.final_boundaries.call_count,
            3,
        )
        self.assertEqual(
            json.loads((self.parent / "controller-result.json").read_bytes()), value
        )


if __name__ == "__main__":
    unittest.main()
