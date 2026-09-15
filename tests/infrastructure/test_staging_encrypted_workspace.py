"""Secretless native-metadata fixtures; never create or mount a disk image."""

import hashlib
import importlib.util
import json
import os
import plistlib
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/staging_encrypted_workspace.py"
SPEC = importlib.util.spec_from_file_location("staging_encrypted_workspace", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
workspace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workspace)

ROOT = Path("/private/tmp/cosmaneura-staging-snapshot-test1234")
IMAGE = ROOT / "staging.sparseimage"
MOUNT = ROOT / "mounted"
DEVICE = "/dev/disk77s1"


def image_info():
    return {
        "images": [
            {
                "image-path": str(IMAGE),
                "image-encrypted": True,
                "writeable": True,
                "owner-uid": os.geteuid(),
                "blockcount": workspace.SIZE_BYTES // 512,
                "blocksize": 512,
                "system-entities": [
                    {"dev-entry": "/dev/disk76"},
                    {"dev-entry": DEVICE, "mount-point": str(MOUNT)},
                ],
            }
        ]
    }


def disk_info():
    return {
        "MountPoint": str(MOUNT),
        "DeviceNode": DEVICE,
        "FilesystemType": "apfs",
        "Writable": True,
        "WritableMedia": True,
        "WritableVolume": True,
        "GlobalPermissionsEnabled": True,
    }


class EncryptedWorkspaceTests(unittest.TestCase):
    def test_existing_binding_rejects_missing_duplicate_and_wrong_types(self):
        value = {
            "workspace": str(ROOT),
            "device": DEVICE,
            "root_device": 4,
            "root_inode": 5,
            "image_device": 4,
            "image_inode": 123,
            "mount_device": 8,
            "mount_inode": 20,
        }
        self.assertEqual(workspace.existing_binding(json.dumps(value)), value)
        for key in value:
            changed = dict(value)
            del changed[key]
            with self.subTest(key=key), self.assertRaises(workspace.WorkspaceError):
                workspace.existing_binding(json.dumps(changed))
        for value_type in (True, 0, -1, "8", None):
            with (
                self.subTest(value=value_type),
                self.assertRaises(workspace.WorkspaceError),
            ):
                workspace.existing_binding(
                    json.dumps(value | {"mount_device": value_type})
                )
        with self.assertRaises(workspace.WorkspaceError):
            workspace.existing_binding('{"workspace":"one","workspace":"two"}')

    def test_existing_observation_requires_exact_empty_root_and_live_binding(self):
        binding = {
            "workspace": str(ROOT),
            "device": DEVICE,
            "root_device": 4,
            "root_inode": 5,
            "image_device": 4,
            "image_inode": 123,
            "mount_device": 8,
            "mount_inode": 20,
        }
        metadata = {
            ROOT: SimpleNamespace(st_dev=4, st_ino=5),
            IMAGE: SimpleNamespace(st_dev=4, st_ino=123),
            MOUNT: SimpleNamespace(st_dev=8, st_ino=20),
        }

        def native(argv):
            if argv == [workspace.HDIUTIL, "info", "-plist"]:
                return plistlib.dumps(image_info())
            if argv == [workspace.DISKUTIL, "info", "-plist", str(MOUNT)]:
                return plistlib.dumps(disk_info())
            self.fail("unexpected native operation")

        with (
            patch.object(workspace, "workspace_paths", return_value=(IMAGE, MOUNT)),
            patch.object(
                workspace, "private_path", side_effect=lambda path, **kw: metadata[path]
            ),
            patch.object(workspace.os.path, "ismount", return_value=True),
            patch.object(
                workspace.os,
                "listdir",
                return_value=[workspace.IMAGE_NAME, workspace.MOUNT_NAME],
            ) as listing,
            patch.object(workspace, "run_native", side_effect=native),
        ):
            self.assertIs(
                workspace.observe_existing_empty(ROOT, binding), metadata[MOUNT]
            )
            with self.assertRaises(workspace.WorkspaceError):
                workspace.observe_existing_empty(ROOT, binding | {"image_inode": 124})
            listing.return_value.append(workspace.MANIFEST_NAME)
            with self.assertRaises(workspace.WorkspaceError):
                workspace.observe_existing_empty(ROOT, binding)

    def test_complete_empty_is_human_only_before_any_read_or_write(self):
        with (
            patch.object(workspace.os, "isatty", return_value=False),
            patch.object(workspace, "checked_source") as source,
            patch.object(workspace, "observe_existing_empty") as observe,
        ):
            with self.assertRaises(workspace.WorkspaceError):
                workspace.complete_empty(ROOT, "a" * 64, {})
            source.assert_not_called()
            observe.assert_not_called()

    def test_complete_empty_reuses_held_fixture_mount_and_publishes_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            mount, image = root / workspace.MOUNT_NAME, root / workspace.IMAGE_NAME
            mount.mkdir(mode=0o755)
            mount.chmod(0o755)
            image.write_bytes(b"INVENTED NOT ENCRYPTED")
            image.chmod(0o600)
            binding: dict[str, Any] = {"workspace": str(root), "device": DEVICE}
            for name, path in (("root", root), ("image", image), ("mount", mount)):
                binding[name + "_device"], binding[name + "_inode"] = (
                    path.stat().st_dev,
                    path.stat().st_ino,
                )
            observed = mount.stat()
            evidence = {
                **binding,
                "image": str(image),
                "mount": str(mount),
                "capacity_bytes": workspace.SIZE_BYTES,
            }
            with (
                patch.object(workspace.os, "isatty", return_value=True),
                patch.object(workspace, "checked_source", return_value="a" * 64),
                patch.object(
                    workspace, "observe_existing_empty", return_value=observed
                ),
                patch.object(workspace, "workspace_paths", return_value=(image, mount)),
                patch.object(
                    workspace, "initialize_empty_area", return_value=evidence
                ) as initialize,
                patch.object(workspace, "run_native") as native,
            ):
                result = workspace.complete_empty(root, "a" * 64, binding)
            native.assert_not_called()
            initialize.assert_called_once_with(root, expected_binding=binding)
            self.assertEqual(mount.stat().st_mode & 0o777, 0o700)
            self.assertTrue(result["empty_workspace_prepared"])
            self.assertFalse(result["image_created_in_this_process"])
            self.assertEqual(result["setup_operation"], "complete-empty")
            self.assertFalse(result["live_data_copied"])
            manifest = root / workspace.MANIFEST_NAME
            self.assertEqual(manifest.stat().st_mode & 0o777, 0o400)
            self.assertEqual(json.loads(manifest.read_bytes()), result)

    def test_complete_empty_refuses_nonempty_changed_inode_or_source_before_write(self):
        for failure in ("nonempty", "inode", "source"):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary).resolve()
                mount = root / workspace.MOUNT_NAME
                mount.mkdir(mode=0o755)
                mount.chmod(0o755)
                image = root / workspace.IMAGE_NAME
                observed = mount.stat()
                binding = {
                    "workspace": str(root),
                    "device": DEVICE,
                    "root_device": 1,
                    "root_inode": 2,
                    "image_device": 1,
                    "image_inode": 3,
                    "mount_device": observed.st_dev,
                    "mount_inode": observed.st_ino,
                }
                if failure == "nonempty":
                    (mount / "keep").write_bytes(b"INVENTED DATA MUST REMAIN")
                if failure == "inode":
                    observed = SimpleNamespace(
                        st_dev=observed.st_dev, st_ino=observed.st_ino + 1
                    )
                checks = (
                    ["a" * 64, "b" * 64]
                    if failure == "source"
                    else ["a" * 64, "a" * 64]
                )
                with (
                    patch.object(workspace.os, "isatty", return_value=True),
                    patch.object(workspace, "checked_source", side_effect=checks),
                    patch.object(
                        workspace, "observe_existing_empty", return_value=observed
                    ),
                    patch.object(
                        workspace, "workspace_paths", return_value=(image, mount)
                    ),
                    patch.object(workspace.os, "fchmod") as chmod,
                    patch.object(workspace, "initialize_empty_area") as initialize,
                ):
                    with self.assertRaises(workspace.WorkspaceError):
                        workspace.complete_empty(root, "a" * 64, binding)
                chmod.assert_not_called()
                initialize.assert_not_called()
                self.assertEqual(mount.stat().st_mode & 0o777, 0o755)
                self.assertFalse((root / workspace.MANIFEST_NAME).exists())

    def test_expected_binding_checked_before_empty_area_write(self):
        with (
            patch.object(workspace, "verify_mounted", return_value={"image_device": 1}),
            patch.object(workspace, "open_bound_mount") as opening,
        ):
            with self.assertRaises(workspace.WorkspaceError):
                workspace.initialize_empty_area(
                    ROOT, expected_binding={"image_device": 2}
                )
            opening.assert_not_called()

    def test_completion_initialization_rechecks_empty_held_mount_before_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            mount = Path(temporary)
            (mount / "keep").write_bytes(b"INVENTED DATA MUST REMAIN")
            binding = dict.fromkeys(
                (
                    "image_device",
                    "image_inode",
                    "mount_device",
                    "mount_inode",
                    "device",
                ),
                1,
            )
            fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
            with (
                patch.object(workspace, "verify_mounted", return_value=binding),
                patch.object(workspace, "open_bound_mount", return_value=fd),
            ):
                with self.assertRaises(workspace.WorkspaceError):
                    workspace.initialize_empty_area(ROOT, expected_binding=binding)
            self.assertEqual(list(mount.iterdir()), [mount / "keep"])
            self.assertEqual(
                (mount / "keep").read_bytes(), b"INVENTED DATA MUST REMAIN"
            )

    def test_native_metadata_collections_have_required_shapes(self):
        for value in (None, "images", {}, [None], ["image"]):
            with self.subTest(value=value), self.assertRaises(workspace.WorkspaceError):
                workspace.image_binding({"images": value}, IMAGE, MOUNT)
        for value in (None, "entities", {}, [None], ["entity"]):
            info = image_info()
            info["images"][0]["system-entities"] = value
            with self.subTest(value=value), self.assertRaises(workspace.WorkspaceError):
                workspace.image_binding(info, IMAGE, MOUNT)

    def test_manifest_stability_ignores_only_access_time(self):
        original = dict(
            st_dev=1,
            st_ino=2,
            st_uid=501,
            st_gid=20,
            st_mode=0o100400,
            st_nlink=1,
            st_size=1234,
            st_mtime_ns=567,
            st_ctime_ns=890,
            st_atime_ns=100,
        )
        changed = {**original, "st_atime_ns": 101}
        self.assertTrue(
            workspace.stable_file(
                SimpleNamespace(**original), SimpleNamespace(**changed)
            )
        )
        for key in original:
            if key == "st_atime_ns":
                continue
            changed = {**original, key: original[key] + 1}
            with self.subTest(field=key):
                self.assertFalse(
                    workspace.stable_file(
                        SimpleNamespace(**original), SimpleNamespace(**changed)
                    )
                )

    def test_exact_image_mount_binding(self):
        self.assertEqual(workspace.image_binding(image_info(), IMAGE, MOUNT), DEVICE)
        workspace.check_disk(disk_info(), MOUNT, DEVICE)

    def test_unencrypted_or_wrong_owner_or_shadow_refused(self):
        for key, value in (
            ("image-encrypted", False),
            ("image-encrypted", 1),
            ("writeable", False),
            ("owner-uid", -1),
            ("shadow-path", "/tmp/plaintext.shadow"),
        ):
            with self.subTest(key=key, value=value):
                info = image_info()
                info["images"][0][key] = value
                with self.assertRaises(workspace.WorkspaceError):
                    workspace.image_binding(info, IMAGE, MOUNT)

    def test_capacity_requires_exact_eight_gibibytes(self):
        for value in (True, 0, -1, "16777216", 16777215, 16777217):
            with self.subTest(value=value):
                info = image_info()
                info["images"][0]["blockcount"] = value
                with self.assertRaises(workspace.WorkspaceError):
                    workspace.image_binding(info, IMAGE, MOUNT)

    def test_missing_and_ambiguous_image_refused(self):
        for images in ([], image_info()["images"] * 2):
            with self.assertRaises(workspace.WorkspaceError):
                workspace.image_binding({"images": images}, IMAGE, MOUNT)

    def test_mount_alias_and_extra_volume_refused(self):
        for entities in (
            [{"dev-entry": DEVICE, "mount-point": str(MOUNT) + "/../mounted"}],
            [
                {"dev-entry": DEVICE, "mount-point": str(MOUNT)},
                {"dev-entry": "/dev/disk77s2", "mount-point": "/Volumes/extra"},
            ],
        ):
            info = image_info()
            info["images"][0]["system-entities"] = entities
            with self.assertRaises(workspace.WorkspaceError):
                workspace.image_binding(info, IMAGE, MOUNT)

    def test_other_image_claiming_device_or_mount_refused(self):
        for entity in ({"dev-entry": DEVICE}, {"mount-point": str(MOUNT)}):
            info = image_info()
            info["images"].append(
                {"image-path": "/tmp/other.sparseimage", "system-entities": [entity]}
            )
            with self.assertRaises(workspace.WorkspaceError):
                workspace.image_binding(info, IMAGE, MOUNT)

    def test_device_path_is_not_arbitrary_command_input(self):
        for device in ("disk77s1", "/dev/disk77", "/dev/disk77s1;echo", None):
            info = image_info()
            info["images"][0]["system-entities"][-1]["dev-entry"] = device
            with self.assertRaises(workspace.WorkspaceError):
                workspace.image_binding(info, IMAGE, MOUNT)

    def test_disk_wrong_filesystem_device_mount_and_noowners_refused(self):
        for key, value in (
            ("MountPoint", "/tmp/fallback"),
            ("DeviceNode", "/dev/disk88s1"),
            ("FilesystemType", "hfs"),
            ("ReadOnlyVolume", True),
            ("GlobalPermissionsEnabled", False),
        ):
            info = disk_info()
            info[key] = value
            with self.assertRaises(workspace.WorkspaceError):
                workspace.check_disk(info, MOUNT, DEVICE)

    def test_missing_native_boolean_does_not_mean_safe(self):
        for key in disk_info():
            info = disk_info()
            del info[key]
            with self.assertRaises(workspace.WorkspaceError):
                workspace.check_disk(info, MOUNT, DEVICE)

    def test_observed_macos_positive_writability_and_conflicts(self):
        workspace.check_disk(disk_info(), MOUNT, DEVICE)
        for key in ("Writable", "WritableMedia", "WritableVolume"):
            for value in (False, None, 1, "true"):
                with (
                    self.subTest(key=key, value=value),
                    self.assertRaises(workspace.WorkspaceError),
                ):
                    workspace.check_disk(disk_info() | {key: value}, MOUNT, DEVICE)
        for key in ("ReadOnly", "ReadOnlyMedia", "ReadOnlyVolume"):
            workspace.check_disk(disk_info() | {key: False}, MOUNT, DEVICE)
            for value in (True, None, 0, "false"):
                with (
                    self.subTest(key=key, value=value),
                    self.assertRaises(workspace.WorkspaceError),
                ):
                    workspace.check_disk(disk_info() | {key: value}, MOUNT, DEVICE)

    def test_mounted_verification_uses_bound_native_encryption_not_backing_open(self):
        def native(argv):
            if argv == [workspace.HDIUTIL, "info", "-plist"]:
                return plistlib.dumps(image_info())
            if argv == [workspace.DISKUTIL, "info", "-plist", str(MOUNT)]:
                return plistlib.dumps(disk_info())
            self.fail("unexpected native operation: " + repr(argv))

        def metadata(path, **kwargs):
            return SimpleNamespace(
                st_dev=8 if path == MOUNT else 4, st_ino=20 if path == MOUNT else 123
            )

        with (
            patch.object(workspace, "workspace_paths", return_value=(IMAGE, MOUNT)),
            patch.object(workspace, "private_path", side_effect=metadata),
            patch.object(Path, "stat", return_value=SimpleNamespace(st_dev=4)),
            patch.object(workspace.os.path, "ismount", return_value=True),
            patch.object(workspace, "run_native", side_effect=native) as run,
        ):
            result = workspace.verify_mounted(ROOT)
        self.assertTrue(result["encrypted_image_bound_to_mounted_apfs"])
        self.assertEqual(len(run.call_args_list), 2)

    def test_native_plist_has_mapping_root(self):
        self.assertEqual(
            workspace.parse_plist(plistlib.dumps(disk_info())), disk_info()
        )
        with self.assertRaises(workspace.WorkspaceError):
            workspace.parse_plist(plistlib.dumps([]))

    def test_commands_are_bounded_and_no_password_argument(self):
        create = workspace.create_command(IMAGE)
        attach = workspace.attach_command(IMAGE, MOUNT)
        self.assertEqual(create[0], "/usr/bin/hdiutil")
        self.assertEqual(create[create.index("-size") + 1], "8g")
        self.assertEqual(create[create.index("-encryption") + 1], "AES-256")
        self.assertEqual(create[create.index("-fs") + 1], "APFS")
        self.assertEqual(attach[attach.index("-owners") + 1], "on")
        for command in (create, attach):
            self.assertIn("-stdinpass", command)
            for forbidden in (
                "-ov",
                "-shadow",
                "-passphrase",
                "-recover",
                "-pubkey",
                "-certificate",
            ):
                self.assertNotIn(forbidden, command)
        self.assertIn("-nospotlight", create)
        self.assertIn("-noautoopen", attach)
        self.assertIn("-nobrowse", attach)

    def test_native_process_uses_clean_environment_and_direct_tty(self):
        with patch.object(workspace.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b""
            workspace.run_native(workspace.create_command(IMAGE), interactive=True)
            kwargs = run.call_args.kwargs
            self.assertIsNone(kwargs["stdin"])
            self.assertIsNone(kwargs["stderr"])
            self.assertEqual(set(kwargs["env"]), {"PATH", "LC_ALL", "LANG"})
            self.assertNotIn("input", kwargs)
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["timeout"], 300)

    def test_native_failure_never_returns_output_as_success(self):
        with patch.object(workspace.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = b"failure is not evidence"
            with self.assertRaises(workspace.WorkspaceError):
                workspace.run_native([workspace.HDIUTIL, "info", "-plist"])

    def test_private_file_symlink_hardlink_and_unsafe_mode_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "fixture"
            path.touch(mode=0o600)
            workspace.private_path(path, directory=False, mode=0o600)
            alias = root / "alias"
            alias.symlink_to(path)
            with self.assertRaises(workspace.WorkspaceError):
                workspace.private_path(alias, directory=False, mode=0o600)
            path.chmod(0o644)
            with self.assertRaises(workspace.WorkspaceError):
                workspace.private_path(path, directory=False, mode=0o600)
            path.chmod(0o600)
            os.link(path, root / "hardlink")
            with self.assertRaises(workspace.WorkspaceError):
                workspace.private_path(path, directory=False, mode=0o600)

    def test_arbitrary_destination_and_path_traversal_refused(self):
        for path in (
            Path("/Users/example/Desktop/staging"),
            ROOT / "..",
            Path("/tmp") / ROOT.name,
            ROOT.with_name(ROOT.name + "extra"),
        ):
            with self.assertRaises(workspace.WorkspaceError):
                workspace.workspace_paths(path)

    def test_prepare_without_tty_stops_before_any_creation(self):
        with (
            patch.object(
                workspace.sys,
                "argv",
                [str(SCRIPT), "prepare-empty", "--expected-source-sha256", "a" * 64],
            ),
            patch.object(workspace.sys, "platform", "darwin"),
            patch.object(workspace.sys, "flags") as flags,
            patch.object(workspace.os, "geteuid", return_value=501),
            patch.object(workspace.os, "isatty", return_value=False),
            patch.object(workspace, "prepare_empty") as prepare,
        ):
            flags.isolated = 1
            flags.no_site = 1
            flags.optimize = 0
            with self.assertRaises(workspace.WorkspaceError):
                workspace.main()
            prepare.assert_not_called()

    def test_verification_requires_image_inode_and_live_mount_match(self):
        original = {
            "workspace": str(ROOT),
            "image": str(IMAGE),
            "mount": str(MOUNT),
            "device": DEVICE,
            "image_inode": 123,
            "image_device": 4,
            "mount_device": 8,
            "mount_inode": 20,
            "payload_device": 8,
            "payload_inode": 21,
            "noindex_marker_inode": 22,
            "capacity_bytes": workspace.SIZE_BYTES,
            "empty_workspace_prepared": True,
            "source_sha256": workspace.hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        }
        raw = workspace.canonical(original)
        expected = workspace.hashlib.sha256(raw).hexdigest()
        for changed in (
            None,
            "image_inode",
            "mount_device",
            "device",
            "mount_inode",
            "payload_device",
            "payload_inode",
            "noindex_marker_inode",
        ):
            with self.subTest(changed=changed):
                current = deepcopy(original)
                if changed:
                    current[changed] = "changed"
                with (
                    patch.object(workspace, "workspace_paths"),
                    patch.object(workspace, "private_path") as metadata,
                    patch.object(
                        workspace,
                        "checked_source",
                        return_value=original["source_sha256"],
                    ),
                    patch.object(
                        workspace.Path,
                        "read_bytes",
                        return_value=raw,
                    ),
                    patch.object(workspace, "verify_empty", return_value=current),
                ):
                    metadata.return_value.st_size = len(raw)
                    if changed:
                        with self.assertRaises(workspace.WorkspaceError):
                            workspace.verify_prepared(
                                ROOT, expected, original["source_sha256"]
                            )
                    else:
                        result = workspace.verify_prepared(
                            ROOT, expected, original["source_sha256"]
                        )
                        self.assertEqual(result["manifest_sha256"], expected)
                        self.assertTrue(
                            result["not_a_receipt_or_permanent_readiness_proof"]
                        )

    def test_marker_and_empty_payload_are_on_expected_device(self):
        with tempfile.TemporaryDirectory() as temporary:
            mount = Path(temporary).resolve()
            (mount / workspace.MARKER_NAME).touch(mode=0o600)
            (mount / workspace.PAYLOAD_NAME).mkdir(mode=0o700)
            fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
            try:
                area = workspace.empty_area(fd, mount.stat().st_dev)
                self.assertEqual(
                    area["payload_inode"],
                    (mount / workspace.PAYLOAD_NAME).stat().st_ino,
                )
                with self.assertRaises(workspace.WorkspaceError):
                    workspace.empty_area(fd, mount.stat().st_dev + 1)
            finally:
                os.close(fd)

    def test_removed_changed_or_aliased_noindex_marker_refused(self):
        for mode in ("missing", "content", "symlink", "hardlink", "permissions"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                mount = Path(temporary).resolve()
                marker = mount / workspace.MARKER_NAME
                (mount / workspace.PAYLOAD_NAME).mkdir(mode=0o700)
                if mode != "missing":
                    marker.touch(mode=0o600)
                if mode == "content":
                    marker.write_bytes(b"invented")
                elif mode == "symlink":
                    marker.rename(mount / "target")
                    marker.symlink_to(mount / "target")
                elif mode == "hardlink":
                    os.link(marker, mount / "hardlink")
                elif mode == "permissions":
                    marker.chmod(0o644)
                fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with self.assertRaises((workspace.WorkspaceError, OSError)):
                        workspace.empty_area(fd, mount.stat().st_dev)
                finally:
                    os.close(fd)

    def test_nonempty_and_symlink_payload_refused(self):
        for mode in ("file", "directory", "symlink"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                mount = Path(temporary).resolve()
                (mount / workspace.MARKER_NAME).touch(mode=0o600)
                payload = mount / workspace.PAYLOAD_NAME
                payload.mkdir(mode=0o700)
                if mode == "file":
                    (payload / "invented").touch(mode=0o600)
                elif mode == "directory":
                    (payload / "invented").mkdir(mode=0o700)
                else:
                    payload.rename(mount / "other")
                    payload.symlink_to(mount / "other")
                fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with self.assertRaises((workspace.WorkspaceError, OSError)):
                        workspace.empty_area(fd, mount.stat().st_dev)
                finally:
                    os.close(fd)

    def test_unmount_before_marker_creation_cannot_write_plaintext_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            mount = Path(temporary).resolve()
            mounted = {
                "mount": str(mount),
                "mount_device": mount.stat().st_dev + 1,
                "mount_inode": mount.stat().st_ino,
            }
            with patch.object(workspace, "verify_mounted", return_value=mounted):
                with self.assertRaisesRegex(workspace.WorkspaceError, "mount_replaced"):
                    workspace.initialize_empty_area(mount.parent)
            self.assertEqual(list(mount.iterdir()), [])

    def test_verify_rechecks_mount_after_empty_area_inspection(self):
        with tempfile.TemporaryDirectory() as temporary:
            mount = Path(temporary).resolve()
            (mount / workspace.MARKER_NAME).touch(mode=0o600)
            (mount / workspace.PAYLOAD_NAME).mkdir(mode=0o700)
            mounted = {
                "mount": str(mount),
                "mount_device": mount.stat().st_dev,
                "mount_inode": mount.stat().st_ino,
                "image_device": 1,
                "image_inode": 5,
                "device": DEVICE,
            }
            with patch.object(
                workspace,
                "verify_mounted",
                side_effect=[
                    mounted,
                    {**mounted, "mount_inode": mounted["mount_inode"] + 1},
                ],
            ):
                with self.assertRaisesRegex(workspace.WorkspaceError, "mount_changed"):
                    workspace.verify_empty(mount.parent)

    def test_wrong_source_hash_refuses_before_directory_or_native_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary).resolve() / "source.py"
            source.write_bytes(b"invented source fixture")
            source.chmod(0o400)
            with (
                patch.object(workspace, "__file__", str(source)),
                patch.object(workspace.tempfile, "mkdtemp") as create,
                patch.object(workspace, "run_native") as native,
            ):
                with self.assertRaisesRegex(workspace.WorkspaceError, "source_hash"):
                    workspace.prepare_empty("a" * 64)
                create.assert_not_called()
                native.assert_not_called()

    def test_source_change_during_native_preparation_prevents_manifest_publication(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "source.py"
            source.write_bytes(b"invented source fixture")
            source.chmod(0o400)
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            image = root / workspace.IMAGE_NAME
            image.touch(mode=0o600)
            mount = root / workspace.MOUNT_NAME

            def native(_argv, **_kwargs):
                source.chmod(0o600)
                source.write_bytes(b"changed synthetic source fixture")
                source.chmod(0o400)
                return plistlib.dumps({"encrypted": True})

            with (
                patch.object(workspace, "__file__", str(source)),
                patch.object(workspace.tempfile, "mkdtemp", return_value=str(root)),
                patch.object(workspace, "workspace_paths", return_value=(image, mount)),
                patch.object(workspace, "run_native", side_effect=native),
                patch.object(workspace, "image_binding", return_value=DEVICE),
                patch.object(workspace, "check_disk"),
                patch.object(workspace.os.path, "ismount", return_value=True),
                patch.object(workspace, "initialize_empty_area", return_value={}),
            ):
                with self.assertRaisesRegex(workspace.WorkspaceError, "source_hash"):
                    workspace.prepare_empty(expected)
            self.assertFalse((root / workspace.MANIFEST_NAME).exists())


if __name__ == "__main__":
    unittest.main()
