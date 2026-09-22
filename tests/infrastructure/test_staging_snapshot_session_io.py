"""Invented files/package only; native encryption/mount queries are MOCKED.

No actual hdiutil, diskutil, Azure CLI, account/cache, token, network or data
capture. Temporary fixture files are intentionally NOT encryption evidence.
"""

import atexit
import copy
import functools
import hashlib
import importlib.util
import json
import locale
import os
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

for module_name in (
    "staging_encrypted_workspace",
    "staging_snapshot_capture",
    "staging_snapshot_pg_io",
    "staging_snapshot_identity",
    "staging_snapshot_native_files",
    "staging_snapshot_runtime",
    "staging_snapshot_workspace",
    "staging_snapshot_configuration",
    "staging_snapshot_cli",
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

cli = sys.modules["staging_snapshot_cli"]
configuration = sys.modules["staging_snapshot_configuration"]
native = sys.modules["staging_snapshot_native_files"]
pg = sys.modules["staging_snapshot_pg_io"]
runtime = sys.modules["staging_snapshot_runtime"]
workspace = sys.modules["staging_snapshot_workspace"]

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
                timeout=20,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())

        setattr(cls, name, run)
    return cls


class SessionFixture(unittest.TestCase):
    def setUp(self):
        if FILES is None:
            return
        self.patches = []
        self.ci_parent = tempfile.TemporaryDirectory(
            prefix="snapshot-session-ci-parent-"
        )
        self.addCleanup(self.ci_parent.cleanup)
        fixture_parent = Path(self.ci_parent.name).resolve()
        # Always exercise a NONstandard parent, including on macOS. Both the
        # outer binder and reused pure path parser require the fixed literal.
        # Map only that literal in fixtures; production remains unchanged.
        for module in (workspace, workspace.workspace):
            self.start_patch(
                patch.object(
                    module,
                    "Path",
                    side_effect=lambda value: fixture_parent
                    if value == "/private/tmp"
                    else Path(value),
                )
            )
        self.temporary = tempfile.TemporaryDirectory(
            prefix="cosmaneura-staging-snapshot-", dir=fixture_parent
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.image, self.mount = (
            self.root / "staging.sparseimage",
            self.root / "mounted",
        )
        self.image.write_bytes(b"invented, NOT an encrypted image")
        self.image.chmod(0o600)
        self.mount.mkdir(mode=0o700)
        self.payload = self.mount / "payload"
        self.payload.mkdir(mode=0o700)
        self.marker = self.mount / ".metadata_never_index"
        self.marker.write_bytes(b"")
        self.marker.chmod(0o600)
        self.manifest = {
            "schema_version": 1,
            "empty_workspace_prepared": True,
            "source_sha256": "a" * 64,
            "requested_encryption": "AES-256",
            "workspace": str(self.root),
            "image": str(self.image),
            "mount": str(self.mount),
            "capacity_bytes": 8 * 1024**3,
            "device": "/dev/disk99s1",
            "image_device": self.image.stat().st_dev,
            "image_inode": self.image.stat().st_ino,
            "mount_device": self.mount.stat().st_dev,
            "mount_inode": self.mount.stat().st_ino,
            "payload_device": self.payload.stat().st_dev,
            "payload_inode": self.payload.stat().st_ino,
            "noindex_marker_inode": self.marker.stat().st_ino,
        }
        manifest_raw = json.dumps(self.manifest).encode()
        self.manifest_path = self.root / "empty-workspace.json"
        self.manifest_path.write_bytes(manifest_raw)
        self.manifest_path.chmod(0o400)
        # Exact production class, but no real runtime is attested in fixtures.
        self.runtime = object.__new__(runtime.SnapshotRuntime)
        self.runtime._root = self.root / "invented-runtime"
        self.scope = workspace.SnapshotWorkspace(
            FILES,
            self.runtime,
            self.root,
            hashlib.sha256(manifest_raw).hexdigest(),
            "a" * 64,
            "b" * 64,
            "c" * 64,
        )
        self.responses = {
            "encryption": {"encrypted": True},
            "images": {
                "images": [
                    {
                        "image-path": str(self.image),
                        "image-encrypted": True,
                        "writeable": True,
                        "owner-uid": os.geteuid(),
                        "blockcount": 8 * 1024**3 // 512,
                        "blocksize": 512,
                        "system-entities": [
                            {
                                "mount-point": str(self.mount),
                                "dev-entry": "/dev/disk99s1",
                            }
                        ],
                    }
                ]
            },
            "disk": {
                "MountPoint": str(self.mount),
                "DeviceNode": "/dev/disk99s1",
                "FilesystemType": "apfs",
                "Writable": True,
                "WritableMedia": True,
                "WritableVolume": True,
                "GlobalPermissionsEnabled": True,
            },
        }
        self.original_query = workspace.SnapshotWorkspace._query
        self.start_patch(
            patch.object(
                workspace.SnapshotWorkspace,
                "_query",
                lambda _self, operation, deadline: copy.deepcopy(
                    self.responses[operation]
                ),
            )
        )
        self.runtime_check = self.start_patch(
            patch.object(runtime.SnapshotRuntime, "revalidate")
        )
        self.start_patch(patch.object(workspace.SnapshotWorkspace, "_runtime_check"))
        self.start_patch(patch.object(workspace.os.path, "ismount", return_value=True))
        original_stat = Path.stat

        def fake_root_device(path, *args, **kwargs):
            value = original_stat(path, *args, **kwargs)
            # Only the explicit native-proof root.stat() comparison is mocked.
            # Path.lstat() delegates to stat(follow_symlinks=False): NEVER forge
            # the metadata used by the real protected descriptor reader.
            if path == self.root and kwargs.get("follow_symlinks", True):
                fields = list(value)
                fields[2] += 1
                return os.stat_result(fields)
            return value

        self.start_patch(patch.object(Path, "stat", fake_root_device))
        self.home = self.root / "invented-home"
        self.cache_dir = self.home / ".azure"
        self.cache_dir.mkdir(parents=True, mode=0o700)
        self.start_patch(
            patch.object(
                configuration.pwd,
                "getpwuid",
                return_value=SimpleNamespace(pw_dir=str(self.home)),
            )
        )
        self.contents = (
            b"invented-config",
            b"invented-clouds",
            b'{"inventedProfile":true}',
            b'{"inventedCache":true}',
        )
        for name, content in zip(configuration.names(), self.contents, strict=True):
            path = self.cache_dir / name
            path.write_bytes(content)
            path.chmod(0o600)
        original_sha = hashlib.sha256

        def fixture_config_pin(value):
            if value == self.contents[0]:
                return SimpleNamespace(
                    hexdigest=lambda: "b8258f4205b85a305ac3811432073f56fddf4a63b4472485778bfde0f5a69553"
                )
            if value == self.contents[1]:
                return SimpleNamespace(
                    hexdigest=lambda: "2189f3b4fe7357a59f5fcdf8f6e35382784aa2862b5262e5dc89d8e615bfc3ed"
                )
            return original_sha(value)

        # ONLY map the two invented fixture bodies while constructing the
        # fixture. Real configured-source pins are never read, altered or used.
        with patch.object(
            configuration.hashlib, "sha256", side_effect=fixture_config_pin
        ):
            self.source = configuration.ConfiguredSource(
                FILES, deadline=time.monotonic() + 5
            )

    def start_patch(self, value):
        result = value.start()
        self.addCleanup(value.stop)
        return result

    def area(self):
        return self.scope.open_area(deadline=time.monotonic() + 5)

    def private_copy(self, area):
        return self.source.private_copy(area, deadline=time.monotonic() + 5)

    def fake_cli(self):
        python = self.runtime.python
        python.parent.mkdir(parents=True)
        python.symlink_to(Path(sys.executable).resolve())
        package = self.runtime.site_packages / "azure" / "cli"
        package.mkdir(parents=True)
        self.fake_main = package / "__main__.py"
        self.fake_main.write_text(
            "import json,os,sys; print(json.dumps({'argv':sys.argv[1:],'home':os.environ['HOME'],'config':os.environ['AZURE_CONFIG_DIR'],'keys':sorted(os.environ)}))"
        )


@isolated_tests
class WorkspaceTests(SessionFixture):
    def test_fixture_native_observation_contract(self):
        with pg.capture_deadline(time.monotonic() + 5):
            observed = self.scope._observe(time.monotonic() + 4)
        self.assertEqual(observed, self.manifest)

    def test_bound_area_retains_exact_fds_and_closes_after_exit(self):
        with self.area() as area:
            self.assertEqual(area.mount_inode, self.manifest["mount_inode"])
            self.assertEqual(area.payload_inode, self.manifest["payload_inode"])
            self.assertEqual(pg.signal.getitimer(pg.signal.ITIMER_REAL), (0.0, 0.0))
            descriptors = (area.mount_fd, area.payload_fd)
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_wrong_manifest_refuses_without_any_write(self):
        self.scope._manifest_sha = "0" * 64
        with patch.object(workspace.os, "write") as write:
            with self.assertRaises(workspace.SnapshotWorkspaceRefused):
                with self.area():
                    self.fail("must refuse")
        write.assert_not_called()

    def test_unencrypted_image_wrong_disk_or_extra_mount_refuse(self):
        for kind in ("encryption", "disk", "images"):
            saved = copy.deepcopy(self.responses)
            if kind == "encryption":
                self.responses["images"]["images"][0]["image-encrypted"] = False
            elif kind == "disk":
                self.responses[kind]["FilesystemType"] = "hfs"
            else:
                self.responses[kind]["images"].append(
                    copy.deepcopy(self.responses[kind]["images"][0])
                )
            with (
                self.subTest(kind=kind),
                self.assertRaises(workspace.SnapshotWorkspaceRefused),
            ):
                with self.area():
                    self.fail("must refuse")
            self.responses = saved

    def test_nonempty_payload_and_replaced_marker_refuse(self):
        extra = self.payload / "already-there"
        extra.write_bytes(b"invented")
        with self.assertRaises(workspace.SnapshotWorkspaceRefused):
            with self.area():
                self.fail("must refuse")
        extra.unlink()
        self.marker.rename(self.mount / "old-marker")
        self.marker.write_bytes(b"")
        with self.assertRaises(workspace.SnapshotWorkspaceRefused):
            with self.area():
                self.fail("must refuse")

    def test_replaced_payload_refuses_using_held_descriptor(self):
        with self.area() as area:
            self.payload.rename(self.mount / "old-payload")
            self.payload.mkdir(mode=0o700)
            with self.assertRaises(workspace.SnapshotWorkspaceRefused):
                with pg.capture_deadline(time.monotonic() + 5):
                    self.scope.revalidate(area, deadline=time.monotonic() + 4)

    def test_native_commands_are_closed_and_only_read_queries(self):
        calls = []

        def run(_children, argv, env, maximum, sink, **kwargs):
            calls.append(argv)
            self.assertEqual(maximum, 2 * 1024**2)
            sink(plistlib.dumps({"invented": True}))

        with (
            patch.object(workspace.SnapshotWorkspace, "_query", self.original_query),
            patch.object(pg.BoundedChildren, "run", run),
        ):
            with pg.capture_deadline(time.monotonic() + 5):
                for operation in ("images", "disk"):
                    self.scope._query(operation, time.monotonic() + 4)
                for operation in ("encryption", "attach"):
                    with self.assertRaises(workspace.SnapshotWorkspaceRefused):
                        self.scope._query(operation, time.monotonic() + 4)
        self.assertEqual(
            calls,
            [
                ["/usr/bin/hdiutil", "info", "-plist"],
                ["/usr/sbin/diskutil", "info", "-plist", str(self.mount)],
            ],
        )


@isolated_tests
class ConfigurationTests(SessionFixture):
    def test_copied_static_configuration_drift_refuses(self):
        with self.area() as area, self.private_copy(area) as copied:
            for name, original in zip(
                configuration.names()[:3], self.contents[:3], strict=True
            ):
                with self.subTest(name=name):
                    path = copied.path / name
                    path.write_bytes(b"invented changed config or selection")
                    with self.assertRaises(configuration.SnapshotConfigurationRefused):
                        with pg.capture_deadline(time.monotonic() + 5):
                            self.source.validate_copy(
                                copied, deadline=time.monotonic() + 4
                            )
                    path.write_bytes(original)

    def test_missing_marker_after_binding_refuses_before_config_copy(self):
        with self.area() as area:
            self.marker.unlink()
            with self.assertRaises(configuration.SnapshotConfigurationRefused):
                with self.private_copy(area):
                    self.fail("must refuse")
            self.assertFalse(list(self.mount.glob(".azure-cli-session-*")))

    def test_private_copy_preserves_original_and_is_outside_payload(self):
        with self.area() as area:
            with self.private_copy(area) as copied:
                self.assertEqual(copied.path.parent, self.mount)
                self.assertFalse(copied.path.is_relative_to(self.payload))
                for name, content in zip(
                    configuration.names(), self.contents, strict=True
                ):
                    path = copied.path / name
                    self.assertEqual(path.read_bytes(), content)
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual((self.cache_dir / name).read_bytes(), content)
                descriptor = copied.directory_fd
            self.assertTrue(copied.path.is_dir())  # Deliberately preserved.
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_changed_original_refuses_before_creating_any_copy(self):
        with self.area() as area:
            (self.cache_dir / "msal_token_cache.json").write_bytes(b'{"changed":true}')
            with self.assertRaises(configuration.SnapshotConfigurationRefused):
                with self.private_copy(area):
                    self.fail("must refuse")
            self.assertFalse(list(self.mount.glob(".azure-cli-session-*")))

    def test_insecure_umask_refuses_without_changing_caller_setting(self):
        with self.area() as area:
            previous = os.umask(0o022)
            try:
                with self.assertRaises(configuration.SnapshotConfigurationRefused):
                    with self.private_copy(area):
                        self.fail("must refuse")
                self.assertEqual(os.umask(0o022), 0o022)
            finally:
                os.umask(previous)

    def test_copied_files_are_0600_from_first_write(self):
        real_write = os.write

        def writing(descriptor, data):
            self.assertEqual(os.fstat(descriptor).st_mode & 0o777, 0o600)
            return real_write(descriptor, data)

        with (
            self.area() as area,
            patch.object(configuration.os, "write", side_effect=writing),
        ):
            with self.private_copy(area):
                pass

    def test_partial_copy_preserved_and_error_has_no_detail(self):
        with (
            self.area() as area,
            patch.object(
                configuration.os, "write", side_effect=OSError("invented secret detail")
            ),
        ):
            with self.assertRaisesRegex(
                configuration.SnapshotConfigurationRefused,
                "^snapshot_configuration_refused$",
            ):
                with self.private_copy(area):
                    self.fail("must refuse")
        copies = list(self.mount.glob(".azure-cli-session-*"))
        self.assertEqual(len(copies), 1)
        self.assertEqual((copies[0] / "config").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.cache_dir / "config").read_bytes(), self.contents[0])

    def test_extension_injection_or_symlink_copy_refuses(self):
        with self.area() as area, self.private_copy(area) as copied:
            extra = copied.path / "empty-extensions" / "injected"
            extra.write_bytes(b"invented")
            with self.assertRaises(configuration.SnapshotConfigurationRefused):
                with pg.capture_deadline(time.monotonic() + 5):
                    self.source.validate_copy(copied, deadline=time.monotonic() + 4)
            extra.unlink()
            file = copied.path / "config"
            file.rename(copied.path / "original-config")
            file.symlink_to(copied.path / "original-config")
            with self.assertRaises(configuration.SnapshotConfigurationRefused):
                with pg.capture_deadline(time.monotonic() + 5):
                    self.source.validate_copy(copied, deadline=time.monotonic() + 4)

    def test_unbound_object_refuses_before_filesystem_access(self):
        with patch.object(configuration.os, "mkdir") as mkdir:
            with self.assertRaises(configuration.SnapshotConfigurationRefused):
                with self.source.private_copy(
                    cast(Any, SimpleNamespace()), deadline=time.monotonic() + 4
                ):
                    self.fail("must refuse")
        mkdir.assert_not_called()


@isolated_tests
class CLITests(SessionFixture):
    def setUp(self):
        super().setUp()
        if FILES is not None:
            self.fake_cli()

    def test_four_closed_commands_use_private_copy_and_no_ambient_values(self):
        with self.area() as area, self.private_copy(area) as copied:
            driver = cli.OwnedAzureCLI(self.runtime, copied)
            for operation in ("account", "token", "version", "cloud"):
                with patch.dict(
                    os.environ, {"HTTP_PROXY": "invented", "PGPASSWORD": "invented"}
                ):
                    value = driver.read(operation, deadline=time.monotonic() + 5)
                self.assertEqual(
                    value["argv"],
                    [
                        *cli.arguments(operation),
                        "--only-show-errors",
                        "--output",
                        "json",
                    ],
                )
                self.assertEqual(value["home"], "/dev/null")
                self.assertEqual(value["config"], str(copied.path))
                self.assertNotIn("HTTP_PROXY", value["keys"])
                self.assertNotIn("PGPASSWORD", value["keys"])

    def test_unknown_cli_commands_do_not_verify_or_spawn(self):
        with self.area() as area, self.private_copy(area) as copied:
            driver = cli.OwnedAzureCLI(self.runtime, copied)
            with (
                patch.object(cli.OwnedAzureCLI, "_verify") as verify,
                patch.object(pg.subprocess, "Popen") as spawn,
            ):
                for command in (
                    "login",
                    "delete",
                    "account/set",
                    "postgres flexible-server firewall-rule create",
                ):
                    with self.assertRaises(cli.SnapshotCLIRefused):
                        driver.read(command, deadline=time.monotonic() + 1)
            verify.assert_not_called()
            spawn.assert_not_called()

    def test_changed_configured_source_refuses_before_child(self):
        with self.area() as area, self.private_copy(area) as copied:
            driver = cli.OwnedAzureCLI(self.runtime, copied)
            (self.cache_dir / "azureProfile.json").write_bytes(b'{"changed":true}')
            with patch.object(pg.subprocess, "Popen") as spawn:
                with self.assertRaises(cli.SnapshotCLIRefused):
                    driver.read("account", deadline=time.monotonic() + 2)
            spawn.assert_not_called()

    def test_replaced_runtime_object_cannot_select_an_executable(self):
        with self.area() as area, self.private_copy(area) as copied:
            driver = cli.OwnedAzureCLI(self.runtime, copied)
            driver._runtime = cast(
                Any, SimpleNamespace(python="/bin/sh", revalidate=lambda: None)
            )
            with patch.object(pg.subprocess, "Popen") as spawn:
                with self.assertRaises(cli.SnapshotCLIRefused):
                    driver.read("account", deadline=time.monotonic() + 3)
            spawn.assert_not_called()

    def test_duplicate_json_output_and_runtime_failure_refuse(self):
        with self.area() as area, self.private_copy(area) as copied:
            driver = cli.OwnedAzureCLI(self.runtime, copied)
            self.fake_main.write_text('print(\'{"duplicate":1,"duplicate":2}\')')
            with self.assertRaises(cli.SnapshotCLIRefused):
                driver.read("version", deadline=time.monotonic() + 3)
            self.runtime_check.side_effect = RuntimeError("invented private detail")
            with patch.object(pg.subprocess, "Popen") as spawn:
                with self.assertRaisesRegex(
                    cli.SnapshotCLIRefused, "^snapshot_cli_refused$"
                ):
                    driver.read("account", deadline=time.monotonic() + 3)
            spawn.assert_not_called()

    def test_real_silent_fake_cli_timeout_reaps_owned_child(self):
        with self.area() as area, self.private_copy(area) as copied:
            driver = cli.OwnedAzureCLI(self.runtime, copied)
            self.fake_main.write_text("import time;time.sleep(5)")
            original_spawn, children = subprocess.Popen, []

            def spawn(argv, **kwargs):
                child = original_spawn(argv, **kwargs)
                if argv[0] == str(self.runtime.python):
                    children.append(child)
                return child

            with patch.object(pg.subprocess, "Popen", side_effect=spawn):
                with self.assertRaises(cli.SnapshotCLIRefused):
                    driver.read("account", deadline=time.monotonic() + 0.15)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].returncode)


@isolated_tests
class RootBoundaryTests(unittest.TestCase):
    def test_unmapped_production_root_refuses_other_paths(self):
        assert FILES is not None
        bound_runtime = object.__new__(runtime.SnapshotRuntime)
        for path in (
            Path("/tmp/cosmaneura-staging-snapshot-abcdefgh"),
            Path("/private/tmp/not-the-approved-prefix"),
            Path("/private/tmp/cosmaneura-staging-snapshot-abcdefgh/child"),
        ):
            with (
                self.subTest(path=path),
                self.assertRaises(workspace.SnapshotWorkspaceRefused),
            ):
                workspace.SnapshotWorkspace(
                    FILES, bound_runtime, path, "a" * 64, "b" * 64, "c" * 64, "d" * 64
                )


if __name__ == "__main__":
    assert callable(atexit.register)
    locale.normalize("C")
    os.umask(0o077)
    pg.resource.setrlimit(pg.resource.RLIMIT_CORE, (0, 0))
    FILES = native.NativeFiles()
    pg.seal_capture_process()
    unittest.main()
