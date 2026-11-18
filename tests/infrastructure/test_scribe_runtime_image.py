"""Synthetic runtime/image snapshots exercise presence, permissions and OCI pin."""

from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts import build_scribe_runtime as builder
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
    subprocess.run(["git", "init", str(repository)], capture_output=True, check=True)
    for key, value in [
        ("user.name", "Synthetic Test"),
        ("user.email", "synthetic@example.invalid"),
        ("commit.gpgsign", "false"),
    ]:
        subprocess.run(["git", "-C", str(repository), "config", key, value], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "commit",
            "--allow-empty",
            "-m",
            "Synthetic non-pin commit",
        ],
        capture_output=True,
        check=True,
    )
    assert (
        builder.command(["git", "-C", str(repository), "rev-parse", "HEAD"])
        != builder.SCRIBE_SOURCE_REVISION
    )
    with pytest.raises(ValueError, match="scribe_runtime_source_revision_rejected"):
        source_context(repository, tmp_path / "context")


@pytest.mark.parametrize("revision", ["a" * 40 + "-dirty", "a" * 39, ""])
def test_core_source_revision_must_be_immutable_before_source_fetch(
    monkeypatch, revision
):
    calls = []
    monkeypatch.setattr(
        builder, "command", lambda arguments: calls.append(arguments) or ""
    )
    monkeypatch.setattr(
        builder,
        "docker_archive_to_oci",
        lambda *args: (
            "sha256:" + "1" * 64,
            {
                "architecture": "amd64",
                "os": "linux",
                "config": {
                    "Labels": {"org.opencontainers.image.revision": revision},
                    "User": "0:0",
                },
            },
        ),
    )
    with pytest.raises(ValueError, match="core_revision_rejected"):
        builder.build("synthetic-core-image", "synthetic-output-image")
    assert len(calls) == 1
    assert calls[0][:3] == ["docker", "image", "save"]


def test_guard_is_only_in_prepare_workflow_path():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/build_and_publish_local.yml").read_text()
    prepare = workflow.split("  production-candidate:", 1)[1].split("\n  deploy:", 1)[0]
    promote = workflow.split("\n  deploy:", 1)[1]
    assert prepare.index("run: just build") < prepare.index(
        "run: just _scribe_runtime_prepare_check"
    )
    assert prepare.index("run: just _scribe_runtime_prepare_check") < prepare.index(
        "azure/login@"
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


@pytest.fixture
def synthetic_builder(tmp_path, monkeypatch):
    """No Docker or Scribe checkout: exercise production orchestration with fake IO."""
    calls = []
    config = {
        "architecture": "amd64",
        "os": "linux",
        "config": {
            "Labels": {"org.opencontainers.image.revision": "a" * 40},
            "User": "0:0",
        },
    }
    state = {"tags": [], "module": b"synthetic reviewed module\n"}
    monkeypatch.setattr(
        builder, "RUNTIME_SHA256", oci.digest(state["module"]).split(":")[1]
    )
    monkeypatch.setattr(
        builder, "docker_archive_to_oci", lambda *args: ("sha256:" + "1" * 64, config)
    )
    monkeypatch.setattr(builder, "source_context", lambda *args: None)

    def command(arguments):
        calls.append(arguments)
        if arguments[:3] == ["docker", "buildx", "build"] and "--target" in arguments:
            path = arguments[arguments.index("--output") + 1].split("dest=", 1)[1]
            with tarfile.open(path, "w") as archive:
                for name, data in [
                    (
                        "manifest.json",
                        json.dumps(
                            [{"Config": "config.json", "RepoTags": state["tags"]}]
                        ).encode(),
                    ),
                    ("config.json", b"{}"),
                ]:
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    archive.addfile(entry, io.BytesIO(data))
        if arguments[:3] == ["docker", "image", "load"]:
            return "Loaded image ID: sha256:" + "2" * 64
        if arguments[:2] == ["docker", "create"]:
            return "synthetic-container"
        if arguments[:2] == ["docker", "cp"]:
            runtime = Path(arguments[-1])
            (runtime / "bin").mkdir(parents=True)
            runtime.chmod(0o755)
            launcher = runtime / "bin/python"
            launcher.write_text("#!/bin/sh\nexit 0\n")
            launcher.chmod(0o755)
            module = (
                runtime
                / "lib/python3.13/site-packages/cosmaneura_scribe"
                / Path(builder.MODULE_PATH).name
            )
            module.parent.mkdir(parents=True)
            module.write_bytes(state["module"])
            (runtime / "bin/link").symlink_to("python")
        return ""

    monkeypatch.setattr(builder, "command", command)
    return calls, config, state


@pytest.mark.parametrize("mode", ["build", "derive"])
@pytest.mark.parametrize("refusal", ["platform", "user", "RepoTags", "module"])
def test_real_builder_path_refusals_bound_without_docker(
    synthetic_builder, tmp_path, mode, refusal
):
    calls, config, state = synthetic_builder
    if refusal == "platform":
        config["architecture"] = "arm64"
        error = "scribe_runtime_core_platform_rejected"
    elif refusal == "user":
        config["config"]["User"] = "named-user"
        error = "scribe_runtime_core_user_rejected"
    elif refusal == "RepoTags":
        state["tags"] = ["synthetic:unreviewed"]
        error = "scribe_runtime_build_archive_rejected"
    else:
        state["module"] = b"synthetic unreviewed module\n"
        error = "scribe_runtime_built_module_rejected"
    with pytest.raises(ValueError, match=error):
        if mode == "derive":
            builder.derive_runtime_digest("synthetic-core", tmp_path / "manifest.txt")
        else:
            builder.build("synthetic-core", "synthetic-output")
    assert not any("--tag" in call or "--load" in call for call in calls)
    assert not any(
        call[:3] == ["docker", "buildx", "build"] and "--target" not in call
        for call in calls
    )


def test_failing_command_reports_only_argv_head_and_bounded_stderr_tail(
    monkeypatch, capsys
):
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "synthetic-scrubbed-token")
    stderr = "discarded synthetic line\n" + "\n".join(
        f"synthetic stderr {index}: " + "x" * 220 for index in range(20)
    )

    def run(arguments, **kwargs):
        assert "OP_SERVICE_ACCOUNT_TOKEN" not in kwargs["env"]
        return subprocess.CompletedProcess(arguments, 17, stdout="", stderr=stderr)

    monkeypatch.setattr(guard.subprocess, "run", run)
    with pytest.raises(ValueError, match="^scribe_runtime_command_rejected$"):
        guard.command(["docker", "buildx", "build", "synthetic-private-argument"])
    captured = capsys.readouterr()
    assert captured.out == ""
    head = "docker buildx build: exit code 17\n"
    tail = "\n".join(stderr.splitlines()[-20:])[-4000:]
    assert captured.err == head + tail + "\n"
    assert "synthetic-private-argument" not in captured.err
    assert "discarded synthetic line" not in captured.err
    assert "synthetic-scrubbed-token" not in captured.err


def test_build_cli_refusal_reports_code_to_stderr(monkeypatch, capsys):
    def refuse(*args):
        raise ValueError("scribe_runtime_command_rejected")

    monkeypatch.setattr(builder, "build", refuse)
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_scribe_runtime",
            "--core-image",
            "synthetic-core",
            "--output-image",
            "synthetic-output",
        ],
    )
    assert builder.main() == 1
    captured = capsys.readouterr()
    assert captured.out == "scribe_runtime_build_refused\n"
    assert captured.err == "ValueError: scribe_runtime_command_rejected\n"


def test_derive_cli_uses_same_helper_without_layer_or_tags(
    synthetic_builder, tmp_path, monkeypatch, capsys
):
    calls, _, _ = synthetic_builder
    manifest = tmp_path / "manifest.txt"
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_scribe_runtime",
            "--core-image",
            "synthetic-core",
            "--derive-runtime-digest",
            "--manifest-out",
            str(manifest),
        ],
    )
    assert builder.main() == 0
    output = capsys.readouterr().out.strip()
    assert output.startswith("sha256:") and len(output) == 71
    entries = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert entries[0] == {"path": ".", "type": "directory", "mode": "0755", "size": 0}
    assert next(e for e in entries if e["path"] == "bin/python")["mode"] == "0755"
    assert next(e for e in entries if e["path"] == "bin/link")["target"] == "python"
    assert all({"path", "type", "mode", "size"} <= e.keys() for e in entries)
    builds = [call for call in calls if call[:3] == ["docker", "buildx", "build"]]
    assert len(builds) == 1 and "scribe-builder" in builds[0]
    assert not any("--tag" in call or "--load" in call for call in calls)


def test_build_mismatched_pin_refuses_before_copy(synthetic_builder, monkeypatch):
    calls, _, _ = synthetic_builder
    monkeypatch.setattr(guard, "SCRIBE_RUNTIME_IMAGE_DIGEST", "sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="scribe_runtime_layer_digest_rejected"):
        builder.build("synthetic-core", "synthetic-output")
    assert (
        len([call for call in calls if call[:3] == ["docker", "buildx", "build"]]) == 1
    )


def test_build_verifies_runtime_before_copy(synthetic_builder, monkeypatch):
    calls, _, _ = synthetic_builder
    verified = []

    def verify(runtime, actual):
        assert not any(
            call[:3] == ["docker", "buildx", "build"] and "--target" not in call
            for call in calls
        )
        verified.append((runtime, actual))
        return actual

    monkeypatch.setattr(builder, "verify_runtime", verify)
    actual = builder.build("synthetic-core", "synthetic-output")
    assert len(verified) == 1 and verified[0][1] == actual
    layer_calls = [
        call
        for call in calls
        if call[:3] == ["docker", "buildx", "build"] and "--target" not in call
    ]
    assert len(layer_calls) == 1 and "--tag" in layer_calls[0]


@pytest.mark.parametrize("matching", [True, False])
def test_image_guard_behaviorally_checks_core_python_closure(
    runtime, tmp_path, monkeypatch, matching
):
    root, pin = runtime
    core_python = tmp_path / "core-python"
    runtime_python = root / "bin/python-real"
    core_python.write_bytes(
        runtime_python.read_bytes() if matching else b"different synthetic closure"
    )
    launcher = root / "bin/python"
    identity = "sha256:" + "1" * 64
    checked = []

    def command(arguments):
        if arguments[:2] == ["docker", "image"]:
            return json.dumps(
                [{"Id": identity, "Config": {"Labels": {RUNTIME_DIGEST_LABEL: pin}}}]
            )
        if arguments[:2] == ["docker", "create"]:
            return "synthetic-container"
        if arguments[:2] == ["docker", "cp"]:
            shutil.copytree(root, arguments[-1])
        if arguments[:2] == ["docker", "run"]:
            shell = (
                arguments[-1]
                .replace("/opt/venv/bin/python", str(core_python))
                .replace("/opt/scribe/runtime/bin/python-real", str(runtime_python))
                .replace("/opt/scribe/runtime/bin/python", str(launcher))
            )
            checked.append(True)
            result = subprocess.run(["/bin/sh", "-c", shell], capture_output=True)
            if result.returncode:
                raise ValueError("scribe_runtime_command_rejected")
        return ""

    monkeypatch.setattr(guard, "command", command)
    if matching:
        assert guard.verify_image("synthetic-core") == pin
    else:
        with pytest.raises(ValueError, match="scribe_runtime_command_rejected"):
            guard.verify_image("synthetic-core")
    assert checked == [True]


def test_live_release_guard_after_build_before_publish_and_azure():
    root = Path(__file__).resolve().parents[2]
    source = (root / ".github/workflows/build_and_publish_local.yml").read_text()
    guard_index = source.index("run: just _scribe_runtime_prepare_check")
    assert (
        source.index("run: just build\n")
        < guard_index
        < source.index("python3 scripts/production_core_candidate.py publish")
    )
    assert guard_index < source.index("azure/login@")


@pytest.mark.parametrize(
    "workflow",
    [
        "build_and_publish_local",
        "infra_apply_local",
        "infra_production_plan_local",
    ],
)
def test_runtime_scripts_bound_to_every_applicability_and_configuration_list(workflow):
    root = Path(__file__).resolve().parents[2]
    source = (root / f".github/workflows/{workflow}.yml").read_text()
    import re

    lists = re.findall(
        r"git (?:-C \.\. )?(?:diff --quiet[^\n]* --|ls-files -z --) \\\n([\s\S]*?)(?:; then| \|)",
        source,
    )
    assert lists, "workflow must retain its applicability/configuration path list"
    for paths in lists:
        for path in [
            "scripts/build_scribe_runtime.py",
            "scripts/scribe_runtime_check.py",
            "scripts/scribe_runtime_lineage.py",
            "scripts/scribe_runtime_oci.py",
            "scripts/scribe_runtime_pin.py",
        ]:
            assert path in paths.split(), (workflow, path)
