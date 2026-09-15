"""Invented immutable formula trees only; no CLI/cache/network or real runtime."""

import functools
import hashlib
import importlib.util
import json
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
    "staging_snapshot_runtime",
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
runtime = sys.modules["staging_snapshot_runtime"]

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
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())

        setattr(cls, name, run)
    return cls


@isolated_tests
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        if FILES is None:
            return
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snapshot-invented-runtime-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "homebrew"
        (self.root / "opt").mkdir(parents=True)
        for name, version in runtime.formulae():
            directory = self.root / "Cellar" / name / version
            directory.mkdir(parents=True)
            (directory / "invented.txt").write_bytes(name.encode())
            (self.root / "opt" / name).symlink_to("../Cellar/" + name + "/" + version)
        python = (
            self.root
            / "Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/bin/python3.14"
        )
        python.parent.mkdir(parents=True)
        python.write_bytes(b"not an executable; invented fixture only")
        python.chmod(0o500)
        (
            self.root / "Cellar/azure-cli/2.89.1/libexec/lib/python3.14/site-packages"
        ).mkdir(parents=True)
        self.file = self.root / "Cellar/azure-cli/2.89.1/invented.txt"
        expected = {}
        for formula, version in runtime.formulae():
            directory = self.root / "Cellar" / formula / version
            for path in [directory, *sorted(directory.rglob("*"))]:
                relative = path.relative_to(self.root).as_posix()
                if path.is_dir():
                    expected[relative] = ["directory"]
                else:
                    content = path.read_bytes()
                    executable = path == python
                    expected[relative] = [
                        "file",
                        executable,
                        len(content),
                        hashlib.sha256(content).hexdigest(),
                    ]
            expected["opt/" + formula] = [
                "link",
                "../Cellar/" + formula + "/" + version,
            ]
        self.expected = hashlib.sha256(
            json.dumps(
                [[name, *expected[name]] for name in sorted(expected)],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        self.expected_entries = len(expected)
        for path in sorted(self.root.rglob("*"), reverse=True):
            if not path.is_symlink():
                path.chmod(0o500 if path.is_dir() or path == python else 0o400)
        self.root.chmod(0o500)
        assert FILES is not None
        self.binding = runtime.SnapshotRuntime(FILES, self.root, self.expected)

    def bind(self):
        return self.binding.bind(deadline=time.monotonic() + 5)

    def revalidate(self):
        with pg.capture_deadline(time.monotonic() + 2):
            self.binding.revalidate()

    def test_complete_fixture_manifest_and_retained_binding(self):
        observed = self.bind()
        self.assertEqual(observed.manifest_sha256, self.expected)
        self.assertEqual(observed.entries, self.expected_entries)
        self.assertTrue(observed.not_execution_authority)
        self.assertGreater(observed.bytes_hashed, 0)
        self.assertEqual(self.binding.python.name, "python3.14")
        self.assertEqual(self.binding.site_packages.name, "site-packages")
        self.revalidate()
        with self.assertRaises(runtime.SnapshotRuntimeRefused):
            self.bind()  # Cannot silently rebaseline the same object.

    def test_wrong_manifest_never_publishes_a_binding(self):
        assert FILES is not None
        self.binding = runtime.SnapshotRuntime(FILES, self.root, "0" * 64)
        with self.assertRaises(runtime.SnapshotRuntimeRefused):
            self.bind()
        self.assertFalse(self.binding._metadata)
        self.assertIsNone(self.binding._observation)

    def test_writable_file_and_directory_refuse(self):
        self.file.chmod(0o600)
        with self.assertRaises(runtime.SnapshotRuntimeRefused):
            self.bind()
        self.file.chmod(0o400)
        self.file.parent.chmod(0o700)
        with self.assertRaises(runtime.SnapshotRuntimeRefused):
            self.bind()

    def test_unexpected_formula_or_opt_file_refuses(self):
        opt = self.root / "opt"
        opt.chmod(0o700)
        (opt / "unexpected").write_bytes(b"invented")
        opt.chmod(0o500)
        with self.assertRaises(runtime.SnapshotRuntimeRefused):
            self.bind()

    def test_absolute_and_escaping_links_refuse(self):
        opt = self.root / "opt"
        link = opt / "azure-cli"
        for target in (str(self.file), "../../outside"):
            opt.chmod(0o700)
            link.unlink()
            link.symlink_to(target)
            opt.chmod(0o500)
            with self.assertRaises(runtime.SnapshotRuntimeRefused):
                self.bind()

    def test_multiple_link_or_fifo_refuses_without_blocking(self):
        parent = self.file.parent
        parent.chmod(0o700)
        os.link(self.file, parent / "second")
        parent.chmod(0o500)
        with self.assertRaises(runtime.SnapshotRuntimeRefused):
            self.bind()
        parent.chmod(0o700)
        (parent / "second").unlink()
        os.mkfifo(parent / "pipe", 0o400)
        parent.chmod(0o500)
        with self.assertRaises(runtime.SnapshotRuntimeRefused):
            self.bind()

    def test_in_place_change_with_restored_mtime_refuses(self):
        self.bind()
        before = self.file.stat()
        self.file.chmod(0o600)
        self.file.write_bytes(b"alternate")
        self.file.chmod(0o400)
        os.utime(self.file, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.file.stat().st_ino, before.st_ino)
        self.assertEqual(self.file.stat().st_mtime_ns, before.st_mtime_ns)
        with self.assertRaises(runtime.SnapshotRuntimeRefused):
            self.revalidate()

    def test_added_then_removed_file_invalidates_directory_binding(self):
        self.bind()
        parent = self.file.parent
        parent.chmod(0o700)
        extra = parent / "extra"
        extra.write_bytes(b"invented")
        extra.unlink()
        parent.chmod(0o500)
        with self.assertRaises(runtime.SnapshotRuntimeRefused):
            self.revalidate()

    def test_mutation_after_scan_before_publication_refuses(self):
        original = runtime.SnapshotRuntime._scan

        def changed(binding):
            scanned = original(binding)
            self.file.chmod(0o600)
            return scanned

        with patch.object(runtime.SnapshotRuntime, "_scan", changed):
            with self.assertRaises(runtime.SnapshotRuntimeRefused):
                self.bind()
        self.assertFalse(self.binding._metadata)

    def test_unbound_or_untimed_revalidation_refuses(self):
        with self.assertRaises(runtime.SnapshotRuntimeRefused):
            self.revalidate()
        self.bind()
        with self.assertRaises(runtime.SnapshotRuntimeRefused):
            self.binding.revalidate()

    def test_deadline_releases_all_owned_descriptors(self):
        opened = []
        original_open = os.open

        def opening(*args, **kwargs):
            descriptor = original_open(*args, **kwargs)
            opened.append(descriptor)
            return descriptor

        with (
            patch.object(native.os, "open", side_effect=opening),
            patch.object(runtime.os, "read", side_effect=lambda *_: time.sleep(2)),
        ):
            with self.assertRaisesRegex(
                runtime.SnapshotRuntimeRefused, "^snapshot_runtime_refused$"
            ):
                self.binding.bind(deadline=time.monotonic() + 0.04)
        self.assertGreater(len(opened), 5)
        for descriptor in opened:
            with self.assertRaises(OSError):
                os.fstat(descriptor)


if __name__ == "__main__":
    locale.normalize("C")
    pg.resource.setrlimit(pg.resource.RLIMIT_CORE, (0, 0))
    FILES = native.NativeFiles()
    pg.seal_capture_process()
    unittest.main()
