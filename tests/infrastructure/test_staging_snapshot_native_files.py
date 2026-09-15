"""Only invented local files and ACLs; no real account/cache/CLI/network."""

import ctypes
import functools
import importlib.util
import locale
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

for module_name in (
    "staging_snapshot_capture",
    "staging_snapshot_pg_io",
    "staging_snapshot_native_files",
):
    if module_name in sys.modules:
        continue
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).resolve().parents[2] / "scripts" / (module_name + ".py"),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
native = sys.modules["staging_snapshot_native_files"]
pg = sys.modules["staging_snapshot_pg_io"]

FILES = None


def isolated_tests(cls):
    for name, method in list(vars(cls).items()):
        if not name.startswith("test_"):
            continue

        @functools.wraps(method)
        def run(self, _method=method, _name=name):
            if FILES is not None:
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
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())

        setattr(cls, name, run)
    return cls


@isolated_tests
class NativeTests(unittest.TestCase):
    def setUp(self):
        if FILES is None:
            return
        self.directory = tempfile.TemporaryDirectory(prefix="snapshot-invented-native-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.root.chmod(0o700)
        self.path = self.root / "invented.json"
        self.path.write_bytes(b'{"invented":true}')
        self.path.chmod(0o600)

    def read(self):
        assert FILES is not None
        return FILES.read(self.path, mode=0o600, maximum=4096)

    def test_real_protected_read_after_native_loading_is_sealed(self):
        with self.assertRaises(pg.PostgreSQLRefused):
            ctypes.CDLL(None)
        with self.assertRaises(pg.PostgreSQLRefused):
            ctypes.get_errno()
        value = self.read()
        self.assertEqual(value.content, b'{"invented":true}')
        self.assertNotIn("invented", repr(value))
        self.assertEqual(value.file_identity, native.identity(self.path.stat()))

    def test_wrong_modes_parent_or_nonregular_file_refuse(self):
        self.path.chmod(0o644)
        with self.assertRaises(native.ProtectedReadRefused):
            self.read()
        self.path.chmod(0o600)
        self.root.chmod(0o755)
        with self.assertRaises(native.ProtectedReadRefused):
            self.read()
        self.root.chmod(0o700)
        self.path = self.root / "pipe"
        os.mkfifo(self.path, 0o600)
        started = time.monotonic()
        with self.assertRaises(native.ProtectedReadRefused):
            self.read()
        self.assertLess(time.monotonic() - started, 1)

    def test_symlink_and_multiple_link_refuse_without_changing_original(self):
        original = self.path
        link = self.root / "link"
        link.symlink_to(original)
        self.path = link
        with self.assertRaises(native.ProtectedReadRefused):
            self.read()
        self.path = original
        os.link(original, self.root / "second")
        with self.assertRaises(native.ProtectedReadRefused):
            self.read()
        self.assertEqual(original.read_bytes(), b'{"invented":true}')

    def test_changes_during_read_and_oversize_refuse(self):
        real_read = os.read

        def changing(descriptor, count):
            value = real_read(descriptor, count)
            self.path.write_bytes(b'{"invented":null}')
            return value

        with patch.object(native.os, "read", side_effect=changing):
            with self.assertRaises(native.ProtectedReadRefused):
                self.read()
        assert FILES is not None
        with self.assertRaises(native.ProtectedReadRefused):
            FILES.read(self.path, mode=0o600, maximum=2)

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL regression")
    def test_ancestor_allow_acl_refuses_before_content_read(self):
        nested = self.root / "nested"
        nested.mkdir(mode=0o700)
        self.path.rename(nested / "invented.json")
        self.path = nested / "invented.json"
        result = subprocess.run(
            ["/bin/chmod", "+a", "everyone allow readattr", str(self.root)],
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        with patch.object(native.os, "read") as read:
            with self.assertRaises(native.ProtectedReadRefused):
                self.read()
        read.assert_not_called()

    def test_renamed_ancestor_refuses_even_when_held_file_is_unchanged(self):
        nested = self.root / "nested"
        nested.mkdir(mode=0o700)
        self.path.rename(nested / "invented.json")
        self.path = nested / "invented.json"
        moved = self.root / "moved"
        original_read = os.read

        def replacing(descriptor, count):
            value = original_read(descriptor, count)
            nested.rename(moved)
            nested.mkdir(mode=0o700)
            return value

        with patch.object(native.os, "read", side_effect=replacing):
            with self.assertRaises(native.ProtectedReadRefused):
                self.read()
        self.assertEqual((moved / "invented.json").read_bytes(), b'{"invented":true}')

    def test_interrupted_ancestor_open_closes_every_owned_descriptor(self):
        opened = []
        real_open = os.open

        def interrupted(path, *args, **kwargs):
            if len(opened) == 3:
                raise KeyboardInterrupt("invented interruption")
            descriptor = real_open(path, *args, **kwargs)
            opened.append(descriptor)
            return descriptor

        with patch.object(native.os, "open", side_effect=interrupted):
            with self.assertRaisesRegex(
                native.ProtectedReadRefused, "^snapshot_protected_read_refused$"
            ):
                self.read()
        self.assertEqual(len(opened), 3)
        for descriptor in opened:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL regression")
    def test_real_external_allow_acl_is_rejected(self):
        subprocess.run(
            ["/bin/chmod", "+a", "everyone allow read", str(self.path)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=2,
        )
        with self.assertRaises(native.ProtectedReadRefused):
            self.read()

    def test_deadline_closes_every_owned_descriptor(self):
        opened = []
        real_open = os.open

        def opening(*args, **kwargs):
            descriptor = real_open(*args, **kwargs)
            opened.append(descriptor)
            return descriptor

        with (
            patch.object(native.os, "open", side_effect=opening),
            patch.object(native.os, "read", side_effect=lambda *_: time.sleep(2)),
        ):
            with self.assertRaises(Exception):
                with pg.capture_deadline(time.monotonic() + 0.04):
                    self.read()
        self.assertEqual(len(opened), len(self.path.parts))
        for descriptor in opened:
            with self.assertRaises(OSError):
                os.fstat(descriptor)


if __name__ == "__main__":
    locale.normalize("C")
    pg.resource.setrlimit(pg.resource.RLIMIT_CORE, (0, 0))
    FILES = native.NativeFiles()
    pg.seal_capture_process()
    unittest.main()
