from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

import scripts.staging_offline_plan_runtime as runtime


def test_json_and_canonical_hash_are_strict() -> None:
    assert runtime.load_json_bytes(b'{"ok":true}', label="record") == {"ok": True}
    assert runtime.canonical_file_set_sha256({"b": b"2", "a": b"1"}) == (
        runtime.canonical_file_set_sha256({"a": b"1", "b": b"2"})
    )
    for value in (b'{"a":1,"a":2}', b'{"a":NaN}', b"[]"):
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


def test_environment_removes_credential_network_and_tool_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (
        "ARM_CLIENT_SECRET",
        "AZURE_CONFIG_DIR",
        "TF_CLI_ARGS",
        "TOFU_CLI_CONFIG_FILE",
        "GIT_CONFIG_COUNT",
        "HTTPS_PROXY",
        "http_proxy",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
    ):
        monkeypatch.setenv(name, "untrusted")
    environment = runtime.normalized_environment(data_dir=tmp_path)
    assert not any(
        name.upper() in {key.upper() for key in environment}
        for name in (
            "ARM_CLIENT_SECRET",
            "AZURE_CONFIG_DIR",
            "TF_CLI_ARGS",
            "TOFU_CLI_CONFIG_FILE",
            "GIT_CONFIG_COUNT",
            "HTTPS_PROXY",
            "http_proxy",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
        )
    )
    assert environment["TF_DATA_DIR"] == str(tmp_path)
    assert environment["TF_CLI_CONFIG_FILE"] == os.devnull


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
        runtime.require_pinned_executable(destination, expected=digest, label="tool")
        == digest
    )


def test_copy_rejects_unpinned_executable_without_leaving_output(
    tmp_path: Path,
) -> None:
    source = Path(sys.executable).resolve()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    destination = private / "python"
    with pytest.raises(runtime.OfflineRuntimeError, match="digest is not pinned"):
        runtime.copy_pinned_executable(
            source, destination, expected="0" * 64, label="Python"
        )
    assert not destination.exists()


def test_materialized_source_is_private_and_exact(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    source = tmp_path / "source"
    runtime.materialize_source(source, {"main.tf": b"reviewed", "lock": b"pinned"})
    assert stat.S_IMODE(source.stat().st_mode) == 0o700
    assert (source / "main.tf").read_bytes() == b"reviewed"
    assert stat.S_IMODE((source / "main.tf").stat().st_mode) == 0o400

    with pytest.raises(runtime.OfflineRuntimeError, match="member is invalid"):
        runtime.materialize_source(tmp_path / "bad", {"../escape": b"no"})


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


def test_run_process_is_bounded_and_uses_normalized_environment(tmp_path: Path) -> None:
    result = runtime.run_process(
        [sys.executable, "-c", "import os; print(os.environ['TF_DATA_DIR'])"],
        cwd=tmp_path,
        timeout_seconds=5,
        data_dir=tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == str(tmp_path).encode()

    with pytest.raises(runtime.OfflineRuntimeError, match="exceeded limit"):
        runtime.run_process(
            [sys.executable, "-c", "print('x' * 1000)"],
            cwd=tmp_path,
            timeout_seconds=5,
            max_output_bytes=10,
        )
