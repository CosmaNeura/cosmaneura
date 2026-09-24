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
from contextlib import contextmanager, nullcontext
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

    def configure_version_two(self):
        self.plan.update(
            version=2, candidate_sha="e" * 40, candidate_pr=479, controller_check_id=0
        )
        self.plan["release"] = {
            "schema_before": "c" * 12,
            "schema_after": "f" * 12,
            "migration_revisions": ["d" * 12, "f" * 12],
            "review_kind": "local_cursor",
            "runtime_public": {},
            "recovery": {},
        }
        self.put(
            "selection",
            {"founder_selected": True, "candidate_pr": 479, "candidate_sha": "e" * 40},
        )
        self.put(
            "delegation",
            {
                "actor": "delegated_assistant",
                "staging_build_approved": True,
                "candidate_sha": "e" * 40,
                "production_approved": False,
            },
        )
        rehearsal = m.record(Path(self.plan["rehearsal"]["path"]).read_bytes())
        rehearsal["rehearsal"].update(
            source_revision="c" * 12,
            target_revision="f" * 12,
            forward_only=True,
            restore_verified=True,
            actual_online_migrations=[{"revision": x} for x in ("d" * 12, "f" * 12)],
        )
        self.put("rehearsal", rehearsal)
        return rehearsal

    def test_version_two_requires_exact_selection_and_rehearsed_schema(self):
        rehearsal = self.configure_version_two()
        self.assertTrue(self.invoke(check_only=True)["local_bindings_passed"])
        for field, value in (("candidate_sha", "a" * 40), ("candidate_pr", 480)):
            with self.subTest(field=field):
                original = self.plan[field]
                self.plan[field] = value
                with self.assertRaises(m.BuildControllerRefused):
                    self.invoke(check_only=True)
                self.plan[field] = original
        rehearsal["rehearsal"]["restore_verified"] = False
        self.put("rehearsal", rehearsal)
        with self.assertRaises(m.BuildControllerRefused):
            self.invoke(check_only=True)

    def configure_unchanged_schema(self):
        rehearsal = self.configure_version_two()
        self.plan["release"].update(schema_before="f" * 12, migration_revisions=[])
        rehearsal["rehearsal"].update(
            source_revision="f" * 12, actual_online_migrations=[]
        )
        self.put("rehearsal", rehearsal)
        return rehearsal

    def test_unchanged_schema_reaches_source_and_build_with_exact_head(self):
        self.configure_unchanged_schema()
        self.assertTrue(self.invoke(check_only=True)["local_bindings_passed"])
        modules = self.fake_modules()
        with patch.object(m, "load_modules", return_value=modules):
            source = self.invoke(source_only=True)
            result = self.invoke()
        self.assertEqual(source["schema_head"], "f" * 12)
        self.assertTrue(result["complete"])
        self.assertEqual(
            modules["verify_staging_candidate"].HumanCandidateContext.call_args.args[2],
            "f" * 12,
        )
        modules["staging_human_build_executor"].build_candidate.assert_called_once()

    def test_unchanged_schema_requires_no_actual_or_declared_migrations(self):
        rehearsal = self.configure_unchanged_schema()
        for field, value in (
            ("schema_before", "c" * 12),
            ("migration_revisions", ["f" * 12]),
        ):
            with self.subTest(field=field):
                original = self.plan["release"][field]
                self.plan["release"][field] = value
                with self.assertRaises(m.BuildControllerRefused):
                    self.invoke(check_only=True)
                self.plan["release"][field] = original
        for field, value in (
            ("source_revision", "c" * 12),
            ("target_revision", "c" * 12),
            ("actual_online_migrations", [{"revision": "f" * 12}]),
            ("restore_verified", False),
        ):
            with self.subTest(field=field):
                original = rehearsal["rehearsal"][field]
                rehearsal["rehearsal"][field] = value
                self.put("rehearsal", rehearsal)
                with self.assertRaises(m.BuildControllerRefused):
                    self.invoke(check_only=True)
                rehearsal["rehearsal"][field] = original

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

    def invoke(self, *, check_only=False, source_only=False):
        raw = m.canonical(self.plan)
        path = self.root / "plan.json"
        path.write_bytes(raw)
        return m.run(
            path,
            m.sha(raw),
            self.root,
            check_only=check_only,
            source_only=source_only,
        )

    def invoke_with_output(self, commit_state, emit_success):
        raw = m.canonical(self.plan)
        path = self.root / "plan.json"
        path.write_bytes(raw)
        return m.run(
            path,
            m.sha(raw),
            self.root,
            check_only=False,
            commit_state=commit_state,
            emit_success=emit_success,
        )

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
            source_archive=b"invented-source",
            source_archive_sha256=m.sha(b"invented-source"),
            github_user_sha256=self.plan["github_user_sha256"],
            protection_sha256="policy",
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
            metadata = build_root / "artifacts/build-result.json"
            metadata.write_bytes(b'{"invented":"metadata"}\n')
            result = types.SimpleNamespace(
                path=archive,
                sha256=m.sha(archive.read_bytes()),
                size=archive.stat().st_size,
                image_id="sha256:" + "7" * 64,
                metadata_path=metadata,
                public_assets_sha256="8" * 64,
            )
            kwargs["finalize_archive"](result)
            return result

        executor.build_candidate.side_effect = build
        executor.file_sha.side_effect = lambda path, maximum: (
            m.sha(path.read_bytes()),
            path.stat().st_size,
        )
        process = Mock()
        process.defer_control_signals.side_effect = nullcontext
        return {
            "staging_build_source": source,
            "staging_human_build_inputs": inputs,
            "staging_human_build_process": process,
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

    def test_authenticated_source_only_mode_never_prepares_or_builds(self):
        modules = self.fake_modules()
        with patch.object(m, "load_modules", return_value=modules):
            value = self.invoke(source_only=True)
        self.assertTrue(value["local_bindings_passed"])
        self.assertTrue(value["authenticated_source_checked"])
        self.assertFalse(value["build_started"])
        self.assertEqual(value["candidate_sha"], m.CANDIDATE)
        self.assertEqual(value["approval_pr_number"], 385)
        self.assertEqual(value["schema_head"], m.REVISIONS[-1])
        self.assertEqual(value["controller_sha"], self.plan["controller_sha"])
        self.assertEqual(value["github_user_sha256"], self.plan["github_user_sha256"])
        modules["staging_human_build_inputs"].prepare_build_inputs.assert_not_called()
        modules["staging_human_build_executor"].build_candidate.assert_not_called()
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

    def test_executor_must_invoke_final_verification_before_return(self):
        modules = self.fake_modules()
        build = modules["staging_human_build_executor"].build_candidate.side_effect

        def skip_finalization(*args, **kwargs):
            kwargs["finalize_archive"] = lambda _archive: None
            return build(*args, **kwargs)

        modules[
            "staging_human_build_executor"
        ].build_candidate.side_effect = skip_finalization
        with (
            patch.object(m, "load_modules", return_value=modules),
            self.assertRaises(m.BuildControllerRefused),
        ):
            self.invoke()
        value = json.loads((self.parent / "controller-result.json").read_bytes())
        self.assertFalse(value["complete"])

    def test_late_executor_refusal_cannot_persist_completion(self):
        modules = self.fake_modules()
        build = modules["staging_human_build_executor"].build_candidate.side_effect

        def refuse_after_finalization(*args, **kwargs):
            build(*args, **kwargs)
            raise RuntimeError("invented deadline refusal after finalization")

        modules[
            "staging_human_build_executor"
        ].build_candidate.side_effect = refuse_after_finalization
        with (
            patch.object(m, "load_modules", return_value=modules),
            self.assertRaises(RuntimeError),
        ):
            self.invoke()
        value = json.loads((self.parent / "controller-result.json").read_bytes())
        self.assertFalse(value["complete"])
        self.assertNotIn("image_id", value)

    def test_success_record_commit_failure_persists_only_incomplete_result(self):
        modules = self.fake_modules()
        with (
            patch.object(m, "load_modules", return_value=modules),
            patch.object(m.os, "rename", side_effect=OSError("invented refusal")),
            self.assertRaises(OSError),
        ):
            self.invoke()
        value = json.loads((self.parent / "controller-result.json").read_bytes())
        self.assertFalse(value["complete"])
        self.assertEqual(value["phase"], "commit_success_record")
        self.assertNotIn("image_id", value)

    def test_interrupt_after_attempt_creation_persists_incomplete_result(self):
        modules = self.fake_modules()

        @contextmanager
        def interrupted_exit():
            yield
            raise KeyboardInterrupt()

        modules["staging_human_build_process"].defer_control_signals.side_effect = [
            interrupted_exit(),
            nullcontext(),
        ]
        with (
            patch.object(m, "load_modules", return_value=modules),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.invoke()
        value = json.loads((self.parent / "controller-result.json").read_bytes())
        self.assertFalse(value["complete"])
        self.assertEqual(value["phase"], "create_attempt")

    def test_interrupt_after_success_temp_persists_incomplete_result(self):
        modules = self.fake_modules()
        write = modules["staging_human_build_inputs"].write_exclusive.side_effect

        def interrupted_write(path, raw, mode):
            write(path, raw, mode)
            if path.name == "controller-result.success.tmp":
                raise KeyboardInterrupt()

        modules[
            "staging_human_build_inputs"
        ].write_exclusive.side_effect = interrupted_write
        with (
            patch.object(m, "load_modules", return_value=modules),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.invoke()
        value = json.loads((self.parent / "controller-result.json").read_bytes())
        self.assertFalse(value["complete"])
        self.assertEqual(value["phase"], "prepare_success_record")

    def test_directory_sync_failure_replaces_success_with_incomplete_result(self):
        modules = self.fake_modules()
        with (
            patch.object(m, "load_modules", return_value=modules),
            patch.object(
                m,
                "sync_directory",
                side_effect=[OSError("invented sync refusal"), None],
            ),
            self.assertRaises(OSError),
        ):
            self.invoke()
        value = json.loads((self.parent / "controller-result.json").read_bytes())
        self.assertFalse(value["complete"])
        self.assertEqual(value["phase"], "commit_success_record")

    def test_success_output_failure_is_best_effort_after_durable_commit(self):
        modules = self.fake_modules()
        committed = {}
        output = Mock(side_effect=BrokenPipeError("invented closed output"))
        with patch.object(m, "load_modules", return_value=modules):
            value = self.invoke_with_output(committed, output)
        self.assertTrue(value["complete"])
        self.assertEqual(committed["result"], value)
        output.assert_called_once_with(value)
        self.assertEqual(
            json.loads((self.parent / "controller-result.json").read_bytes()), value
        )

    def test_interrupt_after_atomic_success_commit_remains_success(self):
        modules = self.fake_modules()

        @contextmanager
        def interrupted_exit():
            yield
            raise KeyboardInterrupt()

        modules["staging_human_build_process"].defer_control_signals.side_effect = [
            nullcontext(),
            interrupted_exit(),
        ]
        with patch.object(m, "load_modules", return_value=modules):
            value = self.invoke()
        self.assertTrue(value["complete"])
        self.assertEqual(
            json.loads((self.parent / "controller-result.json").read_bytes()), value
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
        self.assertEqual(
            value["build_metadata_sha256"],
            m.sha(Path(value["build_metadata"]).read_bytes()),
        )
        self.assertEqual(
            value["build_metadata_bytes"], Path(value["build_metadata"]).stat().st_size
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

    def test_real_input_validation_and_preparation_accept_controller_timestamp(self):
        fixture_spec = importlib.util.spec_from_file_location(
            "controller_real_input_fixture",
            ROOT / "tests/infrastructure/test_staging_human_build_inputs.py",
        )
        assert fixture_spec and fixture_spec.loader
        fixture = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixture)
        real_inputs = fixture.m
        config = fixture.configuration() | {
            "VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED": "false"
        }
        self.put("frontend", config)
        names = (
            "VITE_CLERK_PUBLIC_KEY",
            "VITE_SENTRY_DSN",
            "VITE_POSTHOG_HOST",
            "VITE_POSTHOG_KEY",
        )
        self.put(
            "frontend_origin",
            {
                "complete": True,
                "public_fields_only": True,
                "configuration_sha256": self.plan["frontend"]["sha256"],
                "fields": [
                    {"name": name, "sha256": m.sha(config[name].encode())}
                    for name in names
                ],
            },
        )
        archive = fixture.archive(
            fixture.source_files()
            + [
                (
                    "migrations/versions/" + revision + ".py",
                    body,
                    fixture.tarfile.REGTYPE,
                )
                for revision, body in zip(
                    m.REVISIONS, self.migration_bodies, strict=True
                )
            ],
            comment=m.CANDIDATE,
        )
        self.plan["source_archive_sha256"] = m.sha(archive)
        modules = self.fake_modules()
        modules["staging_human_build_inputs"] = real_inputs
        modules[
            "staging_build_source"
        ].collect_build_source.return_value.source_archive = archive

        def fake_build(prepared, source_archive, **kwargs):
            self.assertEqual(source_archive, archive)
            self.assertRegex(
                prepared.inputs.created_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
            )
            self.assertEqual(
                (prepared.root / "source/.env").read_bytes(),
                (ROOT / ".env").read_bytes(),
            )
            kwargs["validate_authority"]()
            image = prepared.root / "artifacts/image.tar"
            image.write_bytes(b"invented image")
            metadata = prepared.root / "artifacts/build-result.json"
            metadata.write_bytes(b'{"invented":"metadata"}\n')
            result = types.SimpleNamespace(
                path=image,
                sha256=m.sha(image.read_bytes()),
                size=image.stat().st_size,
                image_id="sha256:" + "7" * 64,
                metadata_path=metadata,
                public_assets_sha256="8" * 64,
            )
            kwargs["finalize_archive"](result)
            return result

        modules["staging_human_build_executor"].build_candidate.side_effect = fake_build
        with (
            patch.object(m, "load_modules", return_value=modules),
            patch.object(
                real_inputs, "native_builder_bytes", return_value=fixture.BINARY
            ),
        ):
            value = self.invoke()
        self.assertTrue(value["complete"])


if __name__ == "__main__":
    unittest.main()
