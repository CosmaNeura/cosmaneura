from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import scripts.staging_offline_plan_runtime as runtime


def test_json_and_canonical_hash_are_strict() -> None:
    assert runtime.load_json_bytes(b'{"ok":true}', label="record") == {"ok": True}
    assert runtime.canonical_file_set_sha256({"b": b"2", "a": b"1"}) == (
        runtime.canonical_file_set_sha256({"a": b"1", "b": b"2"})
    )
    for value in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e9999}', b"[]"):
        with pytest.raises(runtime.OfflineRuntimeError):
            runtime.load_json_bytes(value, label="record")


def test_private_file_requires_exact_mode_owner_link_and_stable_path(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"reviewed")
    artifact.chmod(0o400)
    assert runtime.read_private_file(artifact, label="artifact") == b"reviewed"

    artifact.chmod(0o600)
    with pytest.raises(runtime.OfflineRuntimeError, match="0400"):
        runtime.read_private_file(artifact, label="artifact")
    artifact.chmod(0o400)
    link = tmp_path / "link"
    link.symlink_to(artifact)
    with pytest.raises(runtime.OfflineRuntimeError, match="unavailable"):
        runtime.read_private_file(link, label="artifact")
    hardlink = tmp_path / "hardlink"
    hardlink.hardlink_to(artifact)
    with pytest.raises(runtime.OfflineRuntimeError, match="0400"):
        runtime.read_private_file(artifact, label="artifact")


def test_checkout_reader_rejects_writable_or_linked_source(tmp_path: Path) -> None:
    source = tmp_path / "source.tf"
    source.write_bytes(b"reviewed")
    source.chmod(0o644)
    assert runtime.read_checkout_file(source, label="source") == b"reviewed"

    source.chmod(0o664)
    with pytest.raises(runtime.OfflineRuntimeError, match="stable reviewed"):
        runtime.read_checkout_file(source, label="source")
    source.chmod(0o644)
    link = tmp_path / "linked.tf"
    link.symlink_to(source)
    with pytest.raises(runtime.OfflineRuntimeError, match="safely"):
        runtime.read_checkout_file(link, label="source")


def test_copy_pinned_executable_creates_private_exact_runtime(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"#!/bin/sh\nprintf trusted")
    source.chmod(0o500)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    destination = private / "tool"
    digest = runtime.sha256_bytes(source.read_bytes())

    assert (
        runtime.copy_pinned_executable(
            source, destination, expected=digest, label="tool"
        )
        == destination
    )
    assert destination.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o500
    assert (
        runtime.verify_private_executable(destination, expected=digest, label="tool")
        == digest
    )


def test_copy_rejects_unpinned_executable_without_leaving_output(
    tmp_path: Path,
) -> None:
    source = Path("/bin/sh").resolve()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    destination = private / "python"
    with pytest.raises(runtime.OfflineRuntimeError, match="digest is not pinned"):
        runtime.copy_pinned_executable(
            source, destination, expected="0" * 64, label="Python"
        )
    assert not destination.exists()


def test_copy_cleans_destination_when_final_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"#!/bin/sh\nprintf trusted")
    source.chmod(0o500)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    destination = private / "tool"

    def fail_verification(*_args: object, **_kwargs: object) -> str:
        raise runtime.OfflineRuntimeError("simulated final verification failure")

    monkeypatch.setattr(runtime, "verify_private_executable", fail_verification)
    with pytest.raises(runtime.OfflineRuntimeError, match="final verification"):
        runtime.copy_pinned_executable(
            source,
            destination,
            expected=runtime.sha256_bytes(source.read_bytes()),
            label="tool",
        )
    assert not destination.exists()


def test_copy_cleans_destination_when_initial_metadata_setup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"#!/bin/sh\nprintf trusted")
    source.chmod(0o500)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    destination = private / "tool"

    def fail_mode_change(_descriptor: int, _mode: int) -> None:
        raise OSError("simulated mode failure")

    monkeypatch.setattr(runtime.os, "fchmod", fail_mode_change)
    with pytest.raises(runtime.OfflineRuntimeError, match="could not be isolated"):
        runtime.copy_pinned_executable(
            source,
            destination,
            expected=runtime.sha256_bytes(source.read_bytes()),
            label="tool",
        )
    assert not destination.exists()


def test_executable_hash_rejects_fifo_and_copy_enforces_size_bound(
    tmp_path: Path,
) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, mode=0o500)
    with pytest.raises(runtime.OfflineRuntimeError, match="invalid"):
        runtime.executable_sha256(fifo, label="FIFO")
    with pytest.raises(runtime.OfflineRuntimeError, match="size limit is invalid"):
        runtime.executable_sha256(
            Path("/bin/sh").resolve(),
            label="shell",
            maximum_bytes=runtime.MAX_EXECUTABLE_BYTES + 1,
        )

    source = tmp_path / "source"
    source.write_bytes(b"#!/bin/sh\n")
    source.chmod(0o500)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    with pytest.raises(runtime.OfflineRuntimeError, match="size limit is invalid"):
        runtime.copy_pinned_executable(
            source,
            private / "oversize-limit",
            expected=runtime.sha256_bytes(source.read_bytes()),
            label="tool",
            maximum_bytes=runtime.MAX_EXECUTABLE_BYTES + 1,
        )
    with pytest.raises(runtime.OfflineRuntimeError, match="not stable"):
        runtime.copy_pinned_executable(
            source,
            private / "tool",
            expected=runtime.sha256_bytes(source.read_bytes()),
            label="tool",
            maximum_bytes=4,
        )
    assert not (private / "tool").exists()


def test_materialized_source_is_private_and_exact(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    source = tmp_path / "source"
    runtime.materialize_source(source, {"main.tf": b"reviewed", "lock": b"pinned"})
    assert stat.S_IMODE(source.stat().st_mode) == 0o700
    assert (source / "main.tf").read_bytes() == b"reviewed"
    assert stat.S_IMODE((source / "main.tf").stat().st_mode) == 0o400

    with pytest.raises(runtime.OfflineRuntimeError, match="member is invalid"):
        runtime.materialize_source(tmp_path / "bad", {"../escape": b"no"})
    assert not (tmp_path / "bad").exists()
    with pytest.raises(runtime.OfflineRuntimeError, match="member is invalid"):
        runtime.materialize_source(tmp_path / "bad-nul", {"bad\0name": b"no"})
    assert not (tmp_path / "bad-nul").exists()


def test_materialized_source_never_replaces_existing_directory(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    original_inode = source.stat().st_ino

    with pytest.raises(runtime.OfflineRuntimeError, match="without replacement"):
        runtime.materialize_source(source, {"main.tf": b"reviewed"})

    assert source.stat().st_ino == original_inode
    assert list(source.iterdir()) == []
    assert list(tmp_path.iterdir()) == [source]


def test_materialized_source_uses_one_validated_member_snapshot(tmp_path: Path) -> None:
    class ChangingMapping:
        calls = 0

        def items(self) -> list[tuple[str, bytes]]:
            self.calls += 1
            if self.calls == 1:
                return [("main.tf", b"reviewed")]
            return [("../escape", b"unreviewed")]

    tmp_path.chmod(0o700)
    source = tmp_path / "source"
    changing = ChangingMapping()
    runtime.materialize_source(source, changing)  # type: ignore[arg-type]

    assert changing.calls == 1
    assert (source / "main.tf").read_bytes() == b"reviewed"
    assert not (tmp_path.parent / "escape").exists()

    class DuplicateMapping:
        def items(self) -> list[tuple[str, bytes]]:
            return [("main.tf", b"first"), ("main.tf", b"second")]

    with pytest.raises(runtime.OfflineRuntimeError, match="duplicated"):
        runtime.materialize_source(
            tmp_path / "duplicate",
            DuplicateMapping(),  # type: ignore[arg-type]
        )


def test_materialized_source_cleans_published_tree_when_parent_sync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path.chmod(0o700)
    source = tmp_path / "source"
    original_fsync = runtime.os.fsync
    calls = 0

    def fail_parent_sync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated parent sync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(runtime.os, "fsync", fail_parent_sync)
    with pytest.raises(runtime.OfflineRuntimeError, match="could not be materialized"):
        runtime.materialize_source(source, {"main.tf": b"reviewed"})

    assert not source.exists()
    assert list(tmp_path.iterdir()) == []


def test_runtime_tree_is_private_and_has_one_executable(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    package = tmp_path / "package"
    package.mkdir(mode=0o755)
    executable = package / "provider"
    executable.write_bytes(b"binary")
    (package / "README").write_text("evidence")

    runtime.harden_private_tree(tmp_path, executable=Path("package/provider"))

    assert stat.S_IMODE(package.stat().st_mode) == 0o700
    assert stat.S_IMODE(executable.stat().st_mode) == 0o500
    assert stat.S_IMODE((package / "README").stat().st_mode) == 0o400
