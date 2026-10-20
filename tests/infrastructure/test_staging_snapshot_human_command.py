"""Invented2030 artifacts only; real local file I/O, never a human consumer.

Shell integration uses the test interpreter and a tiny invented public dummy
entrypoint, ALL18 source pins patched to those fixtures, and its own new PTY.
It never attaches to the user's Terminal or executes the snapshot bootstrap.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import pty
import select
import shlex
import signal
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

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
    "command_publication_fixtures",
    "tests/infrastructure/test_staging_snapshot_packet_publication.py",
)
module = load(
    "staging_snapshot_human_command", "scripts/staging_snapshot_human_command.py"
)
publication, plan = fixtures.publisher, fixtures.plan
NOW = fixtures.NOW


class ProductionPinTests(unittest.TestCase):
    def test_unpatched_runtime_pins_source_check_and_environment_consistency(self):
        loader = (ROOT / "scripts/staging_snapshot_bootstrap.py").read_bytes()
        self.assertEqual(plan.sha(loader), module.LOADER_SHA256)
        pins = next(
            ast.literal_eval(node.value)
            for node in ast.parse(loader).body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "SOURCE_PINS"
                for target in node.targets
            )
        )
        self.assertEqual(len(pins), 17)
        files = {"staging_snapshot_bootstrap.py": loader}
        for name, expected in pins:
            raw = (ROOT / "scripts" / (name + ".py")).read_bytes()
            self.assertEqual(plan.sha(raw), expected)
            files[name + ".py"] = raw
        self.assertEqual(set(files), module.SOURCE_NAMES)
        catalog = {name: plan.sha(raw) for name, raw in files.items()}
        self.assertEqual(plan.sha(module.canonical(catalog)), module.BUNDLE_SHA256)
        # Actual source BYTES, but invented2030 provider/review evidence: neither
        # source collection, authentication, publication nor human main executes.
        fixture = fixtures.fixtures
        source = fixture.source(NOW)
        value = json.loads(source.provenance)
        value["files"] = value["pull_requests"][0]["reviewed_files"] = catalog
        source = replace(
            source,
            provenance=fixture.encoded(value),
            source_files=tuple(sorted(files.items())),
        )
        review = fixture.review(
            "source",
            module.BUNDLE_SHA256,
            module.BUNDLE_SHA256,
            NOW - timedelta(seconds=1),
        )
        packet = module.SourcePacket(
            source, review, plan.sha(source.provenance), plan.sha(review.summary)
        )
        with patch.object(plan, "now_utc", return_value=NOW):
            self.assertEqual(module.source_check(packet), files)
        # Consolidate the old actual-source/environment case here, OUTSIDE the
        # invented CommandTests.setUp pin patching. One actual catalog and one
        # fixed production bundle now cover both checks; no historical literal.
        function = next(
            node
            for node in ast.parse(loader).body
            if isinstance(node, ast.FunctionDef) and node.name == "startup_environment"
        )
        namespace = {
            "RUNTIME_ROOT": module.RUNTIME_ROOT,
            "python_prefix": lambda: module.PYTHON_PREFIX,
            "os": os,
        }
        # Only this pure environment function, never imports or executable main.
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                "invented-environment-comparison",
                "exec",
            ),
            namespace,
        )
        self.assertEqual(
            namespace["startup_environment"](), module.startup_environment(os.geteuid())
        )


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.base = fixtures.PublicationTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.stack, self.root = self.base.stack, self.base.root
        self.f = self.base.f
        self.stack.enter_context(patch.object(module, "BUNDLE_SHA256", self.f.bundle))
        self.loader_sha = plan.sha(
            dict(self.f.source.source_files)["staging_snapshot_bootstrap.py"]
        )
        self.stack.enter_context(patch.object(module, "LOADER_SHA256", self.loader_sha))
        self.source = module.SourcePacket(
            self.f.source,
            self.f.source_review,
            plan.sha(self.f.source.provenance),
            plan.sha(self.f.source_review.summary),
        )

    def publish_source(self, **changes):
        return module.publish_source(self.source, source_token="c" * 32, **changes)

    def preparation(self):
        self.sources = self.publish_source()
        self.packet = self.base.preparation_publish()
        self.arguments = {
            "expected_preparation_sha256": self.base.preparation_sha,
            "expected_source_review_sha256": plan.sha(self.f.source_review.summary),
            "human_presence_confirmed_at": self.base.now,
        }

    def preparation_command(self, **changes):
        arguments = {**self.arguments, **changes}
        return module.preparation_command(
            self.source,
            self.sources,
            self.base.preparation,
            self.packet,
            **arguments,
        )

    def capture(self):
        self.source = replace(
            self.source,
            source=self.base.handoff.fresh_source,
            expected_source_sha256=plan.sha(self.base.handoff.fresh_source.provenance),
        )
        self.sources = self.publish_source()
        self.packet = self.base.capture_publish()
        self.arguments = {
            "expected_handoff_sha256": plan.sha(self.base.handoff.handoff),
            "expected_handoff_review_sha256": plan.sha(
                self.base.handoff_review.summary
            ),
            "human_presence_confirmed_at": self.base.now,
        }

    def capture_command(self, **changes):
        return module.capture_command(
            self.source,
            self.sources,
            self.base.handoff,
            self.base.handoff_review,
            self.packet,
            **{**self.arguments, **changes},
        )

    def test_source_exclusive_native_18_files_bootstrap_last_no_presence_or_execution_packet(
        self,
    ):
        written = []
        real = publication.write_file

        def write(parent, name, *args):
            written.append(name)
            return real(parent, name, *args)

        with (
            patch.object(
                publication, "presence", side_effect=AssertionError("no fake presence")
            ),
            patch.object(publication, "write_file", write),
        ):
            value = self.publish_source()
        self.assertEqual(written[-1], "staging_snapshot_bootstrap.py")
        self.assertEqual(len(written), 18)
        self.base.assert_files(value, dict(self.f.source.source_files))
        self.assertIsNone(value.observation_directory)
        self.assertEqual(list(self.root.iterdir()), [value.packet])
        with self.assertRaises(module.HumanCommandRefused):
            self.publish_source()
        self.assertEqual(self.publish_source(revalidate=value), value)

    def test_source_wrong_pins_unreviewed_open_or_changed_coverage_never_creates_directory(
        self,
    ):
        original = self.source
        bad_provenance = copy.deepcopy(plan.record(self.source.source.provenance))
        bad_provenance["pull_requests"][0]["state"] = "OPEN"
        bad = replace(self.source.source, provenance=plan.canonical(bad_provenance))
        cases = [
            replace(original, expected_source_sha256="0" * 64),
            replace(original, expected_review_sha256="0" * 64),
            replace(
                original, source=bad, expected_source_sha256=plan.sha(bad.provenance)
            ),
            replace(
                original,
                source=replace(
                    original.source, source_files=original.source.source_files[:-1]
                ),
            ),
            replace(original, review=replace(original.review, text=b"FAIL")),
        ]
        for value in cases:
            self.source = value
            with (
                self.subTest(value=value.expected_source_sha256),
                self.assertRaises(module.HumanCommandRefused),
            ):
                self.publish_source()
            self.assertEqual(list(self.root.iterdir()), [])
        self.source = original
        for constant in ("BUNDLE_SHA256", "LOADER_SHA256"):
            with (
                patch.object(module, constant, "0" * 64),
                self.assertRaises(module.HumanCommandRefused),
            ):
                self.publish_source()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_source_staleness_at_entry_and_after_files_is_not_renewed(self):
        self.base.now = NOW + timedelta(seconds=1801)
        with self.assertRaises(module.HumanCommandRefused):
            self.publish_source()
        self.assertEqual(list(self.root.iterdir()), [])
        self.base.now = NOW + timedelta(seconds=45)
        real = publication.write_file

        def advance(parent, name, *args):
            result = real(parent, name, *args)
            if name == "staging_snapshot_bootstrap.py":
                self.base.now = NOW + timedelta(seconds=1801)
            return result

        with (
            patch.object(publication, "write_file", advance),
            self.assertRaises(module.HumanCommandRefused),
        ):
            self.publish_source()
        self.assertTrue(
            (
                self.root
                / ("human-source-" + "c" * 32)
                / "staging_snapshot_bootstrap.py"
            ).exists()
        )

    def test_source_revalidation_rejects_modes_links_and_forged_identity(self):
        value = self.publish_source()
        target = value.packet / "staging_snapshot_bootstrap.py"
        target.chmod(0o600)
        identities = dict(value.file_identities)
        identities[target.name] = publication.file_identity(target.stat())
        with self.assertRaises(module.HumanCommandRefused):
            self.publish_source(
                revalidate=replace(
                    value, file_identities=tuple(sorted(identities.items()))
                )
            )

    def test_execution_packets_cannot_use_source_presence_exception(self):
        for kind in ("preparation", "snapshot"):
            with self.assertRaises(publication.PacketPublicationRefused):
                publication.publish(lambda: {}, kind, "d" * 32, None)
        with self.assertRaises(publication.PacketPublicationRefused):
            publication.publish(lambda: {}, "source", "d" * 32, NOW)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_preparation_renderer_is_read_only_stable_and_preserves_shell_stdin(self):
        self.preparation()
        with (
            patch.object(
                publication.os, "mkdir", side_effect=AssertionError("no mkdir")
            ),
            patch.object(
                publication.os, "write", side_effect=AssertionError("no write")
            ),
            patch.object(subprocess, "Popen", side_effect=AssertionError("no child")),
        ):
            first = self.preparation_command()
            self.base.now += timedelta(seconds=1)
            self.assertEqual(self.preparation_command(), first)
        self.assertEqual(plan.sha(first.command), first.command_sha256)
        self.assertTrue(first.independent_exact_command_review_required)
        self.assertEqual(
            first.not_after, plan.encoded_time(NOW + timedelta(seconds=1245))
        )
        argv = shlex.split(first.command.decode())
        self.assertEqual(
            argv[:7],
            [
                "/usr/bin/env",
                "-i",
                "PATH=/usr/bin:/bin",
                "LANG=C",
                "LC_ALL=C",
                "/bin/sh",
                "-c",
            ],
        )
        script = argv[7]
        self.assertNotIn("<<", script)
        self.assertNotIn("|", script.split("exec ")[-1])
        self.assertEqual(script.count("\ncheck_file "), 25)  # interpreter+18+6
        last = shlex.split(script.splitlines()[-1])
        self.assertEqual(
            last[-5:],
            [
                "prepare",
                self.loader_sha,
                str(self.packet.packet),
                self.base.preparation_sha,
                self.arguments["expected_source_review_sha256"],
            ],
        )
        self.assertIn(module.PYTHON_SHA256, script)
        syntax = subprocess.run(
            ["/bin/sh", "-n", "-c", script], capture_output=True, timeout=5
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_capture_renderer_requires_both_exact_reviews_and_all16_files(self):
        self.capture()
        draft = self.capture_command()
        script = shlex.split(draft.command.decode())[-1]
        self.assertEqual(script.count("\ncheck_file "), 35)
        self.assertNotIn(" prepare ", script.splitlines()[-1])
        self.assertEqual(
            draft.not_after, plan.encoded_time(NOW + timedelta(seconds=1170))
        )
        original = self.base.handoff
        self.base.handoff = replace(
            original, plan_review=replace(original.plan_review, text=b"FAIL")
        )
        with self.assertRaises(module.HumanCommandRefused):
            self.capture_command()
        self.base.handoff = original
        self.base.handoff_review = replace(self.base.handoff_review, text=b"FAIL")
        with self.assertRaises(module.HumanCommandRefused):
            self.capture_command()

    def test_rendering_refuses_wrong_external_anchors_without_output(self):
        self.preparation()
        for changes in (
            {"expected_preparation_sha256": "0" * 64},
            {"expected_source_review_sha256": "0" * 64},
            {"human_presence_confirmed_at": NOW - timedelta(minutes=21)},
            {"human_presence_confirmed_at": self.base.now + timedelta(seconds=1)},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaises(module.HumanCommandRefused),
            ):
                self.preparation_command(**changes)

    def test_no_late_or_expired_draft_even_with_new_presence(self):
        self.preparation()
        self.base.now = NOW + timedelta(seconds=1215)
        with self.assertRaises(module.HumanCommandRefused):
            self.preparation_command()
        self.base.now = NOW + timedelta(minutes=31)
        with self.assertRaises(module.HumanCommandRefused):
            self.preparation_command(human_presence_confirmed_at=self.base.now)

    def test_actual_new_presence_cannot_rewrite_or_extend_original_artifacts(self):
        self.preparation()
        original = self.preparation_command()
        original_files = dict(self.base.preparation.files)
        self.base.now = NOW + timedelta(minutes=6)
        newer = self.preparation_command(human_presence_confirmed_at=self.base.now)
        self.assertEqual(dict(self.base.preparation.files), original_files)
        self.assertEqual(
            newer.not_after, plan.encoded_time(NOW + timedelta(minutes=26))
        )
        self.assertNotEqual(newer.command_sha256, original.command_sha256)
        self.assertTrue(newer.independent_exact_command_review_required)

    def test_preparation_review_delay_preserves_exact_command_for_twenty_minutes(self):
        self.preparation()
        original = self.preparation_command()
        original_files = dict(self.base.preparation.files)
        self.base.now = NOW + timedelta(minutes=15)
        self.assertEqual(self.preparation_command(), original)
        self.assertEqual(dict(self.base.preparation.files), original_files)
        self.base.now = NOW + timedelta(minutes=21)
        with self.assertRaises(module.HumanCommandRefused):
            self.preparation_command()

    def test_longer_preparation_source_window_cannot_publish_new_source(self):
        self.base.now = NOW + timedelta(minutes=15)
        with self.assertRaises(module.HumanCommandRefused):
            self.publish_source(preparation=True)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_capture_review_delay_preserves_command_and_presence_expires(self):
        self.capture()
        original = self.capture_command()
        self.base.now = NOW + timedelta(minutes=15)
        self.assertEqual(self.capture_command(), original)
        self.base.now = NOW + timedelta(minutes=21)
        with self.assertRaises(module.HumanCommandRefused):
            self.capture_command()

    def test_plan_change_or_token_expiry_cannot_get_capture_command(self):
        self.capture()
        original = self.base.handoff
        self.base.handoff = replace(
            original, plan=replace(original.plan, plan=original.plan.plan + b" ")
        )
        with self.assertRaises(module.HumanCommandRefused):
            self.capture_command()
        self.base.handoff = original
        self.base.now = NOW + timedelta(hours=1)
        with self.assertRaises(module.HumanCommandRefused):
            self.capture_command(human_presence_confirmed_at=self.base.now)

    def test_mismatched_source_collection_or_namespace_is_refused(self):
        self.capture()
        # Same18 bytes, older source observation: cannot mismatch packet's fresh evidence.
        self.source = replace(
            self.source,
            source=self.f.source,
            expected_source_sha256=plan.sha(self.f.source.provenance),
        )
        with self.assertRaises(module.HumanCommandRefused):
            self.capture_command()
        self.source = replace(
            self.source,
            source=self.base.handoff.fresh_source,
            expected_source_sha256=plan.sha(self.base.handoff.fresh_source.provenance),
        )
        self.sources = replace(self.sources, packet=self.root / "not-a-source")
        with self.assertRaises(module.HumanCommandRefused):
            self.capture_command()

    def test_file_change_between_publication_and_rendering_refuses(self):
        self.preparation()
        target = self.packet.packet / "preparation.json"
        target.chmod(0o600)
        target.write_bytes(b"changed invented artifact")
        target.chmod(0o400)
        with self.assertRaises(module.HumanCommandRefused):
            self.preparation_command()

    def test_source_provenance_must_match_packet_bytes_not_only_decoded_json(self):
        self.preparation()
        changed = replace(
            self.source.source, provenance=self.source.source.provenance + b" "
        )
        self.source = replace(
            self.source,
            source=changed,
            expected_source_sha256=plan.sha(changed.provenance),
        )
        with self.assertRaises(module.HumanCommandRefused):
            self.preparation_command()


class ShellFixtureTests(unittest.TestCase):
    """Only invented dummy source; real OS shell, private new PTY, test interpreter."""

    def setUp(self):
        self.base = CommandTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.root, self.stack = self.base.root, self.base.stack
        self.interpreter = Path(sys.executable).resolve()
        self.interpreter_sha = plan.sha(self.interpreter.read_bytes())
        # A private copy makes the required0500 policy true without chmod of any
        # existing interpreter. The fixture exec still uses its real install prefix.
        # Keep bin/ beside lib/ so relocatable macOS Python can find its dylib.
        (self.root / "bin").mkdir(mode=0o700)
        self.python = self.root / "bin" / "invented-python"
        self.python.write_bytes(self.interpreter.read_bytes())
        self.python.chmod(0o500)
        # Give this copied TEST interpreter its installed standard library;
        # isolated mode correctly ignores PYTHONHOME/PYTHONPATH. No copy of or
        # modification to that existing library, and no snapshot runtime used.
        (self.root / "lib").symlink_to(
            Path(sys.base_prefix) / "lib", target_is_directory=True
        )
        self.stack.enter_context(patch.object(module, "PYTHON", self.python))
        self.stack.enter_context(
            patch.object(module, "PYTHON_SHA256", self.interpreter_sha)
        )
        self.stack.enter_context(
            patch.object(module, "PYTHON_BYTES", self.python.stat().st_size)
        )
        self.sources = self.root / "invented-source"
        self.sources.mkdir(mode=0o700)
        self.packet = self.root / "invented-packet"
        self.packet.mkdir(mode=0o700)
        self.raw = b'import json,os,sys\nprint("DUMMY_READY",flush=True)\nanswer=sys.stdin.readline().strip()\nprint("DUMMY_RESULT:"+json.dumps({"answer":answer,"tty":[os.isatty(i) for i in (0,1,2)],"env":dict(os.environ),"args":sys.argv[1:]}),flush=True)\n'
        self.files = {"staging_snapshot_bootstrap.py": self.raw}
        (self.sources / "staging_snapshot_bootstrap.py").write_bytes(self.raw)
        (self.sources / "staging_snapshot_bootstrap.py").chmod(0o400)
        self.packet_files = {"invented.json": b'{"invented":true}'}
        (self.packet / "invented.json").write_bytes(self.packet_files["invented.json"])
        (self.packet / "invented.json").chmod(0o400)
        self.stack.enter_context(
            patch.object(module, "LOADER_SHA256", plan.sha(self.raw))
        )
        # Intentionally NOT the actual snapshot runtime environment; avoid loading
        # its private native libraries in this dummy test Python interpreter.
        self.environment = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "__CF_USER_TEXT_ENCODING": f"0x{os.geteuid():X}:0x0:0x0",
        }
        self.stack.enter_context(
            patch.object(
                module, "startup_environment", lambda uid: dict(self.environment)
            )
        )

    def command(self, **changes):
        now = int(datetime.now(UTC).timestamp())
        arguments = {
            "source_path": self.sources,
            "packet_path": self.packet,
            "sources": self.files,
            "files": self.packet_files,
            "artifact_sha": "a" * 64,
            "review_sha": "b" * 64,
            "preparing": True,
            "uid": os.geteuid(),
            "not_before": now - 2,
            "not_after": now + 60,
        }
        command = module.shell_text(**{**arguments, **changes}).decode()
        if sys.platform == "linux":
            # The production launcher targets macOS. Exercise the same real
            # file metadata checks with GNU stat in the Linux dummy fixture.
            bsd_stat = '/usr/bin/stat -f "%z:%Lp:%u:%l"'
            self.assertEqual(command.count(bsd_stat), 1)
            command = command.replace(bsd_stat, '/usr/bin/stat -c "%s:%a:%u:%h"')
        return shlex.split(command)

    def run_dummy(self, command):
        master, slave = pty.openpty()
        process = None
        output = bytearray()
        try:
            process = subprocess.Popen(
                command,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                env={
                    **os.environ,
                    "PYTHONPATH": "/invented/poison",
                    "PERL5OPT": "-eFAIL",
                    "BASH_ENV": "/invented/poison",
                    "ENV": "/invented/poison",
                },
            )
            os.close(slave)
            slave = -1
            deadline, sent = time.monotonic() + 10, False
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        block = os.read(master, 65536)
                    except OSError:
                        break
                    if not block:
                        break
                    output.extend(block)
                    if b"DUMMY_READY" in output and not sent:
                        os.write(master, b"invented-terminal-answer\n")
                        sent = True
                if process.poll() is not None:
                    break
            code = process.wait(timeout=1)
            return code, output.decode()
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            if slave >= 0:
                os.close(slave)
            os.close(master)

    def test_real_shell_checks_dummy_pins_clears_environment_and_preserves_all_tty_fds(
        self,
    ):
        code, output = self.run_dummy(self.command())
        self.assertEqual(code, 0, output)
        result = json.loads(output.split("DUMMY_RESULT:", 1)[1].splitlines()[0])
        self.assertEqual(result["answer"], "invented-terminal-answer")
        self.assertEqual(result["tty"], [True, True, True])
        self.assertEqual(result["env"], self.environment)
        self.assertEqual(
            result["args"],
            ["prepare", plan.sha(self.raw), str(self.packet), "a" * 64, "b" * 64],
        )

    def test_hash_mismatch_refuses_before_dummy_entrypoint(self):
        with patch.object(module, "PYTHON_SHA256", "0" * 64):
            code, output = self.run_dummy(self.command())
        self.assertEqual(code, 2, output)
        self.assertIn("staging_snapshot_launch_refused", output)
        self.assertNotIn("DUMMY_READY", output)

    def test_source_artifact_mode_and_operator_mismatch_refuse_before_dummy(self):
        for changes in (
            {"sources": {"staging_snapshot_bootstrap.py": b"x" * len(self.raw)}},
            {
                "files": {
                    "invented.json": b"x" * len(self.packet_files["invented.json"])
                }
            },
            {"uid": os.geteuid() + 1},
        ):
            with self.subTest(changes=tuple(changes)):
                code, output = self.run_dummy(self.command(**changes))
                self.assertEqual(code, 2, output)
                self.assertNotIn("DUMMY_READY", output)
        (self.packet / "invented.json").chmod(0o600)
        code, output = self.run_dummy(self.command())
        self.assertEqual(code, 2, output)
        self.assertNotIn("DUMMY_READY", output)

    def test_expired_and_future_clock_commands_refuse_before_dummy(self):
        now = int(datetime.now(UTC).timestamp())
        for changes in ({"not_after": now - 1}, {"not_before": now + 60}):
            code, output = self.run_dummy(self.command(**changes))
            self.assertEqual(code, 2, output)
            self.assertNotIn("DUMMY_READY", output)

    def test_nonterminal_invocation_refuses_without_dummy_execution(self):
        result = subprocess.run(
            self.command(), input=b"invented\n", capture_output=True, timeout=5
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"DUMMY_READY", result.stdout)


if __name__ == "__main__":
    unittest.main()
