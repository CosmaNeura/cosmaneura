"""INVENTED preparation only. No real human main, account/cache, ARM, IP or image.

Existing test modules supply fixture definitions, NOT repeated passing suites.
Native/source attestation and Terminal I/O are replaced explicitly. Only the
publication tests write tiny invented bytes to their own temporary directory.
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fixtures = load(
    "preparation_public_fixtures",
    "tests/infrastructure/test_staging_snapshot_public_plan.py",
)
load(
    "preparation_human_fixtures", "tests/infrastructure/test_staging_snapshot_human.py"
)
console_fixtures = load(
    "preparation_console_fixtures",
    "tests/infrastructure/test_staging_snapshot_console.py",
)
boot = load("preparation_bootstrap", "scripts/staging_snapshot_bootstrap.py")
human = sys.modules["staging_snapshot_human"]
art = sys.modules["staging_snapshot_artifacts"]
azure = sys.modules["staging_snapshot_azure"]
console = sys.modules["staging_snapshot_console"]
native = sys.modules["staging_snapshot_native_files"]
config = sys.modules["staging_snapshot_configuration"]
core = sys.modules["staging_snapshot_capture"]
public = fixtures.module
NOW = fixtures.NOW
OBSERVATION_ROOT = str(art.PACKET_PARENT / ("human-observation-" + "d" * 32))


class Clock(datetime):
    value = NOW

    @classmethod
    def now(cls, tz=None):
        assert tz is UTC
        return cls.value


def fixture():
    result = fixtures.PublicPlanTests()
    result.setUp()
    return result


def preparation(f, **changes):
    arguments = {
        "expected_source_sha256": public.sha(f.source.provenance),
        "expected_source_review_sha256": public.sha(f.source_review.summary),
        "expected_workspace_manifest_sha256": public.sha(f.workspace),
        "hdiutil_sha256": "6" * 64,
        "diskutil_sha256": "7" * 64,
        "operator_uid": 501,
        "observation_root": OBSERVATION_ROOT,
        "human_presence_confirmed_at": NOW,
    }
    arguments.update(changes)
    with patch.object(public, "now_utc", return_value=NOW):
        return dict(
            public.preparation_files(
                f.source, f.source_review, f.workspace, **arguments
            )
        )


class PreparationArtifactTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        Clock.value = NOW
        self.stack.enter_context(patch.object(art, "datetime", Clock))
        self.stack.enter_context(patch.object(art.PreparationArtifacts, "_types"))
        self.stack.enter_context(patch.object(art.PreparationArtifacts, "_approval"))
        self.f = fixture()
        self.packet = preparation(self.f)
        self.generation = 1
        owner = self

        class Files:
            def read(_self, path, *, mode, maximum):
                owner.assertEqual(mode, 0o400)
                raw = owner.packet[path.name]
                owner.assertLessEqual(len(raw), maximum)
                return native.FileObservation(raw, (1, owner.generation, 501), (2, 3))

        self.source = SimpleNamespace(
            files=Files(),
            loader_sha256=self.f.files["staging_snapshot_bootstrap.py"],
            sources={
                name.removesuffix(".py"): SimpleNamespace(content=raw)
                for name, raw in self.f.source.source_files
                if name != "staging_snapshot_bootstrap.py"
            },
            loader_source=SimpleNamespace(file_identity=(1, 2, 501)),
            revalidate=Mock(),
        )
        self.root = art.PACKET_PARENT / ("human-preparation-" + "c" * 32)

    def read(self):
        return art.PreparationArtifacts(
            self.source,
            self.root,
            public.sha(self.packet["preparation.json"]),
            public.sha(self.packet["source-review-summary.json"]),
        )

    def change(self, **changes):
        value = json.loads(self.packet["preparation.json"])
        value.update(changes)
        self.packet["preparation.json"] = public.canonical(value)

    def test_public_producer_real_reader_agree_without_capture_artifacts(self):
        result = self.read()
        self.assertEqual(set(self.packet), set(result.catalog))
        self.assertEqual(len(self.packet), 6)
        self.assertFalse(hasattr(result, "inputs"))
        self.assertFalse(hasattr(result, "handoff_created"))
        self.assertEqual(result.observation_root, Path(OBSERVATION_ROOT))
        result.revalidate()
        result.require_start()

    def test_public_builder_refuses_unanchored_or_unauthorized_inputs(self):
        for changes in (
            {"expected_source_sha256": "0" * 64},
            {"expected_source_review_sha256": "0" * 64},
            {"expected_workspace_manifest_sha256": "0" * 64},
            {"operator_uid": True},
            {"hdiutil_sha256": "bad"},
            {"observation_root": "/private/tmp/unsafe"},
            {"human_presence_confirmed_at": NOW - timedelta(minutes=6)},
            {"human_presence_confirmed_at": NOW + timedelta(seconds=1)},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaises(public.PublicPlanRefused),
            ):
                preparation(self.f, **changes)

    def test_reader_rejects_authority_flags_operator_paths_and_lifetime(self):
        original = dict(self.packet)
        for changes in (
            {"execution_authorized": True},
            {"database_credentials_read": 0},
            {"firewall_mutated": True},
            {"operator_uid": 502},
            {"source_bundle_sha256": "0" * 64},
            {"workspace_source_sha256": "0" * 64},
            {"observation_root": str(self.root)},
            {"workspace_root": "/tmp/unsafe"},
            {"expires_at": public.encoded_time(NOW + timedelta(hours=1))},
            {
                "human_presence_confirmed_at": public.encoded_time(
                    NOW - timedelta(minutes=6)
                )
            },
        ):
            self.packet = dict(original)
            self.change(**changes)
            with (
                self.subTest(changes=changes),
                self.assertRaises(art.SnapshotArtifactsRefused),
            ):
                self.read()

    def test_source_review_fail_and_unmerged_or_stale_provenance_refuse(self):
        original = dict(self.packet)
        for state, observed in (
            ("OPEN", NOW),
            ("MERGED", NOW - timedelta(minutes=30, seconds=1)),
            ("MERGED", NOW + timedelta(seconds=1)),
        ):
            self.packet = dict(original)
            value = json.loads(self.packet["source-provenance.json"])
            value["pull_requests"][0]["state"] = state
            value["observed_at"] = public.encoded_time(observed)
            self.packet["source-provenance.json"] = public.canonical(value)
            self.change(
                source_provenance_sha256=public.sha(
                    self.packet["source-provenance.json"]
                )
            )
            with (
                self.subTest(state=state, observed=observed),
                self.assertRaises(art.SnapshotArtifactsRefused),
            ):
                self.read()
        self.packet = dict(original)
        value = json.loads(self.packet["source-review-summary.json"])
        value["verdict"] = "FAIL"
        self.packet["source-review-summary.json"] = public.canonical(value)
        self.change(
            source_review_summary_sha256=public.sha(
                self.packet["source-review-summary.json"]
            )
        )
        with self.assertRaises(art.SnapshotArtifactsRefused):
            self.read()

    def test_provenance_at_exact_freshness_limit_is_accepted(self):
        value = json.loads(self.packet["source-provenance.json"])
        value["observed_at"] = public.encoded_time(NOW - timedelta(minutes=30))
        self.packet["source-provenance.json"] = public.canonical(value)
        self.change(
            source_provenance_sha256=public.sha(self.packet["source-provenance.json"])
        )
        self.read()

    def test_retained_inode_and_time_cannot_be_renewed(self):
        result = self.read()
        self.generation += 1
        with self.assertRaises(art.SnapshotArtifactsRefused):
            result.revalidate()
        Clock.value = NOW + timedelta(minutes=30)
        with self.assertRaises(art.SnapshotArtifactsRefused):
            result.require_start()

    def test_preparation_runtime_accepts_later_start_without_extending_artifact(self):
        result = self.read()
        original = dict(self.packet)
        Clock.value = NOW + timedelta(minutes=20)
        result.require_start()
        Clock.value = NOW + timedelta(minutes=29, seconds=59)
        result.require_start()
        self.assertEqual(self.packet, original)
        Clock.value = NOW + timedelta(minutes=30)
        with self.assertRaises(art.SnapshotArtifactsRefused):
            result.require_start()


class PreparationConsoleTests(unittest.TestCase):
    def setUp(self):
        self.f = console_fixtures.ConsoleTests()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.value = console.PreparationConsole()
        self.addCleanup(self.value.close)

    def test_one_confirmation_without_any_firewall_capability(self):
        self.f.answers.append(b"PREPARE\n")
        self.value.confirm_preparation("a" * 64, 60)
        self.assertIn(b"READ-ONLY", self.f.output)
        self.assertIn(b"a" * 64, self.f.output)
        self.assertEqual(len(self.f.flushes), 1)
        self.assertFalse(hasattr(self.value, "present_manual_window_instructions"))
        self.assertFalse(hasattr(self.value, "await_human_rule_creation"))
        with self.assertRaises(console.HumanConsoleRefused):
            self.value.confirm_preparation("a" * 64, 60)

    def test_decline_or_changed_foreground_terminal_refuses(self):
        self.f.answers.append(b"NO\n")
        with self.assertRaises(console.HumanConsoleRefused):
            self.value.confirm_preparation("a" * 64, 60)
        self.f.groups[1] = 99
        with self.assertRaises(console.HumanConsoleRefused):
            console.PreparationConsole()


class PreparationProjectionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture()
        self.artifacts = SimpleNamespace(
            preparation=json.loads(preparation(self.f)["preparation.json"]),
            source_commit=fixtures.COMMIT,
            source_bundle_sha256=self.f.bundle,
        )
        self.observed = {
            "target_matches": True,
            "identity_evidence": self.f.observation["identity"],
            "server_body_sha256": "5" * 64,
            "exact_token_sha256": "4" * 64,
            "unused_body": "INVENTED_SECRET_CANARY",
        }
        self.rules = (core.Rule("Existing", "0.0.0.0", "0.0.0.0"),)
        self.clock = patch.object(human, "datetime", Clock)
        Clock.value = NOW
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def project(self):
        return human.preparation_observation(
            self.artifacts, self.observed, "8" * 64, self.rules, "8.8.8.8"
        )

    def test_projection_matches_existing_plan_producer_exactly(self):
        raw = self.project()
        self.assertEqual(json.loads(raw), self.f.observation)
        self.assertNotIn(b"INVENTED_SECRET_CANARY", raw)
        with patch.object(public, "now_utc", return_value=NOW):
            plan = public.create_plan(
                self.f.source,
                self.f.source_review,
                raw,
                self.f.workspace,
                expected_source_sha256=public.sha(self.f.source.provenance),
                expected_source_review_sha256=public.sha(self.f.source_review.summary),
                expected_observation_sha256=public.sha(raw),
                rule_name=fixtures.RULE,
            )
        self.assertEqual(
            json.loads(plan.plan)["identity_evidence_sha256"], public.sha(plan.identity)
        )

    def test_identity_extra_field_linkage_token_age_and_headroom_refuse(self):
        original = copy.deepcopy(self.observed)
        for changes in (
            {"extra": "INVENTED_SECRET_CANARY"},
            {"authenticated_same_token_arm_read": False},
            {"configured_home_account_linked_to_exact_arm_token": False},
            {"token_expires_epoch": int(NOW.timestamp()) + 2430},
            {"observed_at_epoch": int(NOW.timestamp()) - 121},
            {"exact_arm_token_sha256": "9" * 64},
            {"arm_read_body_sha256": "9" * 64},
        ):
            self.observed = copy.deepcopy(original)
            self.observed["identity_evidence"].update(changes)
            with (
                self.subTest(changes=changes),
                self.assertRaises(
                    (art.SnapshotArtifactsRefused, human.SnapshotHumanRefused)
                ),
            ):
                self.project()


class PreparationOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.events, self.fail_at = [], set()
        self.clock, self.active = 100.0, False
        self.advance_at = {}
        Clock.value = NOW
        self.f = fixture()
        self.p = json.loads(preparation(self.f)["preparation.json"])
        owner = self

        class Source:
            files, runtime = object(), object()

            def revalidate(_self):
                owner.assertTrue(owner.active)
                owner.step("source")

        self.source = Source()
        self.patch(sys.modules["__main__"], "SourceRuntimeBinding", Source, create=True)
        self.patch(human, "_attempted", [])
        self.patch(human, "require_capture_process", Mock())
        self.patch(human, "capture_deadline", self.timer)
        self.patch(human, "time", SimpleNamespace(monotonic=lambda: self.clock))
        self.patch(human, "datetime", Clock)
        self.artifacts = SimpleNamespace(
            preparation=self.p,
            source_commit=fixtures.COMMIT,
            source_bundle_sha256=self.f.bundle,
            preparation_sha256="a" * 64,
            workspace_root=Path(self.p["workspace_root"]),
            revalidate=lambda: self.checked("artifacts_revalidate"),
            require_start=lambda: self.checked("start"),
        )
        self.patch(
            human, "PreparationArtifacts", self.factory("artifacts", self.artifacts)
        )
        self.console = SimpleNamespace(
            confirm_preparation=lambda *_: self.step("confirm"),
            close=lambda: self.step("console_close"),
        )
        self.patch(human, "PreparationConsole", self.factory("console", self.console))
        self.area, self.copied, self.cli, self.session = (object() for _ in range(4))
        workspace = SimpleNamespace(
            open_area=lambda **_: self.context("area", self.area),
            revalidate=lambda *_a, **_k: self.checked("workspace_revalidate"),
        )
        self.patch(human, "SnapshotWorkspace", self.factory("workspace", workspace))
        configured = SimpleNamespace(
            private_copy=lambda *_a, **_k: self.context("copy", self.copied),
            revalidate=lambda: self.checked("configuration_revalidate"),
        )
        self.patch(human, "ConfiguredSource", self.factory("configured", configured))
        self.patch(human, "OwnedAzureCLI", self.factory("cli", self.cli))
        self.patch(human, "SnapshotSession", self.factory("session", self.session))
        self.azure = SimpleNamespace(
            observe_application_binding=self.factory("app", "8" * 64),
            list_rules=self.factory(
                "rules", (core.Rule("Existing", "0.0.0.0", "0.0.0.0"),)
            ),
            observe_server=self.factory(
                "server",
                {
                    "target_matches": True,
                    "identity_evidence": self.f.observation["identity"],
                    "server_body_sha256": "5" * 64,
                    "exact_token_sha256": "4" * 64,
                },
            ),
        )
        self.patch(human, "SnapshotAzure", self.factory("azure", self.azure))

        def ip(_files, check):
            def current(timeout):
                owner.step("ip")
                with owner.timer(owner.clock + timeout):
                    check()
                return "8.8.8.8"

            return SimpleNamespace(current_ipv4=current)

        self.patch(human, "SnapshotPublicIP", ip)
        self.raw: bytes | None = None

        def publish(_artifacts, raw):
            owner.step("publish")
            owner.raw = raw
            return {"status": "read_only_preparation_observed"}

        self.patch(human, "publish_preparation", publish)
        self.capture = Mock(side_effect=AssertionError("capture must never run"))
        self.patch(human, "execute_after_human_gate", self.capture)

    def patch(self, obj, name, value, **kwargs):
        self.stack.enter_context(patch.object(obj, name, value, **kwargs))

    def step(self, name):
        self.events.append(name)
        self.clock += self.advance_at.get(name, 0)
        if name in self.fail_at:
            raise RuntimeError("INVENTED_SECRET_CANARY")

    def checked(self, name):
        self.assertTrue(self.active)
        self.step(name)

    def factory(self, name, result):
        def make(*_args, **_kwargs):
            self.step(name)
            return result

        return make

    @contextmanager
    def timer(self, deadline):
        self.assertFalse(self.active)
        self.assertGreater(deadline, self.clock)
        self.assertLessEqual(deadline, 700)
        self.active = True
        try:
            yield
        finally:
            self.active = False

    @contextmanager
    def context(self, name, value):
        self.step(name + "_open")
        try:
            yield value
        finally:
            self.step(name + "_close")

    def run_preparation(self):
        return human.run_human_preparation(
            self.source, Path("/private/tmp/invented"), "a" * 64, "b" * 64
        )

    def test_ordered_read_only_flow_and_publication_after_successful_closes(self):
        attempted = human._attempted
        self.assertEqual(
            self.run_preparation()["status"], "read_only_preparation_observed"
        )
        self.assertIs(attempted, human._attempted)
        self.assertEqual(attempted, [True])
        for first, last in (
            ("artifacts", "confirm"),
            ("confirm", "configured"),
            ("ip", "server"),
            ("server", "copy_close"),
            ("area_close", "console_close"),
            ("console_close", "publish"),
        ):
            self.assertLess(self.events.index(first), self.events.index(last))
        self.assertEqual(self.events.count("ip"), 1)
        self.capture.assert_not_called()
        assert self.raw is not None
        self.assertEqual(json.loads(self.raw), self.f.observation)

    def test_confirmation_failure_precedes_all_configured_credentials(self):
        self.fail_at.add("confirm")
        with self.assertRaisesRegex(
            human.SnapshotHumanRefused, "^snapshot_preparation_refused$"
        ):
            self.run_preparation()
        self.assertNotIn("configured", self.events)
        self.assertNotIn("publish", self.events)
        self.assertEqual(self.events[-1], "console_close")

    def test_source_artifact_failure_precedes_terminal(self):
        self.fail_at.add("artifacts")
        with self.assertRaises(human.SnapshotHumanRefused):
            self.run_preparation()
        self.assertNotIn("console", self.events)
        self.assertNotIn("configured", self.events)

    def test_slow_preparation_cannot_reset_total_deadline(self):
        self.advance_at["session"] = 601
        with self.assertRaises(human.SnapshotHumanRefused):
            self.run_preparation()
        self.assertNotIn("app", self.events)
        self.assertNotIn("publish", self.events)

    def test_multiple_read_only_stages_may_exceed_four_minutes_within_ten(self):
        self.advance_at.update(confirm=100, copy_open=50, session=100, app=40)
        self.assertEqual(
            self.run_preparation()["status"], "read_only_preparation_observed"
        )
        self.assertGreater(self.clock, 340)
        self.assertLess(self.clock, 700)
        self.capture.assert_not_called()

    def test_server_or_close_failure_never_publishes_success(self):
        self.fail_at.add("copy_close")
        with self.assertRaises(human.SnapshotHumanRefused):
            self.run_preparation()
        self.assertIn("server", self.events)
        self.assertNotIn("publish", self.events)

    def test_final_server_refusal_still_closes_both_descriptors_and_terminal(self):
        self.fail_at.add("server")
        with self.assertRaises(human.SnapshotHumanRefused):
            self.run_preparation()
        self.assertEqual(
            self.events[-3:], ["copy_close", "area_close", "console_close"]
        )
        self.assertNotIn("publish", self.events)

    def test_terminal_close_failure_does_not_publish(self):
        self.fail_at.add("console_close")
        with self.assertRaises(human.SnapshotHumanRefused):
            self.run_preparation()
        self.assertNotIn("publish", self.events)

    def test_changed_retained_artifacts_refuse_before_opening_workspace(self):
        self.fail_at.add("artifacts_revalidate")
        with self.assertRaises(human.SnapshotHumanRefused):
            self.run_preparation()
        self.assertIn("confirm", self.events)
        self.assertNotIn("workspace", self.events)
        self.assertNotIn("configured", self.events)

    def test_shared_attempt_prevents_preparation_or_capture_retry(self):
        self.run_preparation()
        count = len(self.events)
        for operation in (
            self.run_preparation,
            lambda: human.run_human_snapshot(
                self.source, Path("/invented"), "a" * 64, "b" * 64
            ),
        ):
            with self.assertRaises(human.SnapshotHumanRefused):
                operation()
            self.assertEqual(len(self.events), count)


class PreparationPublicationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(
            self.stack.enter_context(
                tempfile.TemporaryDirectory(prefix="invented-preparation-output-")
            )
        ).resolve()
        owner = self

        class Files:
            @contextmanager
            def protected_parent(_self, path):
                owner.assertEqual(path.parent, owner.root)
                fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    yield fd
                finally:
                    os.close(fd)

            def acl_safe(_self, _fd):
                pass  # Native ACL binding explicitly outside this fixture.

            def read(_self, path, **_kwargs):
                return native.FileObservation(
                    path.read_bytes(),
                    native.identity(path.stat()),
                    native.identity(path.parent.stat()),
                )

        self.artifacts = object.__new__(art.PreparationArtifacts)
        self.artifacts.observation_root = self.root
        self.artifacts._source = SimpleNamespace(files=Files())
        self.stack.enter_context(patch.object(art.PreparationArtifacts, "revalidate"))
        self.stack.enter_context(
            patch.object(art.PreparationArtifacts, "require_start")
        )
        self.stack.enter_context(patch.object(human, "require_capture_process"))
        self.stack.enter_context(patch.object(human, "private_umask"))
        self.stack.enter_context(
            patch.object(config, "defer_control_signals", nullcontext)
        )
        self.raw = b'{"invented":true}'
        self.path = self.root / "preparation-observation.json"

    def test_exclusive_output_has_exact_bytes_mode_and_no_overwrite(self):
        result = human.publish_preparation(self.artifacts, self.raw)
        self.assertEqual(result["observation_sha256"], public.sha(self.raw))
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o400)
        with self.assertRaises(human.SnapshotHumanRefused):
            human.publish_preparation(self.artifacts, self.raw)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_partial_write_failure_is_preserved_without_success_or_delete(self):
        original = os.write
        calls = []

        def write(fd, raw):
            calls.append(fd)
            if len(calls) == 1:
                return original(fd, raw[:3])
            raise OSError("invented disk failure")

        with patch.object(human.os, "write", write), self.assertRaises(OSError):
            human.publish_preparation(self.artifacts, self.raw)
        self.assertEqual(self.path.read_bytes(), self.raw[:3])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)


class PreparationAzureTests(unittest.TestCase):
    def test_binding_failure_or_slow_first_read_never_enters_secret_route(self):
        value = object.__new__(azure.SnapshotAzure)
        with (
            patch.object(azure, "require_capture_process"),
            patch.object(
                azure.SnapshotAzure, "_observe", return_value=SimpleNamespace(body={})
            ) as observe,
            patch.object(
                azure.SnapshotAzure,
                "_source_binding",
                side_effect=RuntimeError("INVENTED_SECRET_CANARY"),
            ),
        ):
            with self.assertRaisesRegex(
                azure.AzureReadRefused, "^snapshot_azure_read_refused$"
            ):
                value.observe_application_binding(public.IMAGE, 60)
            self.assertEqual(
                [call.args[0] for call in observe.call_args_list], ["app", "revision"]
            )
        with (
            patch.object(azure, "require_capture_process"),
            patch.object(azure.time, "monotonic", side_effect=[100, 100, 161]),
            patch.object(
                azure.SnapshotAzure, "_observe", return_value=SimpleNamespace(body={})
            ) as observe,
        ):
            with self.assertRaises(azure.AzureReadRefused):
                value.observe_application_binding(public.IMAGE, 60)
            self.assertEqual([call.args[0] for call in observe.call_args_list], ["app"])

    def test_only_fixed_app_revision_gets_and_binding_under_one_budget(self):
        value = object.__new__(azure.SnapshotAzure)
        observations, budget = [], []

        def observe(_self, name, timeout):
            observations.append(name)
            budget.append(timeout)
            return SimpleNamespace(body={"invented": name})

        with (
            patch.object(azure, "require_capture_process"),
            patch.object(azure.SnapshotAzure, "_observe", observe),
            patch.object(
                azure.SnapshotAzure, "_source_binding", return_value="8" * 64
            ) as binding,
        ):
            self.assertEqual(
                value.observe_application_binding(public.IMAGE, 60), "8" * 64
            )
        self.assertEqual(observations, ["app", "revision"])
        self.assertTrue(all(0 < seconds <= 30 for seconds in budget))
        binding.assert_called_once_with(
            {"invented": "app"}, {"invented": "revision"}, public.IMAGE
        )
        self.assertEqual(
            [azure.fixed_operation(name)[0] for name in observations], ["GET", "GET"]
        )


class PreparationMainTests(unittest.TestCase):
    def test_prepare_cannot_print_bad_hash_or_execution_authority(self):
        for changes in (
            {"observation_sha256": "INVENTED_SECRET_CANARY"},
            {"execution_authorized": True},
            {"observation_bytes": True},
            {"status": "captured_human_closure_observed"},
        ):
            result = {
                "status": "read_only_preparation_observed",
                "observation_sha256": "d" * 64,
                "observation_bytes": 123,
                "execution_authorized": False,
                "deployment_authorized": False,
            }
            result.update(changes)
            module = SimpleNamespace(
                run_human_preparation=Mock(return_value=result),
                SnapshotHumanRefused=human.SnapshotHumanRefused,
                REFUSAL_CODES=human.REFUSAL_CODES,
            )
            binding = SimpleNamespace(modules={"staging_snapshot_human": module})
            out, err = io.StringIO(), io.StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "invented",
                        "prepare",
                        "a" * 64,
                        "/private/tmp/x",
                        "b" * 64,
                        "c" * 64,
                    ],
                ),
                patch.object(boot, "bind_process", return_value=binding),
                patch.object(boot.os, "umask"),
                patch.object(sys, "stdout", out),
                patch.object(sys, "stderr", err),
            ):
                self.assertEqual(boot.main(), 2)
            self.assertEqual(out.getvalue(), "")
            self.assertNotIn("INVENTED_SECRET_CANARY", err.getvalue())

    def test_explicit_mode_only_with_entire_bootstrap_and_preparer_mocked(self):
        result = {
            "status": "read_only_preparation_observed",
            "observation_sha256": "d" * 64,
            "observation_bytes": 123,
            "execution_authorized": False,
            "deployment_authorized": False,
            "arbitrary_extra": "INVENTED_SECRET_CANARY",
        }
        preparer, capture = Mock(return_value=result), Mock()
        module = SimpleNamespace(
            run_human_preparation=preparer, run_human_snapshot=capture
        )
        binding = SimpleNamespace(modules={"staging_snapshot_human": module})
        out, err = io.StringIO(), io.StringIO()
        arguments = [
            "invented",
            "prepare",
            "a" * 64,
            "/private/tmp/invented",
            "b" * 64,
            "c" * 64,
        ]
        with (
            patch.object(sys, "argv", arguments),
            patch.object(boot, "bind_process", return_value=binding),
            patch.object(boot.os, "umask"),
            patch.object(sys, "stdout", out),
            patch.object(sys, "stderr", err),
        ):
            self.assertEqual(boot.main(), 0)
        preparer.assert_called_once_with(
            binding, Path("/private/tmp/invented"), "b" * 64, "c" * 64
        )
        capture.assert_not_called()
        self.assertIn("staging_snapshot_prepared", out.getvalue())
        self.assertNotIn("INVENTED_SECRET_CANARY", out.getvalue() + err.getvalue())

    def test_unknown_mode_never_reaches_binder(self):
        with (
            patch.object(
                sys,
                "argv",
                ["invented", "deploy", "a" * 64, "/private/tmp/x", "b" * 64, "c" * 64],
            ),
            patch.object(boot, "bind_process") as binder,
            patch.object(sys, "stderr", io.StringIO()),
        ):
            self.assertEqual(boot.main(), 2)
        binder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
