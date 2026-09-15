"""Invented future-dated evidence in isolated temporary folders; no live packet.

Actual local native ACL/descriptor publication is tested. All source/review/
identity/Terminal evidence is invented, and the reader's source/process/approval
authority is mocked. No human main, consumer, cache, network, image or Azure I/O.
"""

from __future__ import annotations

import ctypes
import errno
import importlib.util
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack, nullcontext
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
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
    "packet_public_fixtures",
    "tests/infrastructure/test_staging_snapshot_public_plan.py",
)
load(
    "packet_reader_fixtures", "tests/infrastructure/test_staging_snapshot_artifacts.py"
)
publisher = load(
    "staging_snapshot_packet_publication",
    "scripts/staging_snapshot_packet_publication.py",
)
plan = fixtures.module
art = sys.modules["staging_snapshot_artifacts"]
native = sys.modules["staging_snapshot_native_files"]
NOW = fixtures.NOW
TOKEN = "a" * 32


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(
            self.stack.enter_context(
                tempfile.TemporaryDirectory(
                    prefix="invented-snapshot-publication-", dir="/private/tmp"
                )
            )
        ).resolve()
        self.stack.enter_context(patch.object(publisher, "PACKET_PARENT", self.root))
        self.stack.enter_context(
            patch.object(plan, "PACKET_PARENT", PurePosixPath(self.root))
        )
        self.now = NOW
        self.stack.enter_context(patch.object(plan, "now_utc", lambda: self.now))
        self.f = fixtures.PublicPlanTests()
        self.f.setUp()
        self.f.observation["operator_uid"] = os.geteuid()
        self.observation = self.root / ("human-observation-" + "b" * 32)
        self.preparation_files = plan.preparation_files(
            self.f.source,
            self.f.source_review,
            self.f.workspace,
            expected_source_sha256=plan.sha(self.f.source.provenance),
            expected_source_review_sha256=plan.sha(self.f.source_review.summary),
            expected_workspace_manifest_sha256=plan.sha(self.f.workspace),
            hdiutil_sha256="6" * 64,
            diskutil_sha256="7" * 64,
            operator_uid=os.geteuid(),
            observation_root=str(self.observation),
            human_presence_confirmed_at=NOW,
        )
        self.preparation = publisher.PreparationPacket(
            self.preparation_files,
            self.f.source,
            self.f.source_review,
            self.f.workspace,
        )
        self.preparation_sha = plan.sha(
            dict(self.preparation_files)["preparation.json"]
        )
        self.handoff = self.f.handoff()
        self.handoff_review = fixtures.review(
            "handoff",
            plan.sha(self.handoff.handoff),
            self.f.bundle,
            NOW + timedelta(seconds=40),
        )
        self.now = NOW + timedelta(seconds=45)

    def preparation_publish(self, **changes):
        arguments = {
            "expected_preparation_sha256": self.preparation_sha,
            "expected_source_review_sha256": plan.sha(self.f.source_review.summary),
            "packet_token": TOKEN,
            "human_presence_confirmed_at": self.now,
        }
        arguments.update(changes)
        return publisher.publish_preparation(self.preparation, **arguments)

    def capture_publish(self, **changes):
        arguments = {
            "expected_handoff_sha256": plan.sha(self.handoff.handoff),
            "expected_handoff_review_sha256": plan.sha(self.handoff_review.summary),
            "packet_token": TOKEN,
            "human_presence_confirmed_at": self.now,
        }
        arguments.update(changes)
        return publisher.publish_capture(self.handoff, self.handoff_review, **arguments)

    def assert_files(self, result, expected):
        self.assertEqual(result.packet.stat().st_mode & 0o777, 0o700)
        self.assertEqual(set(os.listdir(result.packet)), set(expected))
        self.assertEqual(
            dict(result.file_sha256),
            {name: plan.sha(raw) for name, raw in expected.items()},
        )
        for name, raw in expected.items():
            path = result.packet / name
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
            self.assertEqual(path.stat().st_nlink, 1)
        with self.assertRaises(FrozenInstanceError):
            result.packet = self.root

    def test_preparation_native_publication_and_read_only_revalidation(self):
        result = self.preparation_publish()
        self.assert_files(result, dict(self.preparation_files))
        self.assertEqual(list(self.observation.iterdir()), [])
        self.assertIsNotNone(result.observation_directory_identity)
        self.now += timedelta(seconds=1)
        with (
            patch.object(
                publisher.os,
                "mkdir",
                side_effect=AssertionError("no mkdir on revalidation"),
            ),
            patch.object(
                publisher.os,
                "write",
                side_effect=AssertionError("no write on revalidation"),
            ),
        ):
            self.assertEqual(self.preparation_publish(revalidate=result), result)
        self.assertEqual(
            dict(result.file_sha256)["preparation.json"], self.preparation_sha
        )

    def test_capture_native_publication_and_read_only_revalidation(self):
        result = self.capture_publish()
        expected = dict(
            plan.packet_files(
                self.handoff,
                self.handoff_review,
                expected_handoff_sha256=plan.sha(self.handoff.handoff),
                expected_handoff_review_sha256=plan.sha(self.handoff_review.summary),
            )
        )
        self.assertEqual(len(expected), 16)
        self.assert_files(result, expected)
        self.assertIsNone(result.observation_directory)
        self.assertEqual(self.capture_publish(revalidate=result), result)

    def test_publication_does_not_change_the_callers_umask(self):
        with patch.object(
            publisher.os, "umask", side_effect=AssertionError("must not change umask")
        ):
            self.preparation_publish()

    def test_wrong_external_hashes_and_unsafe_names_create_nothing(self):
        for changes in (
            {"expected_preparation_sha256": "0" * 64},
            {"expected_source_review_sha256": "0" * 64},
            {"packet_token": "../unsafe"},
            {"human_presence_confirmed_at": NOW - timedelta(minutes=21)},
            {"human_presence_confirmed_at": self.now + timedelta(seconds=1)},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaises(publisher.PacketPublicationRefused),
            ):
                self.preparation_publish(**changes)
            self.assertEqual(list(self.root.iterdir()), [])

    def test_duplicate_extra_oversized_or_nonbyte_file_is_rejected_before_creation(
        self,
    ):
        original = self.preparation
        for files in (
            self.preparation_files + (("extra.json", b"{}"),),
            (self.preparation_files[0],) * 6,
            (
                ("../preparation.json", self.preparation_files[0][1]),
                *self.preparation_files[1:],
            ),
            (("preparation.json", b"x" * 16385), *self.preparation_files[1:]),
            (("preparation.json", "not bytes"), *self.preparation_files[1:]),
        ):
            self.preparation = replace(original, files=files)
            with self.assertRaises(publisher.PacketPublicationRefused):
                self.preparation_publish()
            self.assertEqual(list(self.root.iterdir()), [])

    def test_preparation_cannot_extend_original_expiry_or_claim_execution(self):
        original = self.preparation
        for changes in (
            {"expires_at": plan.encoded_time(NOW + timedelta(hours=2))},
            {"execution_authorized": True},
            {"operator_uid": os.geteuid() + 1},
        ):
            files = dict(self.preparation_files)
            content = json.loads(files["preparation.json"])
            content.update(changes)
            files["preparation.json"] = plan.canonical(content)
            self.preparation = replace(original, files=tuple(files.items()))
            with self.assertRaises(publisher.PacketPublicationRefused):
                self.preparation_publish(
                    expected_preparation_sha256=plan.sha(files["preparation.json"])
                )
            self.assertEqual(list(self.root.iterdir()), [])

    def test_expired_preparation_and_handoff_are_not_published(self):
        self.now = NOW + timedelta(minutes=31)
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.preparation_publish()
        self.now = NOW + timedelta(minutes=11)
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.capture_publish()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_preparation_revalidation_after_fifteen_minutes_does_not_renew_files(self):
        result = self.preparation_publish(human_presence_confirmed_at=NOW)
        original = {
            name: (result.packet / name).read_bytes() for name in publisher.PREPARATION
        }
        self.now = NOW + timedelta(minutes=15)
        self.assertEqual(
            self.preparation_publish(
                revalidate=result, human_presence_confirmed_at=NOW
            ),
            result,
        )
        self.assertEqual(
            original, {name: (result.packet / name).read_bytes() for name in original}
        )
        self.now = NOW + timedelta(minutes=20, seconds=1)
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.preparation_publish(revalidate=result, human_presence_confirmed_at=NOW)

    def test_capture_source_freshness_remains_ten_minutes(self):
        at = NOW + timedelta(minutes=15)
        with self.assertRaises(plan.PublicPlanRefused):
            plan.source_record(self.f.source, plan.sha(self.f.source.provenance), at)
        plan.source_record(
            self.f.source, plan.sha(self.f.source.provenance), at, preparation=True
        )

    def test_changed_plan_or_handoff_review_is_not_published(self):
        original = self.handoff
        self.handoff = replace(
            original, plan=replace(original.plan, plan=b'{"changed":true}')
        )
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.capture_publish()
        self.handoff = original
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.capture_publish(expected_handoff_review_sha256="0" * 64)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_packet_or_observation_is_never_replaced(self):
        self.observation.mkdir(mode=0o700)
        sentinel = self.observation / "invented-sentinel"
        sentinel.write_bytes(b"preserve")
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.preparation_publish()
        self.assertEqual(sentinel.read_bytes(), b"preserve")
        leftover = self.root / ("human-preparation-" + TOKEN)
        self.assertTrue(leftover.is_dir())
        self.assertEqual(list(leftover.iterdir()), [])
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.preparation_publish()
        self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_parent_symlink_and_group_write_refuse_without_repair(self):
        actual = self.root / "actual"
        actual.mkdir(mode=0o700)
        link = self.root / "linked"
        link.symlink_to(actual, target_is_directory=True)
        with (
            patch.object(publisher, "PACKET_PARENT", link),
            self.assertRaises(publisher.PacketPublicationRefused),
        ):
            self.capture_publish()
        self.assertEqual(list(actual.iterdir()), [])
        self.root.chmod(0o770)
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.capture_publish()
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o770)
        self.root.chmod(0o700)  # Restore only this invented fixture for teardown.

    def test_changed_bytes_or_identical_bytes_new_inode_refuse_revalidation(self):
        result = self.capture_publish()
        path = result.packet / "plan.json"
        raw = path.read_bytes()
        old = self.root / "preserved-invented-original"
        path.rename(old)
        path.write_bytes(raw)
        path.chmod(0o400)
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.capture_publish(revalidate=result)
        self.assertEqual(path.read_bytes(), raw)
        path.chmod(0o600)
        path.write_bytes(b"changed")
        path.chmod(0o400)
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.capture_publish(revalidate=result)

    def test_hardlink_symlink_extra_file_or_mode_change_refuses_revalidation(self):
        result = self.capture_publish()
        path = result.packet / "plan.json"
        os.link(path, self.root / "invented-hardlink")
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.capture_publish(revalidate=result)
        other = self.preparation_publish(packet_token="c" * 32)
        (other.packet / "extra").symlink_to(self.root)
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.preparation_publish(packet_token="c" * 32, revalidate=other)

    def test_observation_directory_replacement_or_added_content_refuses(self):
        result = self.preparation_publish()
        self.observation.rename(self.root / "preserved-invented-observation")
        self.observation.mkdir(mode=0o700)
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.preparation_publish(revalidate=result)
        (self.observation / "invented").write_bytes(b"fixture")
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.preparation_publish(revalidate=result)

    def test_supplied_identity_cannot_bless_writable_or_hardlinked_input(self):
        result = self.capture_publish()
        path = result.packet / "plan.json"
        path.chmod(0o600)
        identities = dict(result.file_identities)
        identities["plan.json"] = publisher.file_identity(path.stat())
        forged = replace(result, file_identities=tuple(sorted(identities.items())))
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.capture_publish(revalidate=forged)
        path.chmod(0o400)
        os.link(path, self.root / "invented-extra-link")
        identities["plan.json"] = publisher.file_identity(path.stat())
        forged = replace(result, file_identities=tuple(sorted(identities.items())))
        with self.assertRaises(publisher.PacketPublicationRefused):
            self.capture_publish(revalidate=forged)

    def test_deadline_does_not_publish_anchor_after_partial_work(self):
        original = publisher.write_file
        clock = time.monotonic()

        def write(*args, **kwargs):
            nonlocal clock
            value = original(*args, **kwargs)
            clock += 31
            self.now += timedelta(seconds=31)
            return value

        with (
            patch.object(publisher.time, "monotonic", lambda: clock),
            patch.object(publisher, "write_file", write),
        ):
            with self.assertRaises(publisher.PacketPublicationRefused):
                self.preparation_publish()
        root = self.root / ("human-preparation-" + TOKEN)
        self.assertGreater(len(list(root.iterdir())), 0)
        self.assertFalse((root / "preparation.json").exists())

    def test_partial_write_and_fsync_failure_are_preserved_without_success(self):
        original, calls = os.write, []

        def write(fd, raw):
            calls.append(fd)
            if len(calls) == 1:
                return original(fd, raw[:2])
            raise OSError("INVENTED_SECRET_CANARY")

        with (
            patch.object(publisher.os, "write", write),
            self.assertRaisesRegex(
                publisher.PacketPublicationRefused,
                "^snapshot_packet_publication_refused$",
            ),
        ):
            self.capture_publish()
        folder = self.root / ("human-snapshot-" + TOKEN)
        rows = list(folder.iterdir())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].stat().st_size, 2)
        self.assertEqual(rows[0].stat().st_mode & 0o777, 0o400)
        self.assertFalse((folder / "handoff.json").exists())
        with (
            patch.object(publisher.os, "fsync", side_effect=OSError("fixture")),
            self.assertRaises(publisher.PacketPublicationRefused),
        ):
            self.capture_publish(packet_token="d" * 32)
        self.assertTrue((self.root / ("human-snapshot-" + "d" * 32)).is_dir())

    def test_close_failure_cannot_return_success(self):
        original, calls = os.close, []

        def close(fd):
            original(fd)
            calls.append(fd)
            if len(calls) == 1:
                raise OSError("invented close failure")

        with (
            patch.object(publisher.os, "close", close),
            self.assertRaises(publisher.PacketPublicationRefused),
        ):
            self.capture_publish()
        self.assertGreater(len(calls), 1)

    def test_expiry_during_final_validation_is_not_renewed(self):
        original, calls = plan.packet_files, []

        def files(*args, **kwargs):
            calls.append(True)
            if len(calls) == 3:
                self.now = NOW + timedelta(minutes=11)
            return original(*args, **kwargs)

        with (
            patch.object(plan, "packet_files", files),
            self.assertRaises(publisher.PacketPublicationRefused),
        ):
            self.capture_publish()
        path = self.root / ("human-snapshot-" + TOKEN) / "handoff.json"
        self.assertEqual(
            path.read_bytes(), self.handoff.handoff
        )  # Preserved, NOT accepted.

    def test_reader_accepts_exact_native_published_preparation_and_capture_files(self):
        prep, capture = self.preparation_publish(), self.capture_publish()
        owner = self

        class Clock(datetime):
            @classmethod
            def fromisoformat(cls, value):
                return datetime.fromisoformat(value)

            @classmethod
            def now(cls, tz=None):
                assert tz is UTC
                return owner.now

        # Real descriptor/ACL reads; source, process and approval authority are
        # deliberately mocked. Neither human orchestration function is called.
        with (
            patch.object(native, "require_core_disabled"),
            patch.object(native, "require_capture_process"),
            patch.object(native, "defer_control_signals", nullcontext),
            patch.object(art.ReviewedArtifacts, "_types"),
            patch.object(art.ReviewedArtifacts, "_approval"),
            patch.object(art, "PACKET_PARENT", self.root),
            patch.object(art, "datetime", Clock),
            patch.object(art.time, "time", lambda: self.now.timestamp()),
        ):
            source = SimpleNamespace(
                files=native.NativeFiles(),
                revalidate=Mock(),
                loader_sha256=self.f.files["staging_snapshot_bootstrap.py"],
                loader_source=SimpleNamespace(file_identity=(1, 2, os.geteuid())),
                sources={
                    name.removesuffix(".py"): SimpleNamespace(content=raw)
                    for name, raw in self.f.source.source_files
                    if name != "staging_snapshot_bootstrap.py"
                },
            )
            read_prep = art.PreparationArtifacts(
                source,
                prep.packet,
                self.preparation_sha,
                plan.sha(self.f.source_review.summary),
            )
            read_capture = art.ReviewedArtifacts(
                source,
                capture.packet,
                plan.sha(self.handoff.handoff),
                plan.sha(self.handoff_review.summary),
            )
            self.assertEqual(read_prep.source_bundle_sha256, self.f.bundle)
            self.assertEqual(
                read_capture.inputs.plan_sha256, plan.sha(self.handoff.plan.plan)
            )
            self.assertEqual(read_prep.observation_root, self.observation)


class AlarmAndACLTests(unittest.TestCase):
    def test_pending_caller_alarm_is_refused_before_handler_or_timer_change(self):
        with (
            patch.object(publisher.signal, "sigpending", return_value={signal.SIGALRM}),
            patch.object(publisher.signal, "signal") as handler,
            patch.object(publisher.signal, "setitimer") as timer,
            self.assertRaises(publisher.PacketPublicationRefused),
        ):
            with publisher.operation():
                self.fail("pending caller alarm must refuse")
        handler.assert_not_called()
        timer.assert_not_called()

    def test_pending_teardown_alarm_never_reaches_ignored_or_sentinel_handler(self):
        original = signal.getsignal(signal.SIGALRM)
        original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        previous_calls = []

        def sentinel(*_args):
            previous_calls.append(True)

        for prior in (signal.SIG_IGN, sentinel):
            with self.subTest(prior=prior):
                real_timer = signal.setitimer
                signal.signal(signal.SIGALRM, prior)

                def timer(which, seconds, interval=0):
                    result = real_timer(which, seconds, interval)
                    if seconds == 0:
                        os.kill(os.getpid(), signal.SIGALRM)
                        self.assertIn(signal.SIGALRM, signal.sigpending())
                    return result

                returned = []
                try:
                    with (
                        patch.object(publisher.signal, "setitimer", side_effect=timer),
                        self.assertRaises(publisher.PacketPublicationRefused),
                    ):
                        with publisher.operation():
                            pass
                        returned.append("publication returned")
                    self.assertFalse(returned or previous_calls)
                    self.assertEqual(signal.getsignal(signal.SIGALRM), prior)
                    self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
                    self.assertEqual(
                        signal.pthread_sigmask(signal.SIG_BLOCK, set()), original_mask
                    )
                    self.assertNotIn(signal.SIGALRM, signal.sigpending())
                finally:
                    signal.signal(signal.SIGALRM, original)

    def test_expiry_during_handler_restoration_cannot_return_success(self):
        original = signal.getsignal(signal.SIGALRM)
        real_signal = signal.signal
        with patch.object(publisher.time, "monotonic", return_value=100.0) as clock:

            def handler(number, value):
                result = real_signal(number, value)
                if value is original:
                    clock.return_value = 130.0
                return result

            with (
                patch.object(publisher.signal, "signal", side_effect=handler),
                self.assertRaises(publisher.PacketPublicationRefused),
            ):
                with publisher.operation():
                    pass
        self.assertEqual(signal.getsignal(signal.SIGALRM), original)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_existing_alarm_is_not_stolen(self):
        with (
            patch.object(publisher.signal, "getitimer", return_value=(5.0, 0.0)),
            patch.object(publisher.signal, "setitimer") as timer,
        ):
            with (
                self.assertRaises(publisher.PacketPublicationRefused),
                publisher.operation(),
            ):
                self.fail("must not enter")
            timer.assert_not_called()

    def test_own_alarm_remains_failure_even_if_swallowed_and_handler_is_restored(self):
        old = signal.getsignal(signal.SIGALRM)
        with (
            self.assertRaises(publisher.PacketPublicationRefused),
            publisher.operation(),
        ):
            try:
                signal.raise_signal(signal.SIGALRM)
            except publisher.PacketPublicationRefused:
                pass
        self.assertEqual(signal.getsignal(signal.SIGALRM), old)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_allow_acl_denied_even_on_ancestor_and_deny_acl_only_on_ancestor(self):
        value = object.__new__(publisher.ACL)
        value.get = lambda *_: 1
        value.free = Mock(return_value=0)
        count = []

        def entry(_acl, _selector, pointer):
            count.append(True)
            if len(count) % 2:
                pointer._obj.value = 123
                return 0
            ctypes.set_errno(errno.EINVAL)
            return -1

        value.entry = entry

        def tag(_entry, pointer):
            pointer._obj.value = 1
            return 0

        value.tag = tag
        with patch.object(publisher.sys, "platform", "darwin"):
            with self.assertRaises(publisher.PacketPublicationRefused):
                value.check(123, ancestor=True)
            count.clear()

            def deny(_entry, pointer):
                pointer._obj.value = 2
                return 0

            value.tag = deny
            value.check(123, ancestor=True)
            count.clear()
            with self.assertRaises(publisher.PacketPublicationRefused):
                value.check(123)
        self.assertEqual(value.free.call_count, 3)


if __name__ == "__main__":
    unittest.main()
