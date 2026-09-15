"""Prepare an EMPTY encrypted workspace in a human's macOS Terminal.

No database/cloud access, receipt, execution authority, or data-copy operation.
This helper must never be invoked by an agent against native disk utilities.
Source review and normal merge precede operator use; snapshot authority and
fresh plan/identity/handoff review are separate, later gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

HDIUTIL = "/usr/bin/hdiutil"
DISKUTIL = "/usr/sbin/diskutil"
PREFIX = "cosmaneura-staging-snapshot-"
IMAGE_NAME = "staging.sparseimage"
MOUNT_NAME = "mounted"
MANIFEST_NAME = "empty-workspace.json"
PAYLOAD_NAME = "payload"
MARKER_NAME = ".metadata_never_index"
SIZE_BYTES = 8 * 1024**3


class WorkspaceError(RuntimeError):
    pass


def require(condition: object, code: str) -> None:
    if not condition:
        raise WorkspaceError(code)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def private_path(path: Path, *, directory: bool, mode: int) -> os.stat_result:
    value = path.lstat()
    require(not path.is_symlink() and path.resolve() == path, "path_alias")
    require(value.st_uid == os.geteuid(), "wrong_owner")
    require(stat.S_IMODE(value.st_mode) == mode, "unsafe_permissions")
    require(
        stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode),
        "wrong_file_type",
    )
    if not directory:
        require(value.st_nlink == 1, "hardlinked_file")
    return value


def workspace_paths(root: Path) -> tuple[Path, Path]:
    require(root.parent == Path("/private/tmp"), "workspace_parent")
    require(re.fullmatch(PREFIX + r"[a-z0-9_]{8}", root.name), "workspace_name")
    private_path(root, directory=True, mode=0o700)
    return root / IMAGE_NAME, root / MOUNT_NAME


def create_command(image: Path) -> list[str]:
    return [
        HDIUTIL,
        "create",
        "-size",
        "8g",
        "-type",
        "SPARSE",
        "-fs",
        "APFS",
        "-volname",
        "CosmaneuraStaging",
        "-encryption",
        "AES-256",
        "-stdinpass",
        "-nospotlight",
        str(image),
    ]


def attach_command(image: Path, mount: Path) -> list[str]:
    return [
        HDIUTIL,
        "attach",
        str(image),
        "-stdinpass",
        "-mountpoint",
        str(mount),
        "-owners",
        "on",
        "-nobrowse",
        "-noautoopen",
        "-plist",
    ]


def run_native(argv: list[str], *, interactive: bool = False) -> bytes:
    # -stdinpass reads directly from the inherited TTY with native echo-off.
    # Python never receives, stores, logs, or puts the passphrase in argv.
    result = subprocess.run(
        argv,
        env={"PATH": "/usr/bin:/bin:/usr/sbin", "LC_ALL": "C", "LANG": "C"},
        stdin=None if interactive else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=None if interactive else subprocess.DEVNULL,
        check=False,
        timeout=300 if interactive else 30,
    )
    require(result.returncode == 0, "native_command_failed")
    require(len(result.stdout) <= 2 * 1024**2, "native_output_size")
    return result.stdout


def parse_plist(raw: bytes) -> dict[str, Any]:
    value = plistlib.loads(raw)
    require(type(value) is dict, "native_plist_shape")
    return value


def image_binding(info: dict[str, Any], image: Path, mount: Path) -> str:
    images = info.get("images")
    if not isinstance(images, list):
        raise WorkspaceError("images_shape")
    require(all(type(item) is dict for item in images), "image_record_shape")
    matches = [item for item in images if item.get("image-path") == str(image)]
    require(len(matches) == 1, "image_binding")
    item = matches[0]
    require(item.get("image-encrypted") is True, "image_not_encrypted")
    require(item.get("writeable") is True, "image_not_writable")
    require(item.get("owner-uid") == os.geteuid(), "image_owner")
    require(not item.get("shadow-path"), "image_shadow")
    require(
        type(item.get("blockcount")) is int
        and type(item.get("blocksize")) is int
        and item["blockcount"] > 0
        and item["blocksize"] > 0
        and item["blockcount"] * item["blocksize"] == SIZE_BYTES,
        "image_capacity",
    )
    entities = item.get("system-entities")
    if not isinstance(entities, list):
        raise WorkspaceError("entities_shape")
    require(all(type(entity) is dict for entity in entities), "entity_record_shape")
    mounted = [entity for entity in entities if entity.get("mount-point")]
    require(len(mounted) == 1, "extra_mount")
    require(mounted[0].get("mount-point") == str(mount), "mount_binding")
    device = mounted[0].get("dev-entry")
    require(
        type(device) is str and re.fullmatch(r"/dev/disk[0-9]+s[0-9]+", device),
        "device_shape",
    )
    # Reject a second image claiming the same mount/device, rather than trusting
    # only the first matching image-path record.
    owners = [
        entity
        for candidate in images
        for entity in candidate.get("system-entities", [])
        if entity.get("mount-point") == str(mount) or entity.get("dev-entry") == device
    ]
    require(len(owners) == 1, "ambiguous_mount")
    return device


def check_disk(info: dict[str, Any], mount: Path, device: str) -> None:
    require(info.get("MountPoint") == str(mount), "disk_mount")
    require(info.get("DeviceNode") == device, "disk_device")
    require(info.get("FilesystemType") == "apfs", "disk_filesystem")
    # The pinned macOS diskutil reports positive Writable* booleans. Missing
    # metadata is not false/safe; require all three, and reject contradictory
    # legacy ReadOnly* fields if a later utility supplies them as well.
    require(
        all(
            info.get(key) is True
            for key in ("Writable", "WritableMedia", "WritableVolume")
        )
        and all(
            key not in info or info[key] is False
            for key in ("ReadOnly", "ReadOnlyMedia", "ReadOnlyVolume")
        ),
        "disk_readonly",
    )
    require(info.get("GlobalPermissionsEnabled") is True, "disk_ownership_disabled")


def verify_mounted(root: Path) -> dict[str, Any]:
    image, mount = workspace_paths(root)
    image_stat = private_path(image, directory=False, mode=0o600)
    mount_stat = private_path(mount, directory=True, mode=0o700)
    require(
        mount_stat.st_dev != root.stat().st_dev and os.path.ismount(mount), "not_mount"
    )
    # isencrypted opens the backing image and refuses while that image is
    # attached on this macOS version. image_binding verifies the live native
    # image-encrypted flag AND exact image/owner/capacity/mount/device binding.
    device = image_binding(
        parse_plist(run_native([HDIUTIL, "info", "-plist"])), image, mount
    )
    check_disk(
        parse_plist(run_native([DISKUTIL, "info", "-plist", str(mount)])), mount, device
    )
    after = private_path(image, directory=False, mode=0o600)
    require(
        (image_stat.st_dev, image_stat.st_ino) == (after.st_dev, after.st_ino),
        "image_replaced",
    )
    return {
        "workspace": str(root),
        "image": str(image),
        "mount": str(mount),
        "device": device,
        "image_inode": after.st_ino,
        "image_device": after.st_dev,
        "mount_device": mount_stat.st_dev,
        "mount_inode": mount_stat.st_ino,
        "capacity_bytes": SIZE_BYTES,
        "encrypted_image_bound_to_mounted_apfs": True,
        "observed_at": datetime.now(UTC).isoformat(),
        "capture_authorized": False,
        "live_action_authorized": False,
    }


def checked_source(expected_sha256: str) -> str:
    require(re.fullmatch(r"[0-9a-f]{64}", expected_sha256), "source_hash_shape")
    path = Path(__file__)
    before = private_path(path, directory=False, mode=0o400)
    require(0 < before.st_size < 128 * 1024, "source_size")
    raw = path.read_bytes()
    after = private_path(path, directory=False, mode=0o400)
    require(stable_file(before, after), "source_changed")
    require(hashlib.sha256(raw).hexdigest() == expected_sha256, "source_hash")
    return expected_sha256


def open_bound_mount(mounted: dict[str, Any]) -> int:
    fd = os.open(
        mounted["mount"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        value = os.fstat(fd)
        require(
            value.st_dev == mounted["mount_device"]
            and value.st_ino == mounted["mount_inode"],
            "mount_replaced",
        )
        require(
            value.st_uid == os.geteuid() and stat.S_IMODE(value.st_mode) == 0o700,
            "mount_permissions",
        )
        return fd
    except BaseException:
        os.close(fd)
        raise


def empty_area(mount_fd: int, device: int) -> dict[str, int]:
    marker_fd = os.open(
        MARKER_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=mount_fd
    )
    try:
        marker = os.fstat(marker_fd)
        require(
            stat.S_ISREG(marker.st_mode)
            and marker.st_uid == os.geteuid()
            and stat.S_IMODE(marker.st_mode) == 0o600
            and marker.st_nlink == 1
            and marker.st_size == 0
            and marker.st_dev == device,
            "invalid_noindex_marker",
        )
    finally:
        os.close(marker_fd)
    payload_fd = os.open(
        PAYLOAD_NAME,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=mount_fd,
    )
    try:
        payload = os.fstat(payload_fd)
        require(
            payload.st_uid == os.geteuid()
            and stat.S_IMODE(payload.st_mode) == 0o700
            and payload.st_dev == device,
            "invalid_payload_directory",
        )
        require(not os.listdir(payload_fd), "payload_not_empty")
        return {
            "payload_device": payload.st_dev,
            "payload_inode": payload.st_ino,
            "noindex_marker_inode": marker.st_ino,
        }
    finally:
        os.close(payload_fd)


def verify_empty(root: Path) -> dict[str, Any]:
    before = verify_mounted(root)
    fd = open_bound_mount(before)
    try:
        area = empty_area(fd, before["mount_device"])
        after = verify_mounted(root)
        for key in (
            "mount_device",
            "mount_inode",
            "image_device",
            "image_inode",
            "device",
        ):
            require(before[key] == after[key], "mount_changed")
        require(area == empty_area(fd, before["mount_device"]), "empty_area_changed")
        after.update(area, empty_payload_verified=True, noindex_marker_verified=True)
        return after
    finally:
        os.close(fd)


def initialize_empty_area(
    root: Path, expected_binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    mounted = verify_mounted(root)
    if expected_binding is not None:
        for key in (
            "image_device",
            "image_inode",
            "mount_device",
            "mount_inode",
            "device",
        ):
            require(mounted[key] == expected_binding[key], "existing_workspace_changed")
    # If detach races the open, the device/inode comparison refuses before any
    # write. Once opened, relative writes stay on that encrypted filesystem,
    # never on a plaintext directory that later replaces the mount path.
    fd = open_bound_mount(mounted)
    try:
        if expected_binding is not None:
            require(not os.listdir(fd), "existing_mount_not_empty")
        marker_fd = os.open(
            MARKER_NAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=fd,
        )
        os.close(marker_fd)
        os.mkdir(PAYLOAD_NAME, mode=0o700, dir_fd=fd)
    finally:
        os.close(fd)
    return verify_empty(root)


def prepare_empty(expected_source_sha256: str) -> dict[str, Any]:
    source_sha256 = checked_source(expected_source_sha256)
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix=PREFIX, dir="/private/tmp"))
    image, mount = workspace_paths(root)
    mount.mkdir(mode=0o700)
    print(f"Empty-workspace path: {root}", flush=True)
    print(
        "Enter a NEW strong disk-image passphrase at the native prompt. Keep it outside this task; do not save it in Keychain.",
        flush=True,
    )
    run_native(create_command(image), interactive=True)
    private_path(image, directory=False, mode=0o600)
    encrypted = parse_plist(run_native([HDIUTIL, "isencrypted", str(image), "-plist"]))
    require(encrypted.get("encrypted") is True, "encryption_check_failed")
    print("Enter the same passphrase to attach the empty image.", flush=True)
    run_native(attach_command(image, mount), interactive=True)
    # No data exists yet. Check the native image-to-mount binding before chmod,
    # and refuse a plain directory or a different image at the target path.
    device = image_binding(
        parse_plist(run_native([HDIUTIL, "info", "-plist"])), image, mount
    )
    check_disk(
        parse_plist(run_native([DISKUTIL, "info", "-plist", str(mount)])), mount, device
    )
    require(mount.resolve() == mount and os.path.ismount(mount), "not_mount")
    require(mount.lstat().st_uid == os.geteuid(), "mount_owner")
    mount.chmod(0o700)
    evidence = initialize_empty_area(root)
    return publish_preparation(root, source_sha256, evidence, "prepare-empty")


def publish_preparation(
    root: Path, source_sha256: str, evidence: dict[str, Any], operation: str
) -> dict[str, Any]:
    image, _mount = workspace_paths(root)
    require(operation in {"prepare-empty", "complete-empty"}, "setup_operation")
    evidence.update(
        {
            "schema_version": 1,
            "empty_workspace_prepared": True,
            "source_sha256": source_sha256,
            "creation_command_sha256": hashlib.sha256(
                canonical(create_command(image))
            ).hexdigest(),
            "requested_encryption": "AES-256",
            "live_data_copied": False,
            "not_a_receipt_or_permanent_readiness_proof": True,
            "setup_operation": operation,
            "image_created_in_this_process": operation == "prepare-empty",
        }
    )
    require(checked_source(source_sha256) == source_sha256, "source_changed")
    manifest = root / MANIFEST_NAME
    fd = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(evidence))
        stream.flush()
        os.fsync(stream.fileno())
    return evidence


def existing_binding(raw: str) -> dict[str, Any]:
    """Exact independently reviewed metadata, never self-authenticating authority."""
    require(type(raw) is str and 0 < len(raw) <= 4096, "existing_binding_shape")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "existing_binding_duplicate")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    integers = {
        "root_device",
        "root_inode",
        "image_device",
        "image_inode",
        "mount_device",
        "mount_inode",
    }
    require(
        type(value) is dict and set(value) == integers | {"workspace", "device"},
        "existing_binding_shape",
    )
    value = cast(dict[str, Any], value)
    require(
        all(type(value[key]) is int and value[key] > 0 for key in integers),
        "existing_binding_shape",
    )
    require(
        type(value["workspace"]) is str and type(value["device"]) is str,
        "existing_binding_shape",
    )
    require(
        re.fullmatch(r"/dev/disk[0-9]+s[0-9]+", value["device"]),
        "existing_binding_shape",
    )
    return value


def observe_existing_empty(root: Path, binding: dict[str, Any]) -> os.stat_result:
    """Read-only proof of the precise pre-initialization state; no repair/retry."""
    require(binding["workspace"] == str(root), "existing_workspace_changed")
    image, mount = workspace_paths(root)
    require(
        set(os.listdir(root)) == {IMAGE_NAME, MOUNT_NAME}, "existing_workspace_contents"
    )
    values = {
        "root": private_path(root, directory=True, mode=0o700),
        "image": private_path(image, directory=False, mode=0o600),
        "mount": private_path(mount, directory=True, mode=0o755),
    }
    for name, metadata in values.items():
        require(
            (metadata.st_dev, metadata.st_ino)
            == (binding[name + "_device"], binding[name + "_inode"]),
            "existing_workspace_changed",
        )
    require(
        values["mount"].st_dev != values["root"].st_dev and os.path.ismount(mount),
        "not_mount",
    )
    device = image_binding(
        parse_plist(run_native([HDIUTIL, "info", "-plist"])), image, mount
    )
    require(device == binding["device"], "existing_workspace_changed")
    check_disk(
        parse_plist(run_native([DISKUTIL, "info", "-plist", str(mount)])), mount, device
    )
    return values["mount"]


def complete_empty(
    root: Path, expected_source_sha256: str, binding: dict[str, Any]
) -> dict[str, Any]:
    """Separately approved HUMAN completion of one already attached empty image.

    No create/attach/detach/delete, password, retry or use of a different path.
    The exact binding/command need independent review and explicit recovery scope.
    This is not permission to resume an arbitrary partial or data-bearing image.
    """
    require(all(os.isatty(fd) for fd in (0, 1, 2)), "regular_terminal_required")
    binding = existing_binding(json.dumps(binding))
    source_sha = checked_source(expected_source_sha256)
    os.umask(0o077)
    before = observe_existing_empty(root, binding)
    _image, mount = workspace_paths(root)
    fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        metadata = os.fstat(fd)
        require(
            (metadata.st_dev, metadata.st_ino) == (before.st_dev, before.st_ino),
            "mount_replaced",
        )
        require(
            metadata.st_uid == os.geteuid() and stat.S_IMODE(metadata.st_mode) == 0o755,
            "mount_permissions",
        )
        require(not os.listdir(fd), "existing_mount_not_empty")
        repeated = observe_existing_empty(root, binding)
        require(
            (repeated.st_dev, repeated.st_ino) == (metadata.st_dev, metadata.st_ino),
            "mount_replaced",
        )
        require(checked_source(expected_source_sha256) == source_sha, "source_changed")
        require(not os.listdir(fd), "existing_mount_not_empty")
        # The only permission change targets this held, verified encrypted mount.
        # No pathname chmod may fall through to a plaintext replacement directory.
        os.fchmod(fd, 0o700)
        evidence = initialize_empty_area(root, expected_binding=binding)
        current = os.fstat(fd)
        require(
            (current.st_dev, current.st_ino)
            == (evidence["mount_device"], evidence["mount_inode"]),
            "mount_replaced",
        )
        return publish_preparation(root, source_sha, evidence, "complete-empty")
    finally:
        os.close(fd)


def stable_file(before: os.stat_result, after: os.stat_result) -> bool:
    # A read may update atime; content/ownership/binding changes must not pass.
    fields = (
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
    return all(getattr(before, key) == getattr(after, key) for key in fields)


def verify_prepared(
    root: Path, expected_manifest_sha256: str, expected_source_sha256: str
) -> dict[str, Any]:
    source_sha256 = checked_source(expected_source_sha256)
    workspace_paths(root)
    require(
        re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256), "manifest_hash_shape"
    )
    path = root / MANIFEST_NAME
    before = private_path(path, directory=False, mode=0o400)
    require(0 < before.st_size < 16384, "manifest_size")
    raw = path.read_bytes()
    after = private_path(path, directory=False, mode=0o400)
    require(
        stable_file(before, after)
        and hashlib.sha256(raw).hexdigest() == expected_manifest_sha256,
        "manifest_changed",
    )
    previous = json.loads(raw)
    require(
        previous.get("empty_workspace_prepared") is True, "manifest_not_preparation"
    )
    require(
        previous.get("source_sha256") == source_sha256,
        "source_changed",
    )
    current = verify_empty(root)
    for name in (
        "workspace",
        "image",
        "mount",
        "device",
        "image_inode",
        "image_device",
        "mount_device",
        "mount_inode",
        "payload_device",
        "payload_inode",
        "noindex_marker_inode",
        "capacity_bytes",
    ):
        require(previous.get(name) == current[name], "workspace_changed")
    current["manifest_sha256"] = expected_manifest_sha256
    current["not_a_receipt_or_permanent_readiness_proof"] = True
    require(checked_source(expected_source_sha256) == source_sha256, "source_changed")
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="operation", required=True)
    prepare = subs.add_parser("prepare-empty")
    prepare.add_argument("--expected-source-sha256", required=True)
    complete = subs.add_parser("complete-empty")
    complete.add_argument("--workspace", type=Path, required=True)
    complete.add_argument("--expected-source-sha256", required=True)
    complete.add_argument("--binding-json", required=True)
    verify = subs.add_parser("verify")
    verify.add_argument("--workspace", type=Path, required=True)
    verify.add_argument("--manifest-sha256", required=True)
    verify.add_argument("--expected-source-sha256", required=True)
    args = parser.parse_args()
    require(sys.platform == "darwin" and os.geteuid() != 0, "human_macos_required")
    require(
        sys.flags.isolated and sys.flags.no_site and not sys.flags.optimize,
        "isolated_python_required",
    )
    if args.operation in {"prepare-empty", "complete-empty"}:
        require(all(os.isatty(fd) for fd in (0, 1, 2)), "regular_terminal_required")
        if args.operation == "prepare-empty":
            result = prepare_empty(args.expected_source_sha256)
        else:
            result = complete_empty(
                args.workspace,
                args.expected_source_sha256,
                existing_binding(args.binding_json),
            )
        result["manifest_sha256"] = hashlib.sha256(
            (Path(result["workspace"]) / MANIFEST_NAME).read_bytes()
        ).hexdigest()
    else:
        result = verify_prepared(
            args.workspace, args.manifest_sha256, args.expected_source_sha256
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        code = str(exc) if isinstance(exc, WorkspaceError) else "check_failed"
        print(
            json.dumps(
                {
                    "empty_workspace_ready": False,
                    "code": code,
                    "capture_authorized": False,
                    "live_action_authorized": False,
                }
            )
        )
        print(
            "No cleanup is automatic. Preserve the printed workspace path; report this refusal without entering credentials in chat.",
            file=sys.stderr,
        )
        raise SystemExit(2)
