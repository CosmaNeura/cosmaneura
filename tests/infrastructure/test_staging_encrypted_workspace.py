"""Secretless native-metadata fixtures; never create or mount a disk image."""

import hashlib
import importlib.util
import os
import plistlib
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
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
        "ReadOnlyVolume": False,
        "GlobalPermissionsEnabled": True,
    }


class EncryptedWorkspaceTests(unittest.TestCase):
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
