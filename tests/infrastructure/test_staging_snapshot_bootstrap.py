"""Secretless source fixtures and mocked startup composition, never a consumer.

Native images, interpreter facts, runtime hashing and process sealing are mocked
in composition tests. Timer tests run in isolated children. No real account,
cache, credential, Azure/PG request or encrypted image operation is performed.
"""

import functools
import hashlib
import importlib.machinery
import importlib.util
import os
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
spec = importlib.util.spec_from_file_location(
    "snapshot_bootstrap_under_test", SCRIPTS / "staging_snapshot_bootstrap.py"
)
assert spec is not None and spec.loader is not None
boot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = boot
spec.loader.exec_module(boot)


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="snapshot-invented-source-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "source.py"
        self.raw = b"public_constant = 1\n"
        self.path.write_bytes(self.raw)
        self.path.chmod(0o400)
        self.sha = hashlib.sha256(self.raw).hexdigest()

    def test_exact_dependency_pins_match_current_source(self):
        self.assertEqual(len(boot.SOURCE_PINS), 13)
        self.assertEqual(len(dict(boot.SOURCE_PINS)), 13)
        for name, expected in boot.SOURCE_PINS:
            self.assertEqual(
                hashlib.sha256((SCRIPTS / (name + ".py")).read_bytes()).hexdigest(),
                expected,
            )
        self.assertEqual(
            boot.EXPECTED_RUNTIME_SHA256,
            "4dc3443055fef6f2112c96178fd86df3bf5a675ccdfaac6cd597f2bda13ebb0f",
        )

    def test_exact_public_source_read(self):
        self.assertEqual(boot.initial_source(self.path, self.sha), self.raw)

    def test_mac_encoding_is_explicit_and_not_inherited(self):
        with (
            patch.dict(os.environ, {"__CF_USER_TEXT_ENCODING": "invented ambient"}),
            patch.object(boot.os, "geteuid", return_value=501),
        ):
            self.assertEqual(
                boot.startup_environment()["__CF_USER_TEXT_ENCODING"], "0x1F5:0x0:0x0"
            )

    def test_wrong_hash_or_write_permissions_refused(self):
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            boot.initial_source(self.path, "0" * 64)
        self.path.chmod(0o600)
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            boot.initial_source(self.path, self.sha)

    def test_hardlink_and_symlink_refused(self):
        alias = self.root / "alias.py"
        alias.symlink_to(self.path)
        with self.assertRaises(OSError):
            boot.initial_source(alias, self.sha)
        os.link(self.path, self.root / "hard.py")
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            boot.initial_source(self.path, self.sha)

    def test_empty_oversized_and_unnormalized_refused(self):
        for raw in (b"", b"x" * (128 * 1024 + 1)):
            self.path.chmod(0o600)
            self.path.write_bytes(raw)
            self.path.chmod(0o400)
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                boot.initial_source(self.path, hashlib.sha256(raw).hexdigest())
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            boot.initial_source(
                self.root / ".." / self.root.name / "source.py", self.sha
            )

    def test_short_read_refused_and_descriptor_closed(self):
        close = os.close
        with (
            patch.object(boot.os, "read", return_value=b""),
            patch.object(boot.os, "close", wraps=close) as closed,
        ):
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                boot.initial_source(self.path, self.sha)
            self.assertEqual(closed.call_count, 1)

    def test_linked_metadata_change_refused(self):
        changed = self.path.stat()
        fields = {
            key: getattr(changed, key)
            for key in (
                "st_dev",
                "st_ino",
                "st_uid",
                "st_gid",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        }
        fields["st_ctime_ns"] += 1
        with patch.object(Path, "lstat", return_value=SimpleNamespace(**fields)):
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                boot.initial_source(self.path, self.sha)

    def test_entire_set_checked_before_any_module_exec(self):
        first, second = "invented_snapshot_first", "invented_snapshot_second"
        raw = b"raise AssertionError('must_not_execute')\n"
        (self.root / (first + ".py")).write_bytes(raw)
        (self.root / (first + ".py")).chmod(0o400)
        (self.root / (second + ".py")).write_bytes(b"wrong\n")
        (self.root / (second + ".py")).chmod(0o400)
        pins = ((first, hashlib.sha256(raw).hexdigest()), (second, "0" * 64))
        with patch.object(boot, "SOURCE_PINS", pins):
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                boot.load_sources(self.root)
        self.assertNotIn(first, sys.modules)

    def test_existing_module_refused_before_source_read(self):
        name = "invented_snapshot_shadow"
        with (
            patch.dict(sys.modules, {name: ModuleType(name)}),
            patch.object(boot, "SOURCE_PINS", ((name, self.sha),)),
            patch.object(boot, "initial_source") as read,
        ):
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                boot.load_sources(self.root)
            read.assert_not_called()

    def test_exact_bytes_dependency_order_without_path_import(self):
        source = {
            "invented_snapshot_first": b"VALUE = 17\n",
            "invented_snapshot_second": b"from invented_snapshot_first import VALUE\nRESULT = VALUE + 1\n",
        }
        for name, raw in source.items():
            (self.root / (name + ".py")).write_bytes(raw)
            (self.root / (name + ".py")).chmod(0o400)
        pins = tuple(
            (name, hashlib.sha256(raw).hexdigest()) for name, raw in source.items()
        )
        with patch.dict(sys.modules), patch.object(boot, "SOURCE_PINS", pins):
            original_path = list(sys.path)
            modules = boot.load_sources(self.root)
            self.assertEqual(modules["invented_snapshot_second"].RESULT, 18)
            self.assertEqual(sys.path, original_path)

    def test_native_images_reject_external_and_changed_count(self):
        native = boot.NativeImages.__new__(boot.NativeImages)
        native.count = lambda: 1
        native.name = lambda index: b"/usr/lib/libSystem.B.dylib"
        self.assertEqual(native.observe(), ("/usr/lib/libSystem.B.dylib",))
        for raw in (
            b"/opt/homebrew/lib/unbound.dylib",
            b"/usr/lib/../../private/tmp/evil.dylib",
            None,
        ):
            native.name = lambda index, value=raw: value
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                native.observe()
        native.name = lambda index: b"/usr/lib/libSystem.B.dylib"
        native.count = Mock(side_effect=[1, 2])
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            native.observe()

    def test_native_runtime_symlink_escape_refused(self):
        root = self.root / "runtime"
        root.mkdir()
        (root / "alias.dylib").symlink_to(self.path)
        native = boot.NativeImages.__new__(boot.NativeImages)
        native.count = lambda: 1
        native.name = lambda index: str(root / "alias.dylib").encode()
        with patch.object(boot, "RUNTIME_ROOT", root):
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                native.observe()

    def test_module_origins_accept_only_loader_source_and_runtime(self):
        loader = self.root / "staging_snapshot_bootstrap.py"
        main = ModuleType("__main__")
        main.__file__ = str(loader)
        builtin = ModuleType("invented_builtin")
        builtin.__spec__ = importlib.machinery.ModuleSpec(
            builtin.__name__, None, origin="built-in"
        )
        source = ModuleType("invented_source")
        source.__file__ = str(self.path)
        stdlib = ModuleType("invented_stdlib")
        stdlib.__file__ = str(self.path)
        modules = {
            "__main__": main,
            source.__name__: source,
            stdlib.__name__: stdlib,
            builtin.__name__: builtin,
        }
        with (
            patch.dict(sys.modules, modules, clear=True),
            patch.object(boot, "python_prefix", return_value=self.root),
        ):
            self.assertEqual(
                len(boot.module_origins({source.__name__: source}, loader)), 4
            )
            stdlib.__file__ = "/private/tmp/unbound.py"
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                boot.module_origins({source.__name__: source}, loader)
            stdlib.__file__ = str(self.path)
            sys.modules["invented_unknown"] = ModuleType("invented_unknown")
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                boot.module_origins({source.__name__: source}, loader)

    def test_source_and_class_code_rebinding_detected(self):
        name = "invented_snapshot_bindings"
        module = ModuleType(name)
        exec(
            "def action(): return 1\nclass Adapter:\n def read(self): return 2\n",
            vars(module),
        )
        with patch.dict(sys.modules, {name: module}):
            original = boot.source_bindings({name: module})
            module.action.__code__ = (lambda: 3).__code__
            self.assertNotEqual(boot.source_bindings({name: module}), original)
            original = boot.source_bindings({name: module})
            module.Adapter.read = lambda self: 4
            self.assertNotEqual(boot.source_bindings({name: module}), original)
            sys.modules[name] = ModuleType(name)
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                boot.source_bindings({name: module})

    def test_expat_synthetic_modules_require_exact_pinned_parent_identity(self):
        loader = self.root / "staging_snapshot_bootstrap.py"
        main, parent = ModuleType("__main__"), ModuleType("pyexpat")
        main.__file__, parent.__file__ = str(loader), str(self.path)
        vars(parent).update(
            errors=ModuleType("pyexpat.errors"), model=ModuleType("pyexpat.model")
        )
        modules = {
            "__main__": main,
            "pyexpat": parent,
            "pyexpat.errors": parent.errors,
            "pyexpat.model": parent.model,
            "xml.parsers.expat.errors": parent.errors,
            "xml.parsers.expat.model": parent.model,
        }
        with (
            patch.dict(sys.modules, modules, clear=True),
            patch.object(boot, "python_prefix", return_value=self.root),
        ):
            self.assertEqual(len(boot.module_origins({}, loader)), 6)
            sys.modules["xml.parsers.expat.errors"] = ModuleType("pyexpat.errors")
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                boot.module_origins({}, loader)


class ParentTests(unittest.TestCase):
    def test_exact_parent_facts_required(self):
        prefix = boot.python_prefix()
        expected = {
            "platform": "darwin",
            "implementation": SimpleNamespace(name="cpython"),
            "version_info": (3, 14, 7),
            "executable": str(prefix / "bin/python3.14"),
            "prefix": str(prefix),
            "exec_prefix": str(prefix),
            "base_prefix": str(prefix),
            "base_exec_prefix": str(prefix),
            "path": [
                str(prefix / part)
                for part in (
                    "lib/python314.zip",
                    "lib/python3.14",
                    "lib/python3.14/lib-dynload",
                )
            ],
            "flags": SimpleNamespace(
                isolated=1, no_site=1, dont_write_bytecode=1, optimize=0
            ),
        }
        with ExitStack() as stack:
            for name, value in expected.items():
                stack.enter_context(patch.object(boot.sys, name, value))
            stack.enter_context(
                patch.object(
                    boot.os, "uname", return_value=SimpleNamespace(machine="arm64")
                )
            )
            stack.enter_context(patch.object(boot.os, "getuid", return_value=501))
            stack.enter_context(patch.object(boot.os, "geteuid", return_value=501))
            stack.enter_context(patch.object(boot.sys, "gettrace", return_value=None))
            stack.enter_context(patch.object(boot.sys, "getprofile", return_value=None))
            boot.parent_interpreter()
            for name, value in (
                ("executable", "/unbound/python"),
                ("version_info", (3, 13, 15)),
                ("platform", "linux"),
                ("base_prefix", "/unbound"),
                ("path", [*expected["path"], "/unbound/site-packages"]),
                (
                    "flags",
                    SimpleNamespace(
                        isolated=0, no_site=1, dont_write_bytecode=1, optimize=0
                    ),
                ),
            ):
                with self.subTest(name=name), patch.object(boot.sys, name, value):
                    with self.assertRaises(boot.SnapshotBootstrapRefused):
                        boot.parent_interpreter()


def isolated_timers(cls):
    for name, method in list(vars(cls).items()):
        if not name.startswith("test_"):
            continue

        @functools.wraps(method)
        def run(self, _method=method, _name=name):
            if __name__ == "__main__":
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
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())

        setattr(cls, name, run)
    return cls


@isolated_timers
class TimerTests(unittest.TestCase):
    def test_success_disarms_and_preserves_handler(self):
        previous = signal.getsignal(signal.SIGALRM)
        with boot.startup_deadline(time.monotonic() + 1):
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 0)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0, 0))
        self.assertIs(signal.getsignal(signal.SIGALRM), previous)

    def test_swallowed_timeout_still_refuses_after_teardown(self):
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            with boot.startup_deadline(time.monotonic() + 0.03):
                try:
                    time.sleep(0.08)
                except boot.SnapshotBootstrapRefused:
                    pass
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0, 0))

    def test_active_timer_never_reset(self):
        signal.setitimer(signal.ITIMER_REAL, 2)
        try:
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                with boot.startup_deadline(time.monotonic() + 1):
                    self.fail("nested timer accepted")
            self.assertGreater(signal.getitimer(signal.ITIMER_REAL)[0], 1)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

    def test_blocked_alarm_and_expired_deadlines_refused(self):
        for deadline in (float("nan"), time.monotonic() - 1, time.monotonic() + 91):
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                with boot.startup_deadline(deadline):
                    self.fail("invalid deadline accepted")
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        try:
            with self.assertRaises(boot.SnapshotBootstrapRefused):
                with boot.startup_deadline(time.monotonic() + 1):
                    self.fail("blocked alarm accepted")
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temporary = self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="snapshot-invented-bootstrap-")
        )
        root = Path(self.temporary).resolve()
        loader = root / "staging_snapshot_bootstrap.py"
        loader.write_bytes(b"# invented loader fixture, never executed\n")
        loader.chmod(0o400)
        self.loader_sha = hashlib.sha256(loader.read_bytes()).hexdigest()
        self.deadline = time.monotonic() + 60
        self.events, self.active = [], False
        self.native_images = ("/usr/lib/libSystem.B.dylib",)
        self.content = {loader: loader.read_bytes()}
        owner = self

        @contextmanager
        def guard(deadline):
            self.assertEqual(deadline, self.deadline)
            self.assertFalse(self.active)
            self.events.append("guard_enter")
            self.active = True
            try:
                yield
            finally:
                self.active = False
                self.events.append("guard_exit")

        class Files:
            def __init__(self):
                owner.events.append("native_files_preload")

            def read(self, path, **kwargs):
                owner.assertTrue(owner.active)
                owner.assertIn("sealed", owner.events)
                return SimpleNamespace(content=owner.content[path], identity="invented")

        class Native:
            def observe(self):
                return owner.native_images

        class Runtime:
            def __init__(self, files, root, expected):
                self._files, self._root, self._expected = files, root, expected
                owner.assertEqual(expected, boot.EXPECTED_RUNTIME_SHA256)
                owner.assertEqual(root, boot.RUNTIME_ROOT)

            def bind(self, *, deadline):
                with guard(deadline):
                    owner.events.append("runtime_bind")

            def revalidate(self):
                owner.assertTrue(owner.active)
                owner.events.append("runtime_recheck")

        class PostgreSQLRuntime(Runtime):
            def __init__(self, files):
                super().__init__(files, boot.RUNTIME_ROOT, boot.EXPECTED_RUNTIME_SHA256)

        modules = {}
        for name in (
            "staging_snapshot_pg_io",
            "staging_snapshot_native_files",
            "staging_snapshot_runtime",
            "staging_snapshot_postgresql_runtime",
        ):
            module = ModuleType(name)
            module.__file__ = str(root / (name + ".py"))
            self.content[Path(module.__file__)] = name.encode()
            modules[name] = module
        modules["staging_snapshot_pg_io"].seal_capture_process = (
            lambda: self.events.append("sealed")
        )
        modules["staging_snapshot_pg_io"].require_capture_process = (
            lambda: self.assertIn("sealed", self.events)
        )
        modules["staging_snapshot_pg_io"].capture_deadline = guard
        modules["staging_snapshot_native_files"].NativeFiles = Files
        modules["staging_snapshot_runtime"].SnapshotRuntime = Runtime
        modules[
            "staging_snapshot_postgresql_runtime"
        ].PostgreSQLRuntime = PostgreSQLRuntime
        self.modules = modules
        pins = tuple(
            (name, hashlib.sha256(self.content[Path(module.__file__)]).hexdigest())
            for name, module in modules.items()
        )
        self.stack.enter_context(patch.dict(sys.modules, modules))
        self.stack.enter_context(
            patch.dict(os.environ, boot.startup_environment(), clear=True)
        )
        for name, value in {
            "_started": False,
            "__file__": str(loader),
            "SOURCE_PINS": pins,
            "PRELOAD": (),
            "NativeImages": Native,
            "startup_deadline": guard,
            "parent_interpreter": Mock(),
            "load_sources": Mock(return_value=modules),
            "module_origins": Mock(return_value=("invented",)),
            "source_bindings": Mock(return_value=("invented",)),
        }.items():
            self.stack.enter_context(patch.object(boot, name, value))
        self.stack.enter_context(patch.object(boot.resource, "setrlimit"))
        self.stack.enter_context(
            patch.object(boot.resource, "getrlimit", return_value=(0, 0))
        )
        self.stack.enter_context(
            patch.object(
                boot.signal,
                "getitimer",
                side_effect=lambda timer: (1.0 if self.active else 0.0, 0.0),
            )
        )
        self.stack.enter_context(patch.object(ssl, "SSLContext"))
        self.guard = guard

    def bind(self):
        return boot.bind_process(loader_sha256=self.loader_sha, deadline=self.deadline)

    def test_composes_preload_seal_binding_and_single_absolute_deadline(self):
        bound = self.bind()
        self.assertLess(
            self.events.index("native_files_preload"), self.events.index("sealed")
        )
        self.assertLess(self.events.index("sealed"), self.events.index("runtime_bind"))
        self.assertNotIn("DYLD_LIBRARY_PATH", os.environ)
        self.assertNotIn("OPENSSL_CONF", os.environ)
        self.assertNotIn("OPENSSL_MODULES", os.environ)
        self.assertEqual(bound.runtime._expected, boot.EXPECTED_RUNTIME_SHA256)
        with self.guard(self.deadline):
            bound.revalidate()

    def test_ambient_setting_refuses_before_loading_or_core_change(self):
        os.environ["HTTPS_PROXY"] = "https://invented.invalid"
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            self.bind()
        boot.load_sources.assert_not_called()
        boot.resource.setrlimit.assert_not_called()

    def test_wrong_loader_hash_refuses_before_loading(self):
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            boot.bind_process(loader_sha256="0" * 64, deadline=self.deadline)
        boot.load_sources.assert_not_called()

    def test_failed_attempt_cannot_retry_in_same_process(self):
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            boot.bind_process(loader_sha256="0" * 64, deadline=self.deadline)
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            self.bind()
        boot.load_sources.assert_not_called()

    def test_startup_environment_change_refuses_before_binding(self):
        def changed():
            self.events.append("sealed")
            os.environ["OPENSSL_CONF"] = "/invented/changed.cnf"

        self.modules["staging_snapshot_pg_io"].seal_capture_process = changed
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            self.bind()
        self.assertNotIn("runtime_bind", self.events)

    def test_source_change_during_load_refuses_before_runtime(self):
        def changed():
            self.events.append("sealed")
            path = Path(self.modules["staging_snapshot_pg_io"].__file__)
            self.content[path] = b"changed after exec"

        self.modules["staging_snapshot_pg_io"].seal_capture_process = changed
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            self.bind()
        self.assertNotIn("runtime_bind", self.events)

    def test_later_source_or_native_image_drift_refuses(self):
        bound = self.bind()
        self.native_images += ("/usr/lib/additional.dylib",)
        with (
            self.guard(self.deadline),
            self.assertRaises(boot.SnapshotBootstrapRefused),
        ):
            bound.revalidate()
        self.native_images = bound.images
        path = Path(self.modules["staging_snapshot_pg_io"].__file__)
        self.content[path] = b"later changed source"
        with (
            self.guard(self.deadline),
            self.assertRaises(boot.SnapshotBootstrapRefused),
        ):
            bound.revalidate()

    def test_missing_guard_and_rebound_runtime_authority_refuse(self):
        bound = self.bind()
        with self.assertRaises(boot.SnapshotBootstrapRefused):
            bound.revalidate()
        bound.runtime._expected = "0" * 64
        with (
            self.guard(self.deadline),
            self.assertRaises(boot.SnapshotBootstrapRefused),
        ):
            bound.revalidate()


if __name__ == "__main__":
    unittest.main()
