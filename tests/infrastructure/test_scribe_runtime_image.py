"""Synthetic runtime/image snapshots exercise presence, permissions and OCI pin."""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import tarfile
from pathlib import Path

import pytest

from scripts import scribe_runtime_check as guard
from scripts import scribe_runtime_oci as oci
from scripts.build_scribe_runtime import source_context
from scripts.scribe_runtime_pin import RUNTIME_DIGEST_LABEL


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-runtime"
    (root / "bin").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "bin/python").write_text("#!/bin/sh\nexit 0\n")
    (root / "bin/python").chmod(0o755)
    (root / "bin/python-real").write_bytes(b"synthetic interpreter\n")
    (root / "lib/module.py").write_text("synthetic_value = 1\n")
    expected = oci.package(root)
    monkeypatch.setattr(guard, "SCRIBE_RUNTIME_IMAGE_DIGEST", expected)
    return root, expected


def test_runtime_with_executable_python_and_matching_pin_passes(runtime):
    root, pin = runtime
    assert guard.verify_runtime(root, pin) == pin


@pytest.mark.parametrize("condition", ["absent", "non_executable", "symlink"])
def test_missing_or_non_executable_python_refuses(runtime, condition):
    root, pin = runtime
    launcher = root / "bin/python"
    if condition == "non_executable":
        launcher.chmod(0o644)
    else:
        launcher.unlink()
        if condition == "symlink":
            launcher.symlink_to("python-real")
    with pytest.raises(ValueError, match="python_missing_or_not_executable"):
        guard.verify_runtime(root, pin)


def test_different_layered_digest_refuses(runtime):
    root, _ = runtime
    with pytest.raises(ValueError, match="layer_digest"):
        guard.verify_runtime(root, "sha256:" + "0" * 64)


@pytest.mark.parametrize("mutation", ["bytes", "mode", "extra_file", "symlink"])
def test_valid_digest_label_cannot_cover_altered_runtime(runtime, mutation):
    root, pin = runtime
    module = root / "lib/module.py"
    if mutation == "bytes":
        module.write_text("synthetic_value = 2\n")
    elif mutation == "mode":
        module.chmod(0o755)
    elif mutation == "extra_file":
        (root / "lib/unreviewed.py").write_text("synthetic_value = 3\n")
    else:
        module.unlink()
        module.symlink_to("../bin/python-real")
    with pytest.raises(ValueError):
        guard.verify_runtime(root, pin)


def test_repack_ignores_timestamps_but_retains_content_identity(runtime, tmp_path):
    root, pin = runtime
    for path in [root, *root.rglob("*")]:
        os.utime(path, (1700000000, 1700000000))
    output = tmp_path / "oci"
    assert oci.package(root, output) == pin
    manifest = (output / "blobs/sha256" / pin.split(":")[1]).read_bytes()
    assert oci.digest(manifest) == pin
    value = json.loads(manifest)
    for entry in [value["config"], *value["layers"]]:
        data = (output / "blobs/sha256" / entry["digest"].split(":")[1]).read_bytes()
        assert oci.digest(data) == entry["digest"]
        assert len(data) == entry["size"]


def test_build_cache_normalization_preserves_runtime_sources_and_record(runtime):
    root, _ = runtime
    site = root / "lib/python3.13/site-packages"
    metadata = site / "synthetic-0.1.dist-info"
    metadata.mkdir(parents=True)
    (site / "__pycache__").mkdir()
    (site / "__pycache__/synthetic.pyc").write_bytes(b"synthetic timestamped bytecode")
    (metadata / "uv_cache.json").write_text('{"timestamp": 1}')
    (metadata / "RECORD").write_text(
        "synthetic.py,sha256=source_hash,1\n"
        "synthetic-0.1.dist-info/uv_cache.json,sha256=cache_hash,1\n"
        "synthetic-0.1.dist-info/RECORD,,\n"
    )
    original = (root / "lib/module.py").read_bytes()
    oci.normalize_build_runtime(root)
    assert (root / "lib/module.py").read_bytes() == original
    assert not (site / "__pycache__").exists()
    assert not (metadata / "uv_cache.json").exists()
    assert (metadata / "RECORD").read_text() == (
        "synthetic.py,sha256=source_hash,1\nsynthetic-0.1.dist-info/RECORD,,\n"
    )
    normalized = oci.package(root)
    oci.normalize_build_runtime(root)
    assert oci.package(root) == normalized


@pytest.mark.parametrize("enabled", [True, False])
def test_literal_api_source_pin_controls_requirement(enabled):
    source = (
        "production_live_scribe_core_bundle = {\napi_enabled = "
        + str(enabled).lower()
        + "\n}\n"
    )
    assert guard.runtime_required(source) is enabled


@pytest.mark.parametrize(
    "source",
    [
        "",
        "# production_live_scribe_core_bundle = {\n# api_enabled = true\n# }",
        'example = "production_live_scribe_core_bundle = { api_enabled = true }"',
        'production_live_scribe_core_bundle = {\napi_enabled = "true"\n}',
        "production_live_scribe_core_bundle = {\napi_enabled = var.enabled\n}",
        "production_live_scribe_core_bundle = {\napi_enabled = true\napi_enabled = false\n}",
        "production_live_scribe_core_bundle = {\napi_enabled = true\n",
        "production_live_scribe_core_bundle = {\nnested = {\napi_enabled = true\n}\n}",
        "production_live_scribe_core_bundle = {\napi_enabled = false\n}\n"
        "production_live_scribe_core_bundle = {\napi_enabled = true\n}",
    ],
)
def test_absent_ambiguous_or_non_literal_source_pin_refuses(source):
    with pytest.raises(ValueError, match="source_pin"):
        guard.runtime_required(source)


def test_image_with_runtime_passes_and_image_without_it_refuses(runtime, monkeypatch):
    root, pin = runtime
    identity = "sha256:" + "1" * 64
    present = True
    calls = []

    def docker(arguments):
        calls.append(arguments)
        operation = arguments[1]
        if operation == "image":
            return json.dumps(
                [{"Id": identity, "Config": {"Labels": {RUNTIME_DIGEST_LABEL: pin}}}]
            )
        if operation == "create":
            assert arguments[-1] == identity
            assert arguments[2:4] == ["--network", "none"]
            return "synthetic-container"
        if operation == "cp":
            if not present:
                raise ValueError("synthetic_missing_runtime")
            shutil.copytree(root, arguments[-1])
        if operation == "run":
            assert "--read-only" in arguments
            assert arguments[arguments.index("--network") + 1] == "none"
            assert arguments[arguments.index("--entrypoint") + 1] == "/bin/sh"
            assert identity in arguments
            assert "cmp /opt/venv/bin/python" in arguments[-1]
        return ""

    monkeypatch.setattr(guard, "command", docker)
    assert guard.verify_image("synthetic-built-image") == pin
    present = False
    with pytest.raises(ValueError, match="synthetic_missing_runtime"):
        guard.verify_image("synthetic-built-image")
    assert calls[-1] == ["docker", "rm", "synthetic-container"]


@pytest.mark.parametrize("compressed", [True, False])
def test_saved_core_layers_preserve_config_and_validate_diff_ids(tmp_path, compressed):
    layer = b"synthetic layer bytes"
    content = gzip.compress(layer) if compressed else layer
    config = oci.encoded({"rootfs": {"diff_ids": [oci.digest(layer)]}})
    manifest = oci.encoded([{"Config": "config.json", "Layers": ["layer.tar"]}])
    path = tmp_path / "image.tar"
    with tarfile.open(path, "w") as archive:
        for name, data in [
            ("config.json", config),
            ("layer.tar", content),
            ("manifest.json", manifest),
        ]:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    image_digest, saved = oci.docker_archive_to_oci(path, tmp_path / "oci")
    rootfs = saved["rootfs"]
    assert isinstance(rootfs, dict)
    assert rootfs["diff_ids"] == [oci.digest(layer)]
    assert image_digest.startswith("sha256:")
    with tarfile.open(path, "w") as archive:
        for name, data in [
            ("config.json", config),
            ("layer.tar", b"altered synthetic layer"),
            ("manifest.json", manifest),
        ]:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    with pytest.raises(ValueError, match="layer_rejected"):
        oci.docker_archive_to_oci(path, tmp_path / "altered-oci")


def test_source_checkout_must_match_pin_before_context_copy(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    import subprocess

    subprocess.run(["git", "init", str(repository)], capture_output=True, check=True)
    with pytest.raises(ValueError):
        source_context(repository, tmp_path / "context")


def test_guard_is_only_in_prepare_workflow_path():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/build_and_publish_local.yml").read_text()
    prepare = workflow.split("  production-candidate:", 1)[1].split("\n  deploy:", 1)[0]
    promote = workflow.split("\n  deploy:", 1)[1]
    assert prepare.index("run: just build") < prepare.index(
        "run: just _scribe_runtime_prepare_check"
    )
    assert prepare.index("run: just _scribe_runtime_prepare_check") < prepare.index(
        "production_core_candidate.py publish"
    )
    assert "_scribe_runtime_prepare_check" not in promote


@pytest.mark.skipif(
    not os.environ.get("CORE_SCRIBE_SYNTHETIC_PLAIN_IMAGE"),
    reason="requires an explicitly supplied local synthetic image",
)
def test_real_docker_synthetic_image_without_runtime_refuses():
    with pytest.raises(ValueError):
        guard.verify_image(os.environ["CORE_SCRIBE_SYNTHETIC_PLAIN_IMAGE"])


@pytest.mark.skipif(
    not os.environ.get("CORE_SCRIBE_SYNTHETIC_BUNDLED_IMAGE"),
    reason="requires an explicitly supplied local synthetic bundled image",
)
def test_real_docker_synthetic_image_with_runtime_passes():
    assert (
        guard.verify_image(os.environ["CORE_SCRIBE_SYNTHETIC_BUNDLED_IMAGE"])
        == guard.SCRIBE_RUNTIME_IMAGE_DIGEST
    )
