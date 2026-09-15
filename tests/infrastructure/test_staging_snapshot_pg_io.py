"""Secretless adapter tests; no PostgreSQL, Azure or native encryption calls."""

import functools
import hashlib
import importlib.util
import json
import locale
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "staging_snapshot_pg_io",
    Path(__file__).resolve().parents[2] / "scripts/staging_snapshot_pg_io.py",
)
assert SPEC is not None and SPEC.loader is not None
pg = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pg
SPEC.loader.exec_module(pg)
SOURCE = (
    f"postgresql://fixture-user:fixture-password@{pg.HOST}:5432/staging?sslmode=require"
)
SCHEMA = json.dumps(
    {
        "database": "staging",
        "major": 16,
        "readonly": True,
        "tls": True,
        "revisions": [pg.REVISION],
    }
).encode()
ARCHIVED_REVISION = b"-- Invented fixture\nCOPY public.alembic_version (version_num) FROM stdin;\n89c31f0ae275\n\\.\n"
FAKE_DUMP = b"PGDMP" + b"invented bytes, not a real database archive" * 5


def dedicated_process_tests(cls):
    """Never freeze pytest's shared process or exercise signals with its threads."""
    for name, method in list(vars(cls).items()):
        if not name.startswith("test_"):
            continue

        @functools.wraps(method)
        def run(self, _method=method, _name=name):
            if pg._capture_process_pid == os.getpid():
                return _method(self)
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    __file__,
                    cls.__name__ + "." + _name,
                ],
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())

        setattr(cls, name, run)
    return cls


class FixtureChildren:
    def __init__(self):
        self.deadline = time.monotonic() + 200
        self.calls = []
        self.dump = FAKE_DUMP
        self.revision = ARCHIVED_REVISION
        self.schema = SCHEMA
        self.failure: str | None = None

    def remaining(self):
        return 200.0

    def run(self, argv, environment, max_bytes, sink, *, pass_fds=()):
        self.calls.append((argv, environment, max_bytes, pass_fds))
        tool = Path(argv[0]).name
        if tool == self.failure:
            raise pg.PostgreSQLRefused("postgresql_command_failed")
        if tool == "pg_dump":
            output = self.dump
        elif tool == "psql":
            output = self.schema
        elif "--data-only" in argv:
            output = self.revision
        else:
            output = b""
        if len(output) > max_bytes:
            raise pg.PostgreSQLRefused("child_output_limit")
        if output:
            # Fragment the signature to exercise prefix accumulation.
            sink(output[:2])
            sink(output[2:])
        return len(output)


@dedicated_process_tests
class ContractTests(unittest.TestCase):
    def test_source_url_is_fixed_and_secrets_do_not_appear_in_repr(self):
        credentials = pg.credentials_from_exact_source_dsn(SOURCE)
        self.assertNotIn("fixture", repr(credentials))
        self.assertEqual(credentials.username, "fixture-user")

    def test_wrong_targets_and_dsn_overrides_refused(self):
        invalid = [
            SOURCE.replace(pg.HOST, "production.invalid"),
            SOURCE.replace(":5432/", ":5433/"),
            SOURCE.replace("/staging?", "/postgres?"),
            SOURCE + "&options=-c%20transaction_read_only=off",
            SOURCE + "&sslmode=require",
            SOURCE + "#fragment",
            SOURCE.replace("fixture-password", "bad%00value"),
            SOURCE.replace("fixture-user", "bad%0avalue"),
            SOURCE.replace("sslmode=require", "sslmode=disable"),
            SOURCE.replace("fixture-password", ""),
        ]
        for value in invalid:
            with (
                self.subTest(value=invalid.index(value)),
                self.assertRaises(pg.PostgreSQLRefused),
            ):
                pg.credentials_from_exact_source_dsn(value)

    def test_environment_ignores_ambient_source_and_requires_verified_tls(self):
        with (
            patch.dict(
                os.environ,
                {
                    "PGHOST": "production.invalid",
                    "DYLD_INSERT_LIBRARIES": "bad",
                    "PGSERVICE": "bad",
                },
            ),
            patch.object(pg.os.path, "lexists", return_value=False),
        ):
            environment = pg.connection_environment(
                pg.Credentials("invented", "synthetic")
            )
        self.assertEqual(environment["PGHOST"], pg.HOST)
        self.assertEqual(environment["PGSSLMODE"], "verify-full")
        self.assertEqual(environment["PGSSLROOTCERT"], pg.CA_FILE)
        self.assertEqual(environment["PGPASSFILE"], "/dev/null")
        self.assertIn("default_transaction_read_only=on", environment["PGOPTIONS"])
        self.assertEqual(environment["HOME"], "/dev/null")
        self.assertEqual(environment["OPENSSL_CONF"], "/dev/null")
        self.assertEqual(environment["OPENSSL_MODULES"], "/dev/null")
        self.assertEqual(environment["PGREQUIREAUTH"], "password,md5,scram-sha-256")
        self.assertEqual(environment["PGSSLCERTMODE"], "disable")
        self.assertEqual(environment["PGSYSCONFDIR"], "/dev/null")
        self.assertEqual(environment["DYLD_LIBRARY_PATH"], str(pg.CLIENT_LIBRARIES))
        for name in ("PGSERVICE", "DYLD_INSERT_LIBRARIES", "HTTPS_PROXY"):
            self.assertNotIn(name, environment)

    def test_local_client_environment_has_no_credentials_or_user_config(self):
        with patch.dict(
            os.environ,
            {
                "PGPASSWORD": "invented",
                "OPENSSL_CONF": "/unbound/config",
                "KRB5_CONFIG": "/unbound/kerberos",
                "KRB5CCNAME": "unbound-cache",
                "DYLD_LIBRARY_PATH": "/unbound/libraries",
            },
        ):
            environment = pg.client_environment()
        self.assertEqual(environment["HOME"], "/dev/null")
        self.assertEqual(environment["OPENSSL_CONF"], "/dev/null")
        self.assertEqual(environment["OPENSSL_MODULES"], "/dev/null")
        self.assertEqual(environment["KRB5_CONFIG"], "/dev/null")
        self.assertEqual(environment["KRB5CCNAME"], "FILE:/dev/null")
        self.assertEqual(environment["KRB5_CLIENT_KTNAME"], "FILE:/dev/null")
        self.assertEqual(environment["DYLD_LIBRARY_PATH"], str(pg.CLIENT_LIBRARIES))
        self.assertFalse(any(name.startswith("PG") for name in environment))

    def test_default_tls_material_is_refused_not_read_or_deleted(self):
        with (
            patch.object(pg.os.path, "lexists", return_value=True),
            self.assertRaisesRegex(
                pg.PostgreSQLRefused, "ambient_postgresql_tls_configuration"
            ),
        ):
            pg.connection_environment(pg.Credentials("invented", "synthetic"))

    def test_direct_invalid_credential_objects_refused(self):
        for value in (pg.Credentials("", "x"), pg.Credentials("a", "bad\x00password")):
            with self.assertRaises(pg.PostgreSQLRefused):
                pg.connection_environment(value)

    def test_schema_requires_exact_revision_readonly_tls_and_pg16(self):
        pg.schema_observation(SCHEMA)
        original = json.loads(SCHEMA)
        for key, replacement in (
            ("tls", False),
            ("readonly", False),
            ("major", 17),
            ("major", True),
            ("database", "other"),
            ("revisions", []),
            ("revisions", [pg.REVISION, "other"]),
        ):
            with self.subTest(key=key), self.assertRaises(pg.PostgreSQLRefused):
                pg.schema_observation(
                    json.dumps({**original, key: replacement}).encode()
                )

    def test_archived_revision_must_be_one_exact_row_in_one_section(self):
        pg.archive_revision(ARCHIVED_REVISION)
        for value in (
            b"",
            ARCHIVED_REVISION * 2,
            ARCHIVED_REVISION.replace(b"89c31f0ae275", b"other"),
            ARCHIVED_REVISION.replace(b"\\.\n", b"other\n\\.\n"),
            ARCHIVED_REVISION.replace(
                b"public.alembic_version", b"private.alembic_version"
            ),
        ):
            with self.assertRaises(pg.PostgreSQLRefused):
                pg.archive_revision(value)


@dedicated_process_tests
class PayloadTests(unittest.TestCase):
    def setUp(self):
        # Only invented non-sensitive bytes touch this ordinary temporary
        # directory. It is explicitly NOT an encrypted-workspace test.
        self.temporary = tempfile.TemporaryDirectory(prefix="cosmaneura-pg-io-test-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        info = os.fstat(self.directory_fd)
        self.binding = pg.PayloadBinding(self.directory_fd, info.st_dev, info.st_ino)
        self.children = FixtureChildren()
        self.native_verifications = 0
        self.no_config = patch.object(pg.os.path, "lexists", return_value=False)
        self.no_config.start()

    def tearDown(self):
        self.no_config.stop()
        os.close(self.directory_fd)
        self.temporary.cleanup()

    def verify_fixture_binding(self):
        self.native_verifications += 1

    def capture(self):
        return pg.capture_with_verified_payload(
            pg.Credentials("invented", "synthetic"),
            self.binding,
            self.children,
            self.verify_fixture_binding,
        )

    def test_exclusive_no_follow_preserves_existing_artifact(self):
        (self.root / pg.ARCHIVE_NAME).write_bytes(b"invented existing")
        with self.assertRaises(pg.PostgreSQLRefused):
            pg.open_exclusive_archive(self.binding)
        self.assertEqual(
            (self.root / pg.ARCHIVE_NAME).read_bytes(), b"invented existing"
        )

    def test_symlink_payload_entry_is_not_followed(self):
        (self.root / pg.ARCHIVE_NAME).symlink_to("does-not-exist")
        with self.assertRaises(pg.PostgreSQLRefused):
            pg.open_exclusive_archive(self.binding)
        self.assertFalse((self.root / "does-not-exist").exists())

    def test_directory_device_inode_and_permission_mismatches_refused(self):
        for binding in (
            replace(self.binding, device=self.binding.device + 1),
            replace(self.binding, inode=self.binding.inode + 1),
        ):
            with self.assertRaises(pg.PostgreSQLRefused):
                pg.open_exclusive_archive(binding)
        self.root.chmod(0o755)
        with self.assertRaises(pg.PostgreSQLRefused):
            pg.open_exclusive_archive(self.binding)

    def test_fake_capture_validates_archive_without_restore_connection(self):
        result = self.capture()
        self.assertEqual(result.size, len(FAKE_DUMP))
        self.assertEqual(result.sha256, hashlib.sha256(FAKE_DUMP).hexdigest())
        self.assertEqual(self.native_verifications, 2)
        self.assertEqual(
            stat.S_IMODE((self.root / pg.ARCHIVE_NAME).stat().st_mode), 0o400
        )
        self.assertEqual(
            [Path(call[0][0]).name for call in self.children.calls],
            ["psql", "pg_dump", "pg_restore", "pg_restore", "psql"],
        )
        for argv, environment, _bound, descriptors in self.children.calls:
            self.assertNotIn("synthetic", " ".join(argv))
            if Path(argv[0]).name == "pg_restore":
                self.assertNotIn("PGPASSWORD", environment)
                self.assertFalse(
                    any(argument.startswith("--dbname") for argument in argv)
                )
                self.assertTrue(descriptors)
                self.assertTrue(argv[-1].startswith("/dev/fd/"))

    def test_incomplete_archive_is_preserved_but_never_accepted(self):
        self.children.dump = b"not a custom archive"
        with self.assertRaisesRegex(pg.PostgreSQLRefused, "archive_custom_format"):
            self.capture()
        self.assertEqual((self.root / pg.ARCHIVE_NAME).read_bytes(), self.children.dump)

    def test_invalid_archived_revision_is_not_live_observation_success(self):
        self.children.revision = ARCHIVED_REVISION.replace(b"89c31f0ae275", b"wrong")
        with self.assertRaisesRegex(pg.PostgreSQLRefused, "archived_revision_contract"):
            self.capture()
        self.assertTrue((self.root / pg.ARCHIVE_NAME).exists())

    def test_failed_dump_preserves_partial_file_without_restore(self):
        self.children.failure = "pg_dump"
        with self.assertRaises(pg.PostgreSQLRefused):
            self.capture()
        self.assertEqual((self.root / pg.ARCHIVE_NAME).read_bytes(), b"")
        self.assertEqual(len(self.children.calls), 2)

    def test_final_binding_replacement_refuses_success(self):
        def change():
            self.native_verifications += 1
            if self.native_verifications == 2:
                (self.root / pg.ARCHIVE_NAME).rename(self.root / "invented-partial")
                (self.root / pg.ARCHIVE_NAME).write_bytes(FAKE_DUMP)

        with self.assertRaisesRegex(pg.PostgreSQLRefused, "archive_descriptor_changed"):
            pg.capture_with_verified_payload(
                pg.Credentials("invented", "synthetic"),
                self.binding,
                self.children,
                change,
            )

    def test_same_size_content_change_is_detected_by_descriptor_rehash(self):
        def change():
            self.native_verifications += 1
            if self.native_verifications == 2:
                (self.root / pg.ARCHIVE_NAME).write_bytes(b"X" * len(FAKE_DUMP))

        with self.assertRaisesRegex(
            pg.PostgreSQLRefused, "archive_changed_during_validation"
        ):
            pg.capture_with_verified_payload(
                pg.Credentials("invented", "synthetic"),
                self.binding,
                self.children,
                change,
            )

    def test_hardlinked_output_refused(self):
        fd = pg.open_exclusive_archive(self.binding)
        try:
            os.link(self.root / pg.ARCHIVE_NAME, self.root / "second")
            with self.assertRaises(pg.PostgreSQLRefused):
                pg.check_archive_binding(self.binding, fd, 0)
        finally:
            os.close(fd)


@unittest.skipUnless(
    hasattr(os, "waitid") and hasattr(os, "WNOWAIT"), "requires POSIX non-reaping wait"
)
@dedicated_process_tests
class ProcessTests(unittest.TestCase):
    def run_invented_child(self, source, *, limit=4096, seconds: float = 2):
        children = pg.BoundedChildren(time.monotonic() + seconds, lambda: None)
        output = bytearray()
        try:
            # These children receive no database credentials, connection options
            # or live tools. Mock the required launcher core-limit precondition.
            with patch.object(pg.resource, "getrlimit", return_value=(0, 0)):
                count = children.run(
                    [sys.executable, "-I", "-S", "-c", source],
                    {"PATH": "/usr/bin:/bin"},
                    limit,
                    output.extend,
                )
            self.assertIsNone(children.child)
            return count, bytes(output)
        finally:
            children.cancel_and_reap()

    def test_real_invented_child_is_streamed_and_reaped(self):
        self.assertEqual(
            self.run_invented_child("import os; os.write(1, b'invented')"),
            (8, b"invented"),
        )

    def test_output_limit_kills_and_reaps_without_more_writes(self):
        with self.assertRaisesRegex(pg.PostgreSQLRefused, "child_output_limit"):
            self.run_invented_child("import os; os.write(1, b'x' * 65536)", limit=10)

    def test_deadline_kills_and_reaps_silent_child(self):
        with self.assertRaisesRegex(pg.PostgreSQLRefused, "capture_deadline"):
            self.run_invented_child("import time; time.sleep(10)", seconds=0.1)

    def test_nonzero_child_is_not_complete_archive_success(self):
        with self.assertRaisesRegex(pg.PostgreSQLRefused, "postgresql_command_failed"):
            self.run_invented_child("raise SystemExit(2)")

    def test_descendant_in_original_group_cannot_survive_leader(self):
        source = "import os,time; pid=os.fork(); os.write(1,str(os.getpid()).encode()) if pid == 0 else None; os.close(1); time.sleep(10) if pid == 0 else None"
        # On some kernels the killed grandchild remains a zombie until its
        # adopter reaps it. Either confirmed absence or a fail-closed group
        # refusal is acceptable; never a claim of containment without readback.
        try:
            _count, output = self.run_invented_child(source)
        except pg.PostgreSQLRefused as error:
            self.assertEqual(str(error), "capture_group_not_gone")
        else:
            with self.assertRaises(ProcessLookupError):
                os.kill(int(output), 0)

    def test_live_coredump_precondition_checked_before_spawn(self):
        children = pg.BoundedChildren(time.monotonic() + 1, lambda: None)
        with (
            patch.object(pg.resource, "getrlimit", return_value=(1, 1)),
            patch.object(pg.subprocess, "Popen") as spawn,
            self.assertRaisesRegex(pg.PostgreSQLRefused, "core_dumps_not_disabled"),
        ):
            children.run(["not-executed"], {}, 1, lambda _: None)
        spawn.assert_not_called()

    def test_cleanup_cannot_extend_successful_command_deadline(self):
        children = pg.BoundedChildren(time.monotonic() + 3, lambda: None)
        cleanup = children.cancel_and_reap

        def expire_after_cleanup():
            cleanup()
            children.deadline = time.monotonic() - 1

        with (
            patch.object(children, "cancel_and_reap", side_effect=expire_after_cleanup),
            self.assertRaisesRegex(pg.PostgreSQLRefused, "capture_deadline"),
        ):
            children.run(
                [sys.executable, "-I", "-S", "-c", "print('invented')"],
                {"PATH": "/usr/bin:/bin"},
                64,
                lambda _chunk: None,
            )
        self.assertIsNone(children.child)


@dedicated_process_tests
class CleanupDenialTests(unittest.TestCase):
    def test_failed_inspection_retains_child_and_never_signals_twice(self):
        children = pg.BoundedChildren(time.monotonic() + 2, lambda: None)
        child = SimpleNamespace(
            pid=12345, wait=Mock(return_value=0), stdout=Mock(), returncode=None
        )
        children.child = child
        children.may_signal_child = True
        with (
            patch.object(
                children, "group_members", side_effect=[PermissionError(), ()]
            ),
            patch.object(pg.os, "killpg") as kill,
        ):
            with self.assertRaisesRegex(pg.PostgreSQLRefused, "capture_group_not_gone"):
                children.cancel_and_reap()
            self.assertIs(children.child, child)
            with self.assertRaisesRegex(pg.PostgreSQLRefused, "capture_group_not_gone"):
                children.cancel_and_reap()
            kill.assert_called_once_with(12345, pg.signal.SIGKILL)
        child.wait.assert_not_called()
        child.stdout.close.assert_not_called()

    def test_zombie_only_group_reaped_without_signal(self):
        children = pg.BoundedChildren(time.monotonic() + 2, lambda: None)
        child = SimpleNamespace(
            pid=12345, wait=Mock(return_value=0), stdout=Mock(), returncode=None
        )
        children.child = child
        children.may_signal_child = True
        with (
            patch.object(children, "group_members", side_effect=[((12345, "Z"),), ()]),
            patch.object(pg.os, "killpg") as kill,
        ):
            children.cancel_and_reap()
            children.cancel_and_reap()
        kill.assert_not_called()
        self.assertIsNone(children.child)

    def test_group_readback_rejects_ambiguous_exit_and_wrong_group(self):
        children = pg.BoundedChildren(time.monotonic() + 2, lambda: None)
        for returncode, output in (
            (0, b""),
            (2, b""),
            (0, b"123 999 S\n"),
            (0, b"bad"),
        ):
            with (
                patch.object(
                    pg.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=returncode, stdout=output),
                ),
                self.assertRaises(pg.PostgreSQLRefused),
            ):
                children.group_members(123)

    def test_group_absence_requires_exact_empty_exit_one(self):
        children = pg.BoundedChildren(time.monotonic() + 2, lambda: None)
        with patch.object(
            pg.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout=b"")
        ):
            self.assertEqual(children.group_members(123), ())

    def test_interrupt_during_inspection_signals_but_retains_unproved_child(self):
        children = pg.BoundedChildren(time.monotonic() + 2, lambda: None)
        child = SimpleNamespace(
            pid=12345, wait=Mock(return_value=0), stdout=Mock(), returncode=None
        )
        children.child = child
        children.may_signal_child = True
        with (
            patch.object(
                children, "group_members", side_effect=[KeyboardInterrupt(), ()]
            ),
            patch.object(pg.os, "killpg") as kill,
            self.assertRaisesRegex(pg.PostgreSQLRefused, "capture_group_not_gone"),
        ):
            children.cancel_and_reap()
        kill.assert_called_once_with(12345, pg.signal.SIGKILL)
        child.wait.assert_not_called()
        self.assertIs(children.child, child)

    def test_interrupted_wait_never_signals_again_even_if_not_yet_reaped(self):
        children = pg.BoundedChildren(time.monotonic() + 2, lambda: None)
        child = SimpleNamespace(
            pid=12345,
            wait=Mock(side_effect=[KeyboardInterrupt(), 0]),
            stdout=Mock(),
            returncode=None,
        )
        children.child = child
        children.may_signal_child = True
        with (
            patch.object(
                children,
                "group_members",
                side_effect=[((12345, "S"),), ((12345, "Z"),), ((12345, "Z"),), ()],
            ),
            patch.object(pg.os, "killpg") as kill,
        ):
            with self.assertRaisesRegex(
                pg.PostgreSQLRefused, "capture_child_not_reaped"
            ):
                children.cancel_and_reap()
            self.assertIs(children.child, child)
            with self.assertRaisesRegex(pg.PostgreSQLRefused, "capture_group_not_gone"):
                children.cancel_and_reap()
            kill.assert_called_once_with(12345, pg.signal.SIGKILL)
        self.assertIsNone(children.child)

    def test_interrupted_wait_already_reaped_discards_handle(self):
        children = pg.BoundedChildren(time.monotonic() + 2, lambda: None)
        child = SimpleNamespace(pid=12345, stdout=Mock(), returncode=None)

        def interrupted_reap(**_kwargs):
            child.returncode = 0
            raise KeyboardInterrupt()

        child.wait = Mock(side_effect=interrupted_reap)
        children.child = child
        children.may_signal_child = True
        with (
            patch.object(
                children,
                "group_members",
                side_effect=[((12345, "S"),), ((12345, "Z"),)],
            ),
            patch.object(pg.os, "killpg") as kill,
        ):
            with self.assertRaisesRegex(
                pg.PostgreSQLRefused, "capture_child_not_reaped"
            ):
                children.cancel_and_reap()
            with self.assertRaisesRegex(pg.PostgreSQLRefused, "capture_group_not_gone"):
                children.cancel_and_reap()
            kill.assert_called_once_with(12345, pg.signal.SIGKILL)
        self.assertIsNone(children.child)

    def test_ambiguous_pre_reap_membership_is_permanent_failure(self):
        for observation in (
            (),
            ((12345, "Z"), (12345, "Z")),
            ((12346, "Z"),),
            PermissionError(),
        ):
            for survivor in (False, True):
                with self.subTest(observation=observation, survivor=survivor):
                    children = pg.BoundedChildren(time.monotonic() + 2, lambda: None)
                    child = SimpleNamespace(pid=12345, returncode=None, stdout=Mock())
                    calls = []
                    active = [survivor]

                    def kill(pid, sig):
                        self.assertIsNone(child.returncode)
                        self.assertEqual((pid, sig), (12345, pg.signal.SIGKILL))
                        active[0] = False
                        calls.append("kill")

                    def reap(**_kwargs):
                        self.assertFalse(active[0])
                        calls.append("reap")
                        child.returncode = 0

                    child.wait = Mock(side_effect=reap)
                    children.child, children.may_signal_child = child, True
                    with (
                        patch.object(
                            children,
                            "group_members",
                            side_effect=[observation, ((12345, "Z"),), ()],
                        ),
                        patch.object(pg.os, "killpg", side_effect=kill),
                    ):
                        with self.assertRaisesRegex(
                            pg.PostgreSQLRefused, "capture_group_not_gone"
                        ):
                            children.cancel_and_reap()
                        self.assertIs(children.child, child)
                        child.wait.assert_not_called()
                        with self.assertRaises(pg.PostgreSQLRefused):
                            children.cancel_and_reap()
                    self.assertEqual(calls, ["kill", "reap"])
                    self.assertTrue(children.cleanup_failed)
                    self.assertIsNone(children.child)

    def test_later_ambiguity_retains_child_and_original_deadline(self):
        for observation in (
            (),
            ((12345, "Z"), (12345, "Z")),
            ((12346, "Z"),),
            PermissionError(),
            pg.PostgreSQLRefused("capture_group_readback"),
        ):
            children = pg.BoundedChildren(time.monotonic() + 2, lambda: None)
            child = SimpleNamespace(pid=12345, returncode=None, stdout=Mock())
            calls = []

            def reap(**_kwargs):
                child.returncode = 0
                calls.append("reap")

            def kill(*_args):
                self.assertIsNone(child.returncode)
                calls.append("kill")

            child.wait = Mock(side_effect=reap)
            children.child, children.may_signal_child = child, True
            with (
                patch.object(pg.time, "monotonic", return_value=100.0) as clock,
                patch.object(
                    children,
                    "group_members",
                    side_effect=[
                        ((12345, "S"),),
                        observation,
                        ((12345, "Z"),),
                        (),
                    ],
                ) as observe,
                patch.object(pg.os, "killpg", side_effect=kill),
            ):
                with self.assertRaisesRegex(
                    pg.PostgreSQLRefused, "capture_group_not_gone"
                ):
                    children.cancel_and_reap()
                self.assertIs(children.child, child)
                child.wait.assert_not_called()
                child.stdout.close.assert_not_called()
                clock.return_value = 104.999
                with self.assertRaisesRegex(
                    pg.PostgreSQLRefused, "capture_group_not_gone"
                ):
                    children.cancel_and_reap()
                self.assertLess(child.wait.call_args.kwargs["timeout"], 0.01)
                self.assertEqual(children.cleanup_deadline, 105.0)
                self.assertTrue(
                    all(c.kwargs["deadline"] == 105.0 for c in observe.call_args_list)
                )
            self.assertEqual(calls, ["kill", "reap"])
            self.assertTrue(children.cleanup_failed)
            self.assertIsNone(children.child)

    def test_cleanup_expiry_never_gains_time_or_reaps_unproved_child(self):
        children = pg.BoundedChildren(time.monotonic() + 2, lambda: None)
        child = SimpleNamespace(pid=12345, returncode=None, stdout=Mock(), wait=Mock())
        children.child, children.may_signal_child = child, True
        with (
            patch.object(pg.time, "monotonic", return_value=100.0) as clock,
            patch.object(
                children, "group_members", side_effect=[((12345, "S"),), ()]
            ) as observe,
            patch.object(pg.os, "killpg") as kill,
        ):
            with self.assertRaises(pg.PostgreSQLRefused):
                children.cancel_and_reap()
            for now in (105.0, 110.0):
                clock.return_value = now
                with self.assertRaises(pg.PostgreSQLRefused):
                    children.cancel_and_reap()
                self.assertEqual(children.cleanup_deadline, 105.0)
            self.assertEqual(observe.call_count, 2)
            kill.assert_called_once()
        child.wait.assert_not_called()
        self.assertIs(children.child, child)

    def test_post_reap_evidence_must_be_empty_and_within_original_deadline(self):
        for final, at, accepted in (
            (((12346, "S"),), 104.999, False),
            ((), 104.999, True),
            ((), 105.0, False),
            ((), 105.001, False),
        ):
            children = pg.BoundedChildren(time.monotonic() + 20, lambda: None)
            child = SimpleNamespace(
                pid=12345, returncode=None, stdout=Mock(), wait=Mock()
            )
            children.child, children.may_signal_child = child, True
            with patch.object(pg.time, "monotonic", return_value=100.0) as clock:

                def observe(_pid, *, deadline):
                    self.assertEqual(deadline, 105.0)
                    if children.child is not None:
                        return ((12345, "Z"),)
                    clock.return_value = at
                    return final

                with (
                    patch.object(
                        children, "group_members", side_effect=observe
                    ) as read,
                    patch.object(pg.os, "killpg") as kill,
                ):
                    for _attempt in range(2):
                        if accepted:
                            children.cancel_and_reap()
                        else:
                            with self.assertRaisesRegex(
                                pg.PostgreSQLRefused, "capture_group_not_gone"
                            ):
                                children.cancel_and_reap()
                    self.assertEqual(read.call_count, 2)
                kill.assert_not_called()
                child.wait.assert_called_once()
                self.assertIsNone(children.child)
                self.assertEqual(children.cleanup_failed, not accepted)

    def test_already_reaped_child_is_never_signalled(self):
        children = pg.BoundedChildren(time.monotonic() + 2, lambda: None)
        child = SimpleNamespace(pid=12345, returncode=0, stdout=Mock(), wait=Mock())
        children.child, children.may_signal_child = child, True
        with (
            patch.object(pg.os, "killpg") as kill,
            self.assertRaisesRegex(
                pg.PostgreSQLRefused, "capture_child_already_reaped"
            ),
        ):
            children.cancel_and_reap()
        kill.assert_not_called()
        child.wait.assert_not_called()
        self.assertTrue(children.cleanup_failed)
        self.assertIsNone(children.child)


@dedicated_process_tests
class WallClockTests(unittest.TestCase):
    def test_existing_pending_alarm_is_refused_before_handler_installation(self):
        with (
            patch.object(pg.signal, "sigpending", return_value={signal.SIGALRM}),
            patch.object(pg.signal, "signal") as install,
            patch.object(pg.signal, "setitimer") as arm,
            self.assertRaisesRegex(pg.PostgreSQLRefused, "pending_alarm_refused"),
        ):
            with pg.capture_deadline(time.monotonic() + 1):
                self.fail("entered with a caller-owned pending alarm")
        install.assert_not_called()
        arm.assert_not_called()

    def test_expiry_at_each_teardown_step_restores_handler_timer_and_mask(self):
        original = signal.getsignal(signal.SIGALRM)
        original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        for point in (
            "block",
            "disarm_before",
            "disarm_after",
            "pending",
            "restore_before",
            "restore_after",
            "unmask_before",
            "unmask_after",
        ):
            real_mask = signal.pthread_sigmask
            real_timer = signal.setitimer
            real_pending = signal.sigpending
            real_handler = signal.signal
            expiry = []
            fired = []

            def inject(candidate):
                if candidate == point and expiry and not fired:
                    fired.append(candidate)
                    expiry[0](signal.SIGALRM, None)

            def mask(how, values):
                if how == signal.SIG_SETMASK:
                    inject("unmask_before")
                result = real_mask(how, values)
                if how == signal.SIG_BLOCK and values == {signal.SIGALRM}:
                    inject("block")
                if how == signal.SIG_SETMASK:
                    inject("unmask_after")
                return result

            def timer(which, seconds, interval=0):
                if seconds == 0:
                    inject("disarm_before")
                result = real_timer(which, seconds, interval)
                if seconds == 0:
                    inject("disarm_after")
                return result

            def pending():
                inject("pending")
                return real_pending()

            def handler(number, value):
                if value == original:
                    inject("restore_before")
                result = real_handler(number, value)
                if value == original:
                    inject("restore_after")
                return result

            with (
                self.subTest(point=point),
                patch.object(pg.signal, "pthread_sigmask", side_effect=mask),
                patch.object(pg.signal, "setitimer", side_effect=timer),
                patch.object(pg.signal, "sigpending", side_effect=pending),
                patch.object(pg.signal, "signal", side_effect=handler),
                self.assertRaisesRegex(pg.PostgreSQLRefused, "capture_deadline"),
            ):
                with pg.capture_deadline(time.monotonic() + 1):
                    expiry.append(signal.getsignal(signal.SIGALRM))
            self.assertEqual(fired, [point])
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
            self.assertEqual(signal.getsignal(signal.SIGALRM), original)
            self.assertEqual(
                signal.pthread_sigmask(signal.SIG_BLOCK, set()), original_mask
            )

    def test_real_pending_alarm_drained_before_previous_handler_restored(self):
        previous_calls = []
        original = signal.getsignal(signal.SIGALRM)
        original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        signal.signal(signal.SIGALRM, lambda *_: previous_calls.append("unexpected"))
        previous = signal.getsignal(signal.SIGALRM)
        real_timer = signal.setitimer
        fired = []

        def timer(which, seconds, interval=0):
            result = real_timer(which, seconds, interval)
            if seconds == 0 and not fired:
                fired.append(True)
                os.kill(os.getpid(), signal.SIGALRM)
                self.assertIn(signal.SIGALRM, signal.sigpending())
            return result

        try:
            with (
                patch.object(pg.signal, "setitimer", side_effect=timer),
                self.assertRaisesRegex(pg.PostgreSQLRefused, "capture_deadline"),
            ):
                with pg.capture_deadline(time.monotonic() + 1):
                    pass
            self.assertEqual(previous_calls, [])
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
            self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
            self.assertEqual(
                signal.pthread_sigmask(signal.SIG_BLOCK, set()), original_mask
            )
        finally:
            signal.signal(signal.SIGALRM, original)

    def test_blocking_operation_is_interrupted_and_previous_handler_restored(self):
        original = signal.getsignal(signal.SIGALRM)
        start = time.monotonic()
        with self.assertRaisesRegex(pg.PostgreSQLRefused, "capture_deadline"):
            with pg.capture_deadline(start + 0.04):
                time.sleep(2)
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertEqual(signal.getsignal(signal.SIGALRM), original)

    def test_swallowed_alarm_cannot_return_success(self):
        with self.assertRaisesRegex(pg.PostgreSQLRefused, "capture_deadline"):
            with pg.capture_deadline(time.monotonic() + 0.02):
                try:
                    time.sleep(2)
                except pg.PostgreSQLRefused:
                    pass

    def test_existing_alarm_is_not_overwritten(self):
        with (
            patch.object(pg.signal, "getitimer", return_value=(5.0, 0.0)),
            patch.object(pg.signal, "setitimer") as arm,
            self.assertRaisesRegex(pg.PostgreSQLRefused, "active_alarm_refused"),
        ):
            with pg.capture_deadline(time.monotonic() + 1):
                self.fail("entered with active timer")
        arm.assert_not_called()

    def test_blocked_alarm_is_refused_without_changing_timer(self):
        with (
            patch.object(pg.signal, "pthread_sigmask", return_value={signal.SIGALRM}),
            patch.object(pg.signal, "setitimer") as arm,
            self.assertRaisesRegex(pg.PostgreSQLRefused, "blocked_alarm_refused"),
        ):
            with pg.capture_deadline(time.monotonic() + 1):
                self.fail("entered with blocked timer signal")
        arm.assert_not_called()

    def test_ordinary_exception_also_disarms_and_restores_handler(self):
        original = signal.getsignal(signal.SIGALRM)
        with self.assertRaises(ValueError):
            with pg.capture_deadline(time.monotonic() + 1):
                raise ValueError("invented")
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertEqual(signal.getsignal(signal.SIGALRM), original)

    def test_native_fsync_is_inside_the_same_deadline(self):
        fixture = PayloadTests()
        fixture.setUp()
        try:
            fixture.children.deadline = time.monotonic() + 0.04
            with (
                patch.object(pg.os, "fsync", side_effect=lambda _fd: time.sleep(2)),
                self.assertRaisesRegex(pg.PostgreSQLRefused, "capture_deadline"),
            ):
                fixture.capture()
            self.assertTrue((fixture.root / pg.ARCHIVE_NAME).exists())
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        finally:
            fixture.tearDown()


class ProcessSealTests(unittest.TestCase):
    def isolated(self, source):
        assert pg.__file__ is not None
        bootstrap = """
import _thread, importlib.util, os, resource, signal, subprocess, sys, threading, time
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('staging_snapshot_pg_io', sys.argv[1])
pg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pg
spec.loader.exec_module(pg)
resource.setrlimit(resource.RLIMIT_CORE, (0,0))
def refused(call, code):
    try: call()
    except pg.PostgreSQLRefused as error: assert str(error) == code, str(error)
    else: raise AssertionError('operation unexpectedly accepted')
"""
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                bootstrap + source,
                str(Path(pg.__file__).resolve()),
            ],
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_unsealed_caller_refuses_before_alarm_or_spawn(self):
        self.isolated("""
with patch.object(signal, 'setitimer') as arm, patch.object(subprocess, 'Popen') as spawn:
    refused(lambda: pg.capture_deadline(time.monotonic()+1).__enter__(), 'capture_sealed_process_required')
    refused(lambda: pg.defer_control_signals().__enter__(), 'capture_sealed_process_required')
    arm.assert_not_called(); spawn.assert_not_called()
""")

    def test_core_limit_refuses_before_seal_credentials_or_environment(self):
        self.isolated("""
with patch.object(resource, 'getrlimit', return_value=(0,1)), \
     patch.object(pg, 'native_single_thread') as native, \
     patch.object(pg, 'urlsplit') as parse, \
     patch.object(pg.pwd, 'getpwuid') as account:
    refused(pg.seal_capture_process, 'core_dumps_not_disabled')
    refused(lambda: pg.credentials_from_exact_source_dsn('not-read'), 'core_dumps_not_disabled')
    refused(lambda: pg.connection_environment(pg.Credentials('invented','invented')), 'core_dumps_not_disabled')
    refused(lambda: pg.capture_deadline(time.monotonic()+1).__enter__(), 'core_dumps_not_disabled')
    native.assert_not_called(); parse.assert_not_called(); account.assert_not_called()
assert pg._capture_process_pid is None
""")

    def test_real_unblocked_python_worker_prevents_sealing(self):
        self.isolated("""
ready, done = threading.Event(), threading.Event()
def worker():
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGALRM, signal.SIGINT})
    ready.set(); done.wait(3)
thread = threading.Thread(target=worker); thread.start(); assert ready.wait(1)
try:
    with patch.object(signal, 'signal') as install, patch.object(signal, 'setitimer') as arm:
        refused(pg.seal_capture_process, 'capture_single_thread_required')
        refused(lambda: pg.defer_control_signals().__enter__(), 'capture_sealed_process_required')
        install.assert_not_called(); arm.assert_not_called()
finally:
    done.set(); thread.join(2)
""")

    def test_native_count_catches_unregistered_unblocked_thread(self):
        self.isolated("""
ready, done, stopped = threading.Event(), threading.Event(), threading.Event()
def worker():
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGALRM, signal.SIGINT})
    ready.set(); done.wait(3); stopped.set()
_thread.start_new_thread(worker, ()); assert ready.wait(1)
assert threading.active_count() == 1
try: refused(pg.seal_capture_process, 'capture_single_thread_required')
finally: done.set(); assert stopped.wait(2)
assert pg._capture_process_pid is None
""")

    def test_sealed_process_prevents_all_python_worker_apis(self):
        self.isolated("""
pg.seal_capture_process()
refused(lambda: _thread.start_new_thread(lambda: None, ()), 'capture_process_frozen')
refused(lambda: threading.Thread(target=lambda: None).start(), 'capture_process_frozen')
for event in ('ctypes.dlopen', 'cpython.PyInterpreterState_New', 'os.fork'):
    refused(lambda: sys.audit(event), 'capture_process_frozen')
refused(lambda: pg.ctypes.CDLL(None), 'capture_process_frozen')
refused(lambda: pg.ctypes.get_errno(), 'capture_process_frozen')
assert 'fractions' not in sys.modules
refused(lambda: __import__('fractions'), 'capture_process_frozen')
pg.require_capture_process(); pg.native_single_thread()
""")

    def test_only_closed_prebound_native_function_events_are_allowed(self):
        self.isolated("""
binding = pg._prebound_native_calls()
if sys.platform == 'darwin':
    assert binding[0] is not None and len(binding[1]) == 7
    count = binding[0]._dyld_image_count
    count.argtypes = (); count.restype = pg.ctypes.c_uint32
else:
    assert binding == (None, frozenset())
pg.seal_capture_process()
for address in binding[1]:
    sys.audit('ctypes.call_function', address, ())
for arguments in ((0, ()), (1, ()), (None, ()), ('unbound', ()), (0,), (0, [])):
    refused(lambda: sys.audit('ctypes.call_function', *arguments), 'capture_process_frozen')
for event in ('ctypes.dlopen', 'ctypes.dlsym', 'ctypes.cdata', 'ctypes.get_errno'):
    refused(lambda: sys.audit(event), 'capture_process_frozen')
if sys.platform == 'darwin': assert count() > 0
""")

    def test_native_registry_rejects_missing_and_duplicate_addresses(self):
        self.isolated("""
from types import SimpleNamespace
for value in (None, 0, 1):
    with patch.object(sys, 'platform', 'darwin'), \
         patch.object(pg.ctypes, 'CDLL', return_value=SimpleNamespace(**{
             name: object() for name in ('acl_get_fd_np','acl_get_entry','acl_get_tag_type','acl_free','__error','_dyld_image_count','_dyld_get_image_name')
         })), patch.object(pg.ctypes, 'cast', return_value=SimpleNamespace(value=value)):
        refused(pg._prebound_native_calls, 'capture_native_binding')
assert pg._capture_process_pid is None
""")

    def test_actual_signals_cannot_lose_native_child_before_assignment(self):
        for number in (signal.SIGINT, signal.SIGALRM):
            with self.subTest(number=number):
                self.isolated(f"""
pg.seal_capture_process(); resource.setrlimit(resource.RLIMIT_CORE, (0,0))
real_spawn = subprocess.Popen
owned = []
children = pg.BoundedChildren(time.monotonic()+3, lambda: None)
def spawn(*args, **kwargs):
    if args[0][0] != sys.executable:
        return real_spawn(*args, **kwargs)
    child = real_spawn(*args, **kwargs); owned.append(child)
    refused(lambda: threading.Thread(target=lambda: None).start(), 'capture_process_frozen')
    os.kill(os.getpid(), {int(number)})
    return child
try:
    with patch.object(subprocess, 'Popen', side_effect=spawn):
        with pg.capture_deadline(children.deadline):
            children.run([sys.executable,'-I','-S','-c','import time; time.sleep(5)'],
                         {{'PATH':'/usr/bin:/bin'}}, 0, lambda _: None)
except (KeyboardInterrupt, pg.PostgreSQLRefused): pass
else: raise AssertionError('real signal did not refuse')
assert len(owned) == 1 and owned[0].returncode is not None
assert children.child is None
assert signal.getitimer(signal.ITIMER_REAL) == (0.0,0.0)
""")

    def test_real_pending_alarm_and_worker_attempt_during_teardown(self):
        self.isolated("""
pg.seal_capture_process()
prior_calls = []
signal.signal(signal.SIGALRM, lambda *_: prior_calls.append(True))
prior = signal.getsignal(signal.SIGALRM)
real_timer = signal.setitimer
def timer(which, seconds, interval=0):
    result = real_timer(which, seconds, interval)
    if seconds == 0:
        refused(lambda: _thread.start_new_thread(lambda: None, ()), 'capture_process_frozen')
        os.kill(os.getpid(), signal.SIGALRM)
    return result
def exercise():
    with patch.object(signal, 'setitimer', side_effect=timer):
        with pg.capture_deadline(time.monotonic()+1): pass
refused(exercise, 'capture_deadline')
assert prior_calls == [] and signal.getsignal(signal.SIGALRM) is prior
assert signal.getitimer(signal.ITIMER_REAL) == (0.0,0.0)
""")


if __name__ == "__main__":
    # Python3.13.2 gettext imports locale lazily during unittest argument parsing.
    # Warm the parser dependency before freezing; do not weaken the import guard.
    locale.normalize("C")
    pg.resource.setrlimit(pg.resource.RLIMIT_CORE, (0, 0))
    pg.seal_capture_process()
    unittest.main()
