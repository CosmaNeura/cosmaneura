from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/staging_offline_tofu_decoder.py"
TOFU_SHA256 = "1334ccf2360fe38460c47fd0924555f411cf714813e541d17d744283c973aa99"
PROVIDER_VERSION = "4.73.0"
PROVIDER_PACKAGE_H1 = "h1:/GrU387kLaubwGZboawLjs4j5FDuVTcR2brU3ONOYxQ="
SOURCE_NAMES = (
    ".terraform.lock.hcl",
    "main.tf",
    "outputs.tf",
    "variables.tf",
    "versions.tf",
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("offline_tofu_decoder", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


decoder = _load_module()


def _provider_lock(
    version: str = PROVIDER_VERSION, *, package_h1: str = PROVIDER_PACKAGE_H1
) -> bytes:
    return (
        f'provider "{decoder.AZURERM_PROVIDER_SOURCE}" {{\n'
        f'  version = "{version}"\n'
        f'  constraints = "{version}"\n'
        "  hashes = [\n"
        f'    "{package_h1}",\n'
        '    "zh:184d59f1d14d45203173459287671c4de3c731beeda42686d42145c7520fe6da",\n'
        "  ]\n"
        "}\n"
    ).encode()


def _decoded_plan_json(
    *, tofu_version: str = "1.12.5", provider_version: str = PROVIDER_VERSION
) -> bytes:
    return json.dumps(
        {
            "format_version": decoder.SUPPORTED_PLAN_FORMAT_VERSION,
            "terraform_version": tofu_version,
            "configuration": {
                "provider_config": {
                    "azurerm": {
                        "name": "azurerm",
                        "full_name": decoder.AZURERM_PROVIDER_SOURCE,
                        "version_constraint": provider_version,
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode()


def _private_directory(path: Path) -> Path:
    path.mkdir(parents=True)
    path.chmod(0o700)
    return path


def _provider_package(tmp_path: Path) -> Path:
    package = _private_directory(
        tmp_path
        / "registry.opentofu.org"
        / "hashicorp"
        / "azurerm"
        / "4.73.0"
        / "darwin_arm64"
    )
    for name in decoder.EXPECTED_PROVIDER_PACKAGE_FILES:
        path = package / name
        path.write_bytes(name.encode())
        path.chmod(0o500 if name == decoder.PROVIDER_EXECUTABLE_NAME else 0o400)
    return package


def _runtime_paths(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    root = _private_directory(tmp_path / "runtime")
    tofu = _private_directory(root / "bin") / "tofu"
    tofu.write_bytes(b"tofu")
    tofu.chmod(0o500)
    source = _private_directory(root / "source")
    data = _private_directory(root / "data")
    plan_directory = _private_directory(root / "plan")
    plan = plan_directory / "plan.tfplan"
    plan.write_bytes(b"plan")
    plan.chmod(0o400)
    for name in ("home", "tmp", "xdg-cache", "xdg-config"):
        _private_directory(root / name)
    return root, tofu, source, data, plan


def test_policy_values_are_closed() -> None:
    valid = {
        "plan_bytes": b"plan",
        "expected_tofu_version": "1.12.5",
        "expected_tofu_sha256": "a" * 64,
        "provider_version": "4.73.0",
        "provider_platform": "darwin_arm64",
        "expected_provider_sha256": "b" * 64,
        "expected_provider_package_h1": PROVIDER_PACKAGE_H1,
    }
    for name, value in (
        ("plan_bytes", b""),
        ("expected_tofu_version", "latest"),
        ("expected_tofu_sha256", "A" * 64),
        ("provider_version", "../4.73.0"),
        ("provider_platform", "linux_amd64"),
        ("expected_provider_sha256", "b" * 63),
        ("expected_provider_package_h1", "h1:not-a-digest"),
    ):
        arguments = dict(valid)
        arguments[name] = value
        with pytest.raises(decoder.OfflineTofuDecoderError):
            decoder._validate_policy_values(**arguments)


def test_provider_package_staging_is_descriptor_relative_and_exact(
    tmp_path: Path,
) -> None:
    package = _provider_package(tmp_path)
    destination_parent = _private_directory(tmp_path / "destination")
    snapshot, provider = decoder._stage_provider_package(
        package,
        destination_parent / "darwin_arm64",
        provider_version="4.73.0",
        provider_platform="darwin_arm64",
        expected_provider_sha256=hashlib.sha256(
            decoder.PROVIDER_EXECUTABLE_NAME.encode()
        ).hexdigest(),
    )
    assert set(snapshot) == (
        decoder.EXPECTED_PROVIDER_PACKAGE_FILES - {decoder.PROVIDER_EXECUTABLE_NAME}
    )
    assert provider.read_bytes() == decoder.PROVIDER_EXECUTABLE_NAME.encode()

    unexpected = package / "unexpected"
    unexpected.write_bytes(b"unexpected")
    unexpected.chmod(0o400)
    with pytest.raises(decoder.OfflineTofuDecoderError, match="not exact"):
        decoder._stage_provider_package(
            package,
            destination_parent / "unexpected",
            provider_version="4.73.0",
            provider_platform="darwin_arm64",
            expected_provider_sha256=hashlib.sha256(
                decoder.PROVIDER_EXECUTABLE_NAME.encode()
            ).hexdigest(),
        )


def test_lock_and_decoded_plan_require_exact_azurerm_binding() -> None:
    decoder._validate_provider_lock(
        _provider_lock(),
        provider_version="4.73.0",
        expected_provider_package_h1=PROVIDER_PACKAGE_H1,
    )
    decoder._validate_decoded_plan_json(
        _decoded_plan_json(),
        expected_tofu_version="1.12.5",
        provider_version="4.73.0",
    )

    for invalid_lock in (
        _provider_lock("4.72.0"),
        _provider_lock().replace(b"h1:", b"xx:", 1),
        _provider_lock() + b'provider "example.invalid/extra" {}\n',
    ):
        with pytest.raises(decoder.OfflineTofuDecoderError, match="provider lock"):
            decoder._validate_provider_lock(
                invalid_lock,
                provider_version="4.73.0",
                expected_provider_package_h1=PROVIDER_PACKAGE_H1,
            )

    invalid_plans = (
        b"not-json",
        _decoded_plan_json(tofu_version="1.12.4"),
        _decoded_plan_json().replace(b'"name":"azurerm"', b'"name":"terraform"'),
    )
    for invalid_plan in invalid_plans:
        with pytest.raises(decoder.OfflineTofuDecoderError):
            decoder._validate_decoded_plan_json(
                invalid_plan,
                expected_tofu_version="1.12.5",
                provider_version="4.73.0",
            )


def test_sandbox_profile_denies_external_network_and_writes(tmp_path: Path) -> None:
    tofu = tmp_path / "bin/tofu"
    provider = tmp_path / "data/providers/provider"
    profile = decoder._sandbox_profile(tmp_path, tofu=tofu, provider=provider)
    assert "(deny default)" in profile
    assert "(deny network*)" in profile
    assert "(deny file-write*)" in profile
    assert "(deny process-exec*)" in profile
    assert "(deny mach*)" in profile
    assert "(deny ipc*)" in profile
    assert f'(literal "{tofu}")' in profile
    assert f'(literal "{provider}")' in profile
    assert profile.count(f'(subpath "{tmp_path / "tmp"}")') == 3
    for name in ("home", "xdg-cache", "xdg-config"):
        assert f'(subpath "{tmp_path / name}")' in profile


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS specific")
def test_sandbox_profile_blocks_unlisted_executable(tmp_path: Path) -> None:
    profile = decoder._sandbox_profile(tmp_path, tofu=Path("/bin/sh"), provider=None)
    completed = subprocess.run(
        [
            str(decoder.SYSTEM_SANDBOX_EXEC),
            "-p",
            profile,
            "/bin/sh",
            "-c",
            "exec /usr/bin/true",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0


def test_decoder_environment_is_minimal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARM_CLIENT_SECRET", "must-not-leak")
    environment = decoder._decoder_environment(
        runtime_root=tmp_path,
        data_dir=tmp_path / "data",
        home=tmp_path / "home",
        temporary=tmp_path / "tmp",
        xdg=tmp_path / "xdg-config",
    )
    assert set(environment) == {
        "CHECKPOINT_DISABLE",
        "HOME",
        "LANG",
        "LC_ALL",
        "TF_CLI_CONFIG_FILE",
        "TF_DATA_DIR",
        "TF_IN_AUTOMATION",
        "TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
    }
    assert "ARM_CLIENT_SECRET" not in environment


def test_process_collection_always_cleans_the_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    cleaned: list[int] = []

    def cleanup(observed: subprocess.Popen[bytes]) -> None:
        cleaned.append(observed.pid)
        observed.wait(timeout=5)

    monkeypatch.setattr(
        decoder,
        "_terminate_process",
        cleanup,
    )
    returncode, stdout, stderr = decoder._collect_process(
        process, timeout_seconds=10, maximum_output_bytes=1024
    )
    assert (returncode, stdout, stderr) == (0, b"", b"")
    assert cleaned == [process.pid]


def test_runner_allows_only_exact_version_and_show(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, tofu, source, data, plan = _runtime_paths(tmp_path)
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def fake_popen(command: tuple[str, ...], **kwargs: Any) -> object:
        calls.append((command, kwargs))
        return object()

    monkeypatch.setattr(decoder.sys, "platform", "darwin")
    monkeypatch.setattr(decoder.os, "uname", lambda: SimpleNamespace(machine="arm64"))
    monkeypatch.setattr(decoder, "_trusted_system_executable", lambda path, **_: path)
    monkeypatch.setattr(
        decoder.offline_runtime,
        "verify_private_executable",
        lambda _path, *, expected, **__: expected,
    )
    monkeypatch.setattr(decoder.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        decoder,
        "_collect_process",
        lambda *_, **__: (0, b'{"terraform_version":"1.12.5"}', b""),
    )

    common = {
        "tofu": tofu,
        "expected_tofu_sha256": "a" * 64,
        "provider": None,
        "expected_provider_sha256": None,
        "runtime_root": root,
        "source": source,
        "data_dir": data,
        "plan": plan,
    }
    decoder._run_sandboxed_tofu("version", **common)
    decoder._run_sandboxed_tofu("show", **common)
    with pytest.raises(decoder.OfflineTofuDecoderError, match="not approved"):
        decoder._run_sandboxed_tofu("init", **common)

    version_command, version_options = calls[0]
    show_command, show_options = calls[1]
    assert version_command[-3:] == (str(tofu), "version", "-json")
    assert show_command[-6:] == (
        str(tofu),
        f"-chdir={source}",
        "show",
        "-json",
        "-no-color",
        str(plan),
    )
    for _command, options in calls:
        assert options["shell"] is False
        assert options["close_fds"] is True
        assert options["stdin"] is subprocess.DEVNULL
        assert "PATH" not in options["env"]
        assert all(
            not name.startswith(("ARM_", "AZURE_", "TF_VAR_"))
            for name in options["env"]
        )


def test_decode_materializes_and_rechecks_exact_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_parent = _private_directory(tmp_path / "private")
    provider_package = _provider_package(tmp_path / "cache")
    tofu_source = tmp_path / "tofu"
    tofu_source.write_bytes(b"tofu")
    tofu_source.chmod(0o500)
    provider_digest = hashlib.sha256(
        decoder.PROVIDER_EXECUTABLE_NAME.encode()
    ).hexdigest()
    provider_files = {
        name: name.encode()
        for name in decoder.EXPECTED_PROVIDER_PACKAGE_FILES
        if name != decoder.PROVIDER_EXECUTABLE_NAME
    }
    provider_package_h1 = decoder._provider_package_h1(
        provider_files, provider_sha256=provider_digest
    )
    calls: list[str] = []

    def fake_copy(
        source: Path, destination: Path, *, expected: str, label: str, **_: Any
    ) -> Path:
        destination.write_bytes(source.read_bytes())
        destination.chmod(0o500)
        return destination

    def fake_run(operation: str, **_: Any) -> bytes:
        calls.append(operation)
        if operation == "version":
            return b'{"terraform_version":"1.12.5"}'
        return _decoded_plan_json()

    monkeypatch.setattr(decoder.offline_runtime, "copy_pinned_executable", fake_copy)
    monkeypatch.setattr(
        decoder.offline_runtime,
        "verify_private_executable",
        lambda _path, *, expected, **__: expected,
    )
    monkeypatch.setattr(decoder, "_run_sandboxed_tofu", fake_run)
    monkeypatch.setattr(decoder, "_trusted_temporary_root", lambda: runtime_parent)
    result = decoder.decode_saved_plan(
        b"plan",
        terraform_files={
            "main.tf": b"terraform {}\n",
            ".terraform.lock.hcl": _provider_lock(package_h1=provider_package_h1),
        },
        provider_package=provider_package,
        tofu_source=tofu_source,
        expected_tofu_version="1.12.5",
        expected_tofu_sha256="a" * 64,
        provider_version="4.73.0",
        provider_platform="darwin_arm64",
        expected_provider_sha256=provider_digest,
        expected_provider_package_h1=provider_package_h1,
    )
    assert calls == ["version", "show"]
    assert result.plan_json == _decoded_plan_json()
    assert result.tofu_sha256 == "a" * 64
    assert result.provider_sha256 == provider_digest
    assert result.provider_package_h1 == provider_package_h1
    assert list(runtime_parent.iterdir()) == []


def test_decode_requires_lockfile_and_terraform_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(decoder, "_trusted_temporary_root", lambda: tmp_path)
    common = {
        "plan_bytes": b"plan",
        "provider_package": Path("/not-reached"),
        "tofu_source": Path("/not-reached"),
        "expected_tofu_version": "1.12.5",
        "expected_tofu_sha256": "a" * 64,
        "provider_version": "4.73.0",
        "provider_platform": "darwin_arm64",
        "expected_provider_sha256": "b" * 64,
        "expected_provider_package_h1": PROVIDER_PACKAGE_H1,
    }
    for source_files in (
        {"main.tf": b"terraform {}\n"},
        {".terraform.lock.hcl": b"lock"},
        {".terraform.lock.hcl": b"lock", "unexpected.txt": b"value"},
    ):
        with pytest.raises(decoder.OfflineTofuDecoderError, match="source file set"):
            decoder.decode_saved_plan(
                terraform_files=source_files,
                **common,
            )


@pytest.mark.skipif(
    sys.platform != "darwin" or os.uname().machine != "arm64",
    reason="the production decoder is intentionally Darwin arm64 specific",
)
def test_real_opentofu_show_runs_inside_the_network_denied_sandbox(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    executable = shutil.which("tofu")
    if executable is None:
        pytest.skip("OpenTofu is not installed")
    if (
        hashlib.sha256(Path(executable).resolve().read_bytes()).hexdigest()
        != TOFU_SHA256
    ):
        pytest.skip("test requires the pinned OpenTofu executable")
    short_temporary = tempfile.TemporaryDirectory(
        prefix=".cn-otf-test-", dir="/private/tmp"
    )
    request.addfinalizer(short_temporary.cleanup)
    tmp_path = Path(short_temporary.name)
    module = _private_directory(tmp_path / "module")
    source_bytes = (
        b'terraform { required_version = "= 1.12.5" }\n'
        b'resource "terraform_data" "probe" { input = "bound" }\n'
    )
    source_file = module / "main.tf"
    source_file.write_bytes(source_bytes)
    plan = tmp_path / "real.tfplan"
    for command in (
        [executable, f"-chdir={module}", "init", "-backend=false", "-input=false"],
        [executable, f"-chdir={module}", "plan", "-input=false", f"-out={plan}"],
    ):
        subprocess.run(command, check=True, capture_output=True)

    runtime = _private_directory(tmp_path / "runtime")
    bin_directory = _private_directory(runtime / "bin")
    private_tofu = decoder.offline_runtime.copy_pinned_executable(
        Path(executable),
        bin_directory / "tofu",
        expected=TOFU_SHA256,
        label="OpenTofu",
    )
    source = runtime / "source"
    decoder.offline_runtime.materialize_source(source, {"main.tf": source_bytes})
    data = _private_directory(runtime / "data")
    plan_directory = runtime / "plan"
    decoder.offline_runtime.materialize_source(
        plan_directory, {"plan.tfplan": plan.read_bytes()}
    )
    for name in ("home", "tmp", "xdg-cache", "xdg-config"):
        _private_directory(runtime / name)
    _private_directory(runtime / "xdg-config/opentofu")

    decoded = decoder._run_sandboxed_tofu(
        "show",
        tofu=private_tofu,
        expected_tofu_sha256=TOFU_SHA256,
        provider=None,
        expected_provider_sha256=None,
        runtime_root=runtime,
        source=source,
        data_dir=data,
        plan=plan_directory / "plan.tfplan",
    )
    parsed = json.loads(decoded)
    assert parsed["terraform_version"] == "1.12.5"
    assert parsed["resource_changes"][0]["address"] == "terraform_data.probe"


@pytest.mark.skipif(
    sys.platform != "darwin" or os.uname().machine != "arm64",
    reason="the production decoder is intentionally Darwin arm64 specific",
)
def test_real_saved_plan_authenticates_complete_azurerm_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_value = os.environ.get("COSMANEURA_TEST_SAVED_PLAN")
    package_value = os.environ.get("COSMANEURA_TEST_AZURERM_PACKAGE")
    executable = shutil.which("tofu")
    if not plan_value or not package_value or executable is None:
        pytest.skip("real saved-plan evidence paths are not configured")
    if (
        hashlib.sha256(Path(executable).resolve().read_bytes()).hexdigest()
        != TOFU_SHA256
    ):
        pytest.skip("test requires the pinned OpenTofu executable")
    source_directory = ROOT / "infra-staging-sanitizer-bootstrap-identity"
    terraform_files = {
        name: (source_directory / name).read_bytes() for name in SOURCE_NAMES
    }
    provider_source = Path(package_value)
    plan_bytes = Path(plan_value).read_bytes()
    provider_digest = hashlib.sha256(
        (provider_source / decoder.PROVIDER_EXECUTABLE_NAME).read_bytes()
    ).hexdigest()
    decoded = decoder.decode_saved_plan(
        plan_bytes,
        terraform_files=terraform_files,
        provider_package=provider_source,
        tofu_source=Path(executable),
        expected_tofu_version="1.12.5",
        expected_tofu_sha256=TOFU_SHA256,
        provider_version=PROVIDER_VERSION,
        provider_platform="darwin_arm64",
        expected_provider_sha256=provider_digest,
        expected_provider_package_h1=PROVIDER_PACKAGE_H1,
    )
    decoded_json = json.loads(decoded.plan_json)
    assert decoded_json["format_version"] == decoder.SUPPORTED_PLAN_FORMAT_VERSION
    assert decoded_json["terraform_version"] == "1.12.5"

    package = (
        tmp_path
        / "tampered"
        / "providers"
        / "registry.opentofu.org"
        / "hashicorp"
        / "azurerm"
        / PROVIDER_VERSION
        / "darwin_arm64"
    )
    shutil.copytree(provider_source, package)
    readme = package / "README.md"
    readme.chmod(0o600)
    readme.write_bytes(readme.read_bytes() + b"\ntampered for checksum test\n")
    readme.chmod(0o400)
    operations: list[str] = []
    real_runner = decoder._run_sandboxed_tofu

    def observed_runner(operation: str, **kwargs: Any) -> bytes:
        operations.append(operation)
        return real_runner(operation, **kwargs)

    monkeypatch.setattr(decoder, "_run_sandboxed_tofu", observed_runner)
    with pytest.raises(decoder.OfflineTofuDecoderError, match="package did not match"):
        decoder.decode_saved_plan(
            plan_bytes,
            terraform_files=terraform_files,
            provider_package=package,
            tofu_source=Path(executable),
            expected_tofu_version="1.12.5",
            expected_tofu_sha256=TOFU_SHA256,
            provider_version=PROVIDER_VERSION,
            provider_platform="darwin_arm64",
            expected_provider_sha256=provider_digest,
            expected_provider_package_h1=PROVIDER_PACKAGE_H1,
        )
    assert operations == []

    monkeypatch.setattr(
        decoder, "_provider_package_h1", lambda *_args, **_kwargs: PROVIDER_PACKAGE_H1
    )
    with pytest.raises(decoder.OfflineTofuDecoderError, match="show failed"):
        decoder.decode_saved_plan(
            plan_bytes,
            terraform_files=terraform_files,
            provider_package=package,
            tofu_source=Path(executable),
            expected_tofu_version="1.12.5",
            expected_tofu_sha256=TOFU_SHA256,
            provider_version=PROVIDER_VERSION,
            provider_platform="darwin_arm64",
            expected_provider_sha256=provider_digest,
            expected_provider_package_h1=PROVIDER_PACKAGE_H1,
        )
    assert operations == ["version", "show"]
