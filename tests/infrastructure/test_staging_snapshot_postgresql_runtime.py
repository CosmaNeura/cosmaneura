"""Invented installed-client/private-library trees; no real client or database.

Native protected reads and filesystem changes are real on the fixture. Only
platform facts and the fixture ownership of public OS files are substituted.
The separate native integration diagnostic checks actual root-owned OS files.
"""

import atexit
import functools
import hashlib
import importlib.util
import locale
import os
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

for name in (
    "staging_snapshot_pg_io",
    "staging_snapshot_native_files",
    "staging_snapshot_postgresql_runtime",
):
    if name in sys.modules:
        continue
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[2] / "scripts" / (name + ".py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
pg = sys.modules["staging_snapshot_pg_io"]
native = sys.modules["staging_snapshot_native_files"]
runtime = sys.modules["staging_snapshot_postgresql_runtime"]
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


class SourcePinTests(unittest.TestCase):
    def test_closed_inventory_has_fifteen_files_twelve_alias_routes(self):
        self.assertEqual(len(runtime.FILE_PINS), 15)
        self.assertEqual(len(runtime.LOAD_ROUTES), 12)
        paths = {row[0] for row in runtime.FILE_PINS}
        self.assertEqual(len(paths), 15)
        self.assertEqual(len({Path(path).name for path in paths}), 15)
        self.assertTrue(all(target in paths for _, target in runtime.LOAD_ROUTES))
        self.assertEqual(
            {
                Path(path).name
                for path, _, mode, _ in runtime.FILE_PINS
                if mode == 0o555
            },
            {"pg_dump", "pg_restore", "psql"},
        )
        for path, size, mode, sha in runtime.FILE_PINS:
            self.assertTrue(path.startswith("Cellar/"))
            self.assertGreater(size, 0)
            self.assertIn(mode, (0o444, 0o555))
            self.assertRegex(sha, r"^[0-9a-f]{64}$")
        aliases = {
            Path(source).name: Path(target).name
            for source, target in runtime.LOAD_ROUTES
            if Path(source).name != Path(target).name
        }
        self.assertEqual(
            aliases,
            {
                "liblz4.1.dylib": "liblz4.1.10.0.dylib",
                "libreadline.8.dylib": "libreadline.8.3.dylib",
                "libzstd.1.dylib": "libzstd.1.5.7.dylib",
            },
        )
        self.assertEqual(runtime.CLIENT_LIBRARIES, pg.CLIENT_LIBRARIES)


@isolated_tests
class PostgreSQLRuntimeTests(unittest.TestCase):
    def setUp(self):
        if FILES is None:
            return
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="snapshot-invented-pg-runtime-")
        )
        self.root = Path(temporary).resolve()
        self.brew = self.root / "homebrew"
        self.brew.mkdir(mode=0o700)
        self.private = self.root / "pg-libraries"
        self.private.mkdir(mode=0o700)
        self.stack.callback(self.private.chmod, 0o700)
        self.library = self.private / "client.1.dylib"
        self.binary = self.brew / "Cellar/invented/1/bin/client"
        self.binary.parent.mkdir(parents=True)
        self.library.write_bytes(b"invented library, not executable")
        self.binary.write_bytes(b"invented binary, not executable")
        self.library.chmod(0o400)
        self.binary.chmod(0o555)
        self.link = self.private / "client.dylib"
        self.link.symlink_to("client.1.dylib")
        self.private.chmod(0o500)
        self.build, self.ca = self.root / "build.plist", self.root / "ca.pem"
        self.build.write_bytes(
            plistlib.dumps({"ProductBuildVersion": "invented-build"})
        )
        self.build.chmod(0o444)
        self.ca.write_bytes(b"x" * 333483)
        self.ca.chmod(0o644)
        pins = tuple(
            (
                relative,
                path.stat().st_size,
                mode,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path, mode, relative in (
                (self.library, 0o444, "Cellar/invented/1/lib/client.1.dylib"),
                (self.binary, 0o555, "Cellar/invented/1/bin/client"),
            )
        )
        for name, value in {
            "BREW": self.brew,
            "CLIENT_LIBRARIES": self.private,
            "FILE_PINS": pins,
            "LOAD_ROUTES": (("opt/invented/lib/client.dylib", pins[0][0]),),
            "BUILD": self.build,
            "CA": self.ca,
            "CA_SHA256": hashlib.sha256(self.ca.read_bytes()).hexdigest(),
            "EXPECTED_BUILD": "invented-build",
            "sys": SimpleNamespace(platform="darwin"),
        }.items():
            self.stack.enter_context(patch.object(runtime, name, value))
        self.stack.enter_context(
            patch.object(
                runtime.os, "uname", return_value=SimpleNamespace(machine="arm64")
            )
        )
        read = runtime.PostgreSQLRuntime._file
        owner = self

        def fixture_file(instance, records, path, **kwargs):
            if path in (owner.build, owner.ca):
                owner.assertEqual(kwargs["owner"], 0)
                kwargs["owner"] = os.geteuid()
            return read(instance, records, path, **kwargs)

        self.stack.enter_context(
            patch.object(runtime.PostgreSQLRuntime, "_file", fixture_file)
        )
        self.binding = runtime.PostgreSQLRuntime(FILES)

    def bind(self):
        return self.binding.bind(deadline=time.monotonic() + 5)

    def revalidate(self):
        with pg.capture_deadline(time.monotonic() + 2):
            self.binding.revalidate()

    @contextmanager
    def namespace_edit(self):
        self.private.chmod(0o700)
        try:
            yield
        finally:
            self.private.chmod(0o500)

    def test_bind_and_retained_check_cover_real_link_spellings(self):
        observation = self.bind()
        self.assertEqual(observation.client_and_library_files, 2)
        self.assertEqual(observation.load_routes, 1)
        self.assertTrue(observation.not_execution_authority)
        self.assertIn(self.link, {row[0] for row in self.binding._metadata})
        self.revalidate()

    def test_wrong_digest_refused_without_rebaselining(self):
        self.library.chmod(0o644)
        self.library.write_bytes(b"changed library, not executable")
        self.library.chmod(0o400)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()

    def test_writable_binary_and_parent_refused(self):
        self.binary.chmod(0o755)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()
        self.binary.chmod(0o555)
        self.binary.parent.chmod(0o777)
        self.binding = runtime.PostgreSQLRuntime(FILES)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()

    def test_hardlinked_and_canonical_symlink_library_refused(self):
        extra = self.root / "hardlink"
        os.link(self.library, extra)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()
        with self.namespace_edit():
            self.library.unlink()
            self.library.symlink_to(extra)
        self.binding = runtime.PostgreSQLRuntime(FILES)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()

    def test_absolute_and_outside_root_load_links_refused(self):
        with self.namespace_edit():
            self.link.unlink()
            self.link.symlink_to(self.library)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()
        with self.namespace_edit():
            self.link.unlink()
            self.link.symlink_to("../outside")
        self.binding = runtime.PostgreSQLRuntime(FILES)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()

    def test_cycle_and_wrong_pinned_target_refused(self):
        alias = self.link
        with self.namespace_edit():
            alias.unlink()
            alias.symlink_to("client.dylib")
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()
        with self.namespace_edit():
            alias.unlink()
            alias.symlink_to("../homebrew/Cellar/invented/1/bin/client")
        self.binding = runtime.PostgreSQLRuntime(FILES)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()

    def test_library_replacement_detected_after_binding(self):
        self.bind()
        with self.namespace_edit():
            replacement = self.library.parent / "replacement"
            replacement.write_bytes(self.library.read_bytes())
            replacement.chmod(0o400)
            os.replace(replacement, self.library)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.revalidate()

    def test_in_place_change_with_restored_mtime_detected(self):
        self.bind()
        before = self.library.stat()
        self.library.chmod(0o644)
        self.library.write_bytes(b"x" * before.st_size)
        self.library.chmod(0o400)
        os.utime(self.library, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.revalidate()

    def test_link_replacement_with_same_target_detected(self):
        self.bind()
        target = os.readlink(self.link)
        with self.namespace_edit():
            self.link.unlink()
            self.link.symlink_to(target)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.revalidate()

    def test_parent_namespace_change_detected(self):
        self.bind()
        with self.namespace_edit():
            (self.library.parent / "added-after-binding").write_bytes(b"invented")
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.revalidate()

    def test_private_extra_shadow_library_and_missing_alias_refused(self):
        extra = self.private / "libSystem.B.dylib"
        with self.namespace_edit():
            extra.write_bytes(b"unapproved shadow library")
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()
        with self.namespace_edit():
            extra.unlink()
            self.link.unlink()
        self.binding = runtime.PostgreSQLRuntime(FILES)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()

    def test_private_writable_directory_and_writable_library_refused(self):
        self.private.chmod(0o700)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()
        self.private.chmod(0o500)
        self.library.chmod(0o600)
        self.binding = runtime.PostgreSQLRuntime(FILES)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()

    def test_private_directory_symlink_and_regular_alias_refused(self):
        substitute = self.root / "private-link"
        substitute.symlink_to(self.private)
        with patch.object(runtime, "CLIENT_LIBRARIES", substitute):
            with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
                self.bind()
        with self.namespace_edit():
            self.link.unlink()
            self.link.write_bytes(self.library.read_bytes())
            self.link.chmod(0o400)
        self.binding = runtime.PostgreSQLRuntime(FILES)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()

    def test_original_opt_shortcut_is_never_used(self):
        opt = self.brew / "opt"
        opt.mkdir()
        (opt / "invented").symlink_to("/unbound/outside")
        opt.chmod(0o775)
        self.bind()
        self.assertFalse(
            any(path.is_relative_to(opt) for path, *_ in self.binding._metadata)
        )
        self.revalidate()

    def test_installed_client_parent_change_detected(self):
        self.bind()
        (self.binary.parent / "unrelated").write_bytes(b"invented")
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.revalidate()

    def test_ca_drift_and_wrong_os_build_refused(self):
        self.ca.write_bytes(b"y" * 333483)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()
        self.ca.write_bytes(b"x" * 333483)
        self.build.chmod(0o644)
        self.build.write_bytes(plistlib.dumps({"ProductBuildVersion": "wrong-build"}))
        self.build.chmod(0o444)
        self.binding = runtime.PostgreSQLRuntime(FILES)
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()

    def test_missing_bind_or_deadline_and_second_bind_refused(self):
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.revalidate()
        self.bind()
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.binding.revalidate()
        with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
            self.bind()

    def test_short_read_and_swallowed_timeout_refused(self):
        with patch.object(runtime.os, "read", return_value=b""):
            with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
                self.bind()
        self.binding = runtime.PostgreSQLRuntime(FILES)
        original_read = os.read
        delayed = False

        def slow(fd, count):
            nonlocal delayed
            if not delayed:
                delayed = True
                try:
                    time.sleep(0.1)
                except pg.PostgreSQLRefused:
                    pass
            return original_read(fd, count)

        with patch.object(runtime.os, "read", side_effect=slow):
            with self.assertRaises(runtime.PostgreSQLRuntimeRefused):
                self.binding.bind(deadline=time.monotonic() + 0.02)


if __name__ == "__main__":
    assert callable(atexit.register)
    locale.normalize("C")
    pg.resource.setrlimit(pg.resource.RLIMIT_CORE, (0, 0))
    FILES = native.NativeFiles()
    pg.seal_capture_process()
    unittest.main()
