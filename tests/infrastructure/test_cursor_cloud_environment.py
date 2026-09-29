from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = ROOT / "scripts/assert_cursor_local_database.py"
DATABASE_HELPER = ROOT / ".cursor/database.sh"

DEV_LOCALHOST = "postgresql://root:password@localhost:5432/development"
TEST_LOCALHOST = "postgresql://root:password@localhost:5432/test"

SPEC = importlib.util.spec_from_file_location(
    "assert_cursor_local_database", GUARD_PATH
)
assert SPEC is not None and SPEC.loader is not None
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _database_environment(
    *,
    mode: str,
    database_url: str = DEV_LOCALHOST,
    test_database_url: str = TEST_LOCALHOST,
) -> dict[str, str]:
    return {
        "COSMANEURA_CURSOR_CREDENTIAL_NEUTRAL": "1",
        "CI": "" if mode == "dev" else "true",
        "PYTHON_ENV": "development" if mode == "dev" else "test",
        "DATABASE_URL": database_url,
        "TEST_DATABASE_URL": test_database_url,
    }


def _write_fake_direnv(
    tmp_path: Path,
    *,
    database_url: str = DEV_LOCALHOST,
    test_database_url: str,
) -> Path:
    (tmp_path / "python").symlink_to(sys.executable)
    fake_direnv = tmp_path / "direnv"
    _write_executable(
        fake_direnv,
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        '[[ "$1" == "exec" && "$2" == "." ]]\nshift 2\n'
        'export PYTHON_ENV="test"\n'
        f'export DATABASE_URL="{database_url}"\n'
        f'export TEST_DATABASE_URL="{test_database_url}"\nexec "$@"\n',
    )
    fake_mise = tmp_path / "mise"
    _write_executable(
        fake_mise,
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        '[[ "$1" == "exec" && "$2" == "--" ]]\nshift 2\nexec "$@"\n',
    )
    return fake_direnv


def _run_test_boundary(
    tmp_path: Path,
    *,
    database_url: str = DEV_LOCALHOST,
    test_database_url: str,
) -> tuple[int, str]:
    _write_fake_direnv(
        tmp_path,
        database_url=database_url,
        test_database_url=test_database_url,
    )
    marker = tmp_path / "executed"
    command = "\n".join(
        (
            f"source {shlex.quote(str(DATABASE_HELPER))}",
            "cursor_database_test bash -c "
            + shlex.quote('printf "%s" "$TEST_DATABASE_URL" > "$1"')
            + f" _ {shlex.quote(str(marker))}",
        )
    )
    environment = dict(os.environ)
    environment["PATH"] = f"{tmp_path}:{environment['PATH']}"
    environment["COSMANEURA_CURSOR_CREDENTIAL_NEUTRAL"] = "1"
    environment["CURSOR_MISE_BIN"] = str(tmp_path / "mise")
    environment["CURSOR_DIRENV_BIN"] = str(tmp_path / "direnv")
    environment["CURSOR_PYTHON_BIN"] = str(tmp_path / "python")
    result = _run(["bash", "-c", command], environment=environment)
    marker_value = marker.read_text(encoding="utf-8") if marker.exists() else ""
    return result.returncode, marker_value + result.stderr


def _sandbox_dispatcher(tmp_path: Path) -> Path:
    sandbox_cursor = tmp_path / ".cursor"
    sandbox_cursor.mkdir()
    shutil.copyfile(
        ROOT / ".cursor/credential-neutral.sh",
        sandbox_cursor / "credential-neutral.sh",
    )
    return sandbox_cursor


def _run_sandbox_dispatcher(
    sandbox_cursor: Path,
    *command: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = command or ("/bin/true",)
    shell = "\n".join(
        (
            f"source {shlex.quote(str(sandbox_cursor / 'credential-neutral.sh'))}",
            "cursor_exec_credential_neutral "
            + " ".join(shlex.quote(part) for part in command),
        )
    )
    return _run(
        ["bash", "-c", shell],
        cwd=sandbox_cursor.parent,
        environment=environment,
    )


def test_mise_install_is_version_and_digest_pinned(tmp_path: Path) -> None:
    installer = (ROOT / "scripts/install_pinned_mise.sh").read_text(encoding="utf-8")
    assert 'MISE_VERSION="v2026.9.1"' in installer
    assert (
        'MISE_ARCHIVE_SHA256="063dda9149ab6be53da877c2d176afe0eac68e64cf8ca295bd0528720701c65d"'
        in installer
    )
    assert (
        'MISE_BINARY_SHA256="c98423c8470d6dc416d9f7036d0646d8ef5ae92ad9186907f8fcc84cbe7db4ea"'
        in installer
    )
    assert "releases/latest" not in installer
    assert "mise.run" not in installer
    assert installer.count("sha256sum --check --strict") == 3
    system_setup = (ROOT / ".cursor/system-setup.sh").read_text(encoding="utf-8")
    assert 'bash "$CURSOR_SETUP_DIR/../scripts/install_pinned_mise.sh"' in system_setup
    assert "mise.run" not in system_setup
    dispatcher = (ROOT / ".cursor/credential-neutral.sh").read_text(encoding="utf-8")
    library = (ROOT / ".cursor/lib.sh").read_text(encoding="utf-8")
    assert (
        'CURSOR_MISE_BINARY_SHA256="c98423c8470d6dc416d9f7036d0646d8ef5ae92ad9186907f8fcc84cbe7db4ea"'
        in dispatcher
    )
    assert "/usr/bin/sha256sum" in dispatcher
    assert "/usr/bin/shasum -a 256" in dispatcher
    assert 'cursor_require_exact_mise_binary "$CURSOR_MISE_BIN"' in library
    assert "mise activate" not in library
    assert "mise/shims:$PATH" not in library
    assert '"$CURSOR_DIRENV_BIN" exec .' in library
    installs = tmp_path / "installs"
    managed = installs / "uv/0.7.15/bin/uv"
    managed.parent.mkdir(parents=True)
    _write_executable(managed, "#!/bin/sh\nexit 0\n")
    fake_mise = tmp_path / "mise"
    _write_executable(fake_mise, '#!/bin/sh\nprintf "%s\\n" "$FAKE_TOOL_PATH"\n')
    command = (
        f"source {shlex.quote(str(ROOT / '.cursor/credential-neutral.sh'))}; "
        "cursor_mise_executable uv"
    )
    tool_environment = os.environ | {
        "CURSOR_MISE_BIN": str(fake_mise),
        "MISE_INSTALLS_DIR": str(installs),
        "FAKE_TOOL_PATH": str(managed),
    }
    assert _run(["bash", "-c", command], environment=tool_environment).returncode == 0
    escaped = tmp_path / "escaped"
    _write_executable(escaped, "#!/bin/sh\nexit 0\n")
    managed.unlink()
    managed.symlink_to(escaped)
    rejected = _run(["bash", "-c", command], environment=tool_environment)
    assert rejected.returncode != 0
    assert "escapes the isolated install root" in rejected.stderr
    candidate = tmp_path / "mise"
    shutil.copyfile("/usr/bin/true", candidate)
    candidate.chmod(0o755)
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    result = _run(
        [
            "/bin/bash",
            "-c",
            f"source {shlex.quote(str(ROOT / '.cursor/credential-neutral.sh'))}; "
            f"CURSOR_MISE_BINARY_SHA256={digest}; "
            f"cursor_require_exact_mise_binary {shlex.quote(str(candidate))}",
        ],
    )
    assert result.returncode == 0, result.stderr


def test_docker_apt_key_requires_exact_sole_primary_fingerprint() -> None:
    installer = (ROOT / "scripts/install_verified_docker_apt_key.sh").read_text(
        encoding="utf-8"
    )
    required = (
        'DOCKER_APT_KEY_PRIMARY_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"',
        'DOCKER_APT_KEY_URL="https://download.docker.com/linux/ubuntu/gpg"',
        'fingerprints="$(docker_primary_fingerprints "${key_path}")"',
        '[[ "${fingerprints}" != "${DOCKER_APT_KEY_PRIMARY_FINGERPRINT}" ]]',
        'require_exact_docker_primary_fingerprint "${docker_key_download}"',
        'require_exact_docker_primary_fingerprint "${docker_key_dearmored}"',
        'sudo mv -f -- "${docker_key_target_tmp}" "${DOCKER_APT_KEY_PATH}"',
    )
    assert all(snippet in installer for snippet in required)
    assert "curl -fsSL" not in installer
    assert "| sudo gpg" not in installer
    system_setup = (ROOT / ".cursor/system-setup.sh").read_text(encoding="utf-8")
    assert (
        'bash "$CURSOR_SETUP_DIR/../scripts/install_verified_docker_apt_key.sh"'
        in system_setup
    )
    assert "download.docker.com/linux/ubuntu/gpg |" not in system_setup


def test_environment_entrypoints_use_only_credential_neutral_dispatcher() -> None:
    environment = json.loads(
        (ROOT / ".cursor/environment.json").read_text(encoding="utf-8")
    )
    prefix = "/usr/bin/env -i /bin/bash --noprofile --norc --"
    assert environment["install"] == (f"{prefix} .cursor/credential-neutral.sh install")
    assert environment["start"] == f"{prefix} .cursor/credential-neutral.sh start"
    assert [terminal["command"] for terminal in environment["terminals"]] == [
        f"{prefix} .cursor/credential-neutral.sh backend",
        f"{prefix} .cursor/credential-neutral.sh frontend",
    ]
    install = (ROOT / ".cursor/install.sh").read_text(encoding="utf-8")
    system_setup = (ROOT / ".cursor/system-setup.sh").read_text(encoding="utf-8")
    library = (ROOT / ".cursor/lib.sh").read_text(encoding="utf-8")
    dispatcher = (ROOT / ".cursor/credential-neutral.sh").read_text(encoding="utf-8")
    assert "cursor_require_credential_neutral" in install
    assert "cursor_require_credential_neutral" in system_setup
    assert "cursor_require_credential_neutral" in library
    assert (
        'cursor_exec_credential_neutral /bin/bash --noprofile --norc -- "${cursor_target}"'
        in dispatcher
    )


def test_environment_entrypoints_clear_hostile_bash_env_before_repo_bash(
    tmp_path: Path,
) -> None:
    sandbox_cursor = tmp_path / ".cursor"
    sandbox_cursor.mkdir()
    probe = sandbox_cursor / "credential-neutral.sh"
    probe.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf "mode=%s\\n" "$1"
printf "bash_env=%s\\n" "${BASH_ENV-unset}"
printf "sentinel=%s\\n" "${INHERITED_SENTINEL_SECRET-unset}"
printf "shellopts=%s\\n" "$SHELLOPTS"
printf "ps4=%s\\n" "$PS4"
if declare -F cursor_hostile_function >/dev/null; then
  cursor_hostile_function
  printf 'function=present\\n'
else
  printf 'function=absent\\n'
fi
""",
        encoding="utf-8",
    )
    attack_marker = tmp_path / "bash-env-executed"
    hostile_bash_env = tmp_path / "hostile-bash-env.sh"
    hostile_bash_env.write_text(
        f"printf executed > {shlex.quote(str(attack_marker))}\n"
        "export HOSTILE_BASH_ENV_RAN=1\n",
        encoding="utf-8",
    )
    inherited_environment = dict(os.environ)
    inherited_environment.update(
        {
            "BASH_ENV": str(hostile_bash_env),
            "BASH_FUNC_cursor_hostile_function%%": (
                f"() {{ printf executed > {shlex.quote(str(attack_marker))}; }}"
            ),
            "INHERITED_SENTINEL_SECRET": "must-not-survive",
            "PS4": "hostile-ps4-must-not-survive",
            "SHELLOPTS": "braceexpand:hashall:interactive-comments:xtrace",
        }
    )
    environment = json.loads(
        (ROOT / ".cursor/environment.json").read_text(encoding="utf-8")
    )
    commands = {
        "install": environment["install"],
        "start": environment["start"],
    }
    commands.update(
        {item["name"]: item["command"] for item in environment["terminals"]}
    )
    for mode, command in commands.items():
        command_parts = shlex.split(command)
        prefix = "/usr/bin/env -i /bin/bash --noprofile --norc --"
        assert command_parts[:6] == shlex.split(prefix)
        result = _run(
            command_parts,
            cwd=tmp_path,
            environment=inherited_environment,
        )
        assert result.returncode == 0, result.stderr
        assert f"mode={mode}" in result.stdout
        assert "bash_env=unset" in result.stdout
        assert "sentinel=unset" in result.stdout
        assert "function=absent" in result.stdout
        for escaped_value in (
            ":xtrace:",
            "hostile-ps4-must-not-survive",
            "must-not-survive",
        ):
            assert escaped_value not in result.stdout
        assert not attack_marker.exists()


def test_dispatcher_resolves_symlinked_checkout_physically(tmp_path: Path) -> None:
    physical = tmp_path / "physical/repo"
    cursor = physical / ".cursor"
    cursor.mkdir(parents=True)
    shutil.copyfile(
        ROOT / ".cursor/credential-neutral.sh", cursor / "credential-neutral.sh"
    )
    _write_executable(
        cursor / "start.sh",
        '#!/bin/bash\nprintf "pwd=%s\\nceiling=%s\\ntrusted=%s\\n" '
        '"$PWD" "$MISE_CEILING_PATHS" "$MISE_TRUSTED_CONFIG_PATHS"\n',
    )
    logical = tmp_path / "logical-repo"
    logical.symlink_to(physical, target_is_directory=True)
    command = "/usr/bin/env -i /bin/bash --noprofile --norc --"
    dispatcher = logical / ".cursor/credential-neutral.sh"
    result = _run([*shlex.split(command), str(dispatcher), "start"], cwd=logical)
    assert result.returncode == 0, result.stderr
    assert f"pwd={physical}" in result.stdout
    assert f"ceiling={physical.parent}" in result.stdout
    assert f"trusted={physical}" in result.stdout


@pytest.mark.parametrize(
    "entrypoint",
    ["install.sh", "start.sh", "run-backend.sh", "run-frontend.sh", "system-setup.sh"],
)
def test_cursor_targets_refuse_direct_execution_without_marker(
    entrypoint: str,
) -> None:
    environment = {
        "HOME": os.environ["HOME"],
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }
    result = _run(
        ["bash", str(ROOT / ".cursor" / entrypoint)],
        environment=environment,
    )
    assert result.returncode != 0
    assert "must run through .cursor/credential-neutral.sh" in result.stderr


def test_credential_neutral_dispatcher_scrubs_inherited_secrets_and_blocks_op(
    tmp_path: Path,
) -> None:
    inherited_home = tmp_path / "real-home"
    (inherited_home / ".docker").mkdir(parents=True)
    (inherited_home / ".npmrc").write_text(
        "//registry.npmjs.org/:_authToken=must-not-survive\n", encoding="utf-8"
    )
    (inherited_home / ".docker/config.json").write_text(
        '{"auths":{"registry.example":{"auth":"must-not-survive"}}}\n',
        encoding="utf-8",
    )
    inherited_bin = tmp_path / "inherited-bin"
    inherited_bin.mkdir()
    inherited_op_marker = tmp_path / "inherited-op-ran"
    inherited_op = inherited_bin / "op"
    _write_executable(
        inherited_op,
        f"#!/usr/bin/env bash\nprintf ran > {shlex.quote(str(inherited_op_marker))}\n",
    )
    package_probe = tmp_path / "package-probe"
    _write_executable(
        package_probe,
        """#!/usr/bin/env bash
set -euo pipefail
printf "sentinel=%s\\n" "${INHERITED_SENTINEL_SECRET-unset}"
printf "azure=%s\\n" "${AZURE_OPENAI_API_KEY-unset}"
printf "github=%s\\n" "${GITHUB_TOKEN-unset}"
printf "op=%s\\n" "$(command -v op)"
printf "home=%s\\n" "$HOME"
printf "xdg_config=%s\\n" "$XDG_CONFIG_HOME"
printf "xdg_state=%s\\n" "$XDG_STATE_HOME"
printf "direnv_config=%s\\n" "$DIRENV_CONFIG"
printf "mise_config=%s\\n" "$MISE_CONFIG_DIR"
printf "mise_cache=%s\\n" "$MISE_CACHE_DIR"
printf "mise_data=%s\\n" "$MISE_DATA_DIR"
printf "mise_installs=%s\\n" "$MISE_INSTALLS_DIR"
printf "mise_plugins=%s\\n" "$MISE_PLUGINS_DIR"
printf "mise_state=%s\\n" "$MISE_STATE_DIR"
printf "mise_system_config=%s\\n" "$MISE_SYSTEM_CONFIG_DIR"
printf "mise_global_config=%s\\n" "$MISE_GLOBAL_CONFIG_FILE"
printf "mise_ceiling=%s\\n" "$MISE_CEILING_PATHS"
printf "mise_no_hooks=%s\\n" "$MISE_NO_HOOKS"
printf "mise_system_data=%s\\n" "$MISE_SYSTEM_DATA_DIR"
printf "docker_config=%s\\n" "$DOCKER_CONFIG"
printf "git_config=%s\\n" "$GIT_CONFIG_GLOBAL"
printf "npm_config=%s\\n" "$NPM_CONFIG_USERCONFIG"
[[ ! -f "$HOME/.npmrc" ]] || cat "$HOME/.npmrc"
[[ ! -f "$DOCKER_CONFIG/config.json" ]] || cat "$DOCKER_CONFIG/config.json"
op vault list >/dev/null 2>&1 || true
""",
    )
    shell = (
        f"source {shlex.quote(str(ROOT / '.cursor/credential-neutral.sh'))}\n"
        f"cursor_exec_credential_neutral {shlex.quote(str(package_probe))}"
    )
    environment = os.environ | {
        "HOME": str(inherited_home),
        "PATH": f"{inherited_bin}:{os.environ['PATH']}",
        "INHERITED_SENTINEL_SECRET": "must-not-survive",
        "AZURE_OPENAI_API_KEY": "must-not-survive",
        "GITHUB_TOKEN": "must-not-survive",
        "OP_SERVICE_ACCOUNT_TOKEN": "must-not-survive",
    }
    result = _run(["bash", "-c", shell], environment=environment)
    assert result.returncode == 0
    assert "must-not-survive" not in result.stdout + result.stderr
    assert all(
        value in result.stdout
        for value in ("sentinel=unset", "azure=unset", "github=unset")
    )
    assert "/.cursor/credential-neutral-bin/op" in result.stdout
    runtime_home = ROOT / ".cursor/runtime-home"
    expected = {
        "home": runtime_home,
        "xdg_config": runtime_home / ".config",
        "xdg_state": runtime_home / ".local/state",
        "direnv_config": runtime_home / "direnv-config",
        "mise_config": runtime_home / "mise-config",
        "mise_cache": runtime_home / ".cache/mise",
        "mise_data": runtime_home / ".local/share/mise",
        "mise_installs": runtime_home / ".local/share/mise/installs",
        "mise_plugins": runtime_home / ".local/share/mise/plugins",
        "mise_state": runtime_home / ".local/state/mise",
        "mise_system_config": runtime_home / "mise-system-config",
        "mise_global_config": runtime_home / "mise-config/global-config.toml",
        "mise_ceiling": ROOT.parent,
        "mise_no_hooks": "1",
        "mise_system_data": runtime_home / "mise-system-data",
        "docker_config": runtime_home / ".docker",
        "git_config": runtime_home / ".gitconfig",
        "npm_config": runtime_home / ".npmrc",
    }
    assert all(f"{name}={path}" in result.stdout for name, path in expected.items())
    assert str(inherited_home) not in result.stdout
    assert not inherited_op_marker.exists()


@pytest.mark.parametrize(
    "case",
    """d:. d:.cache d:.cache/ms-playwright d:.cache/uv d:.config d:.docker
    d:.gnupg d:.local d:.local/bin d:.local/share d:.local/share/mise
    d:.local/share/mise/installs d:.local/share/mise/plugins d:.local/share/mise/shims
    d:.local/state d:.local/state/localias d:.local/state/mise d:.pki
    d:.pki/nssdb d:.azure d:.curl d:mise-config d:mise-system-data
    d:mise-system-config d:tmp f:.gitconfig f:.npmrc f:pip.conf
    f:.docker/config.json f:.curl/.curlrc f:.pki/nssdb/cert9.db
    f:.pki/nssdb/key4.db f:.pki/nssdb/pkcs11.txt""".split(),
)
def test_credential_neutral_dispatcher_rejects_runtime_state_symlink(
    tmp_path: Path, case: str
) -> None:
    kind, relative_path = case.split(":", 1)
    target_is_directory = kind == "d"
    expected_error = (
        f"real, user-owned {'directory' if target_is_directory else 'regular file'}"
    )
    sandbox_cursor = _sandbox_dispatcher(tmp_path)
    runtime_home = sandbox_cursor / "runtime-home"
    escaped_path = tmp_path / "credential-bearing-state"
    if target_is_directory:
        escaped_path.mkdir()
    else:
        escaped_path.write_text("must-not-survive\n", encoding="utf-8")
    symlink_path = (
        runtime_home if relative_path == "." else runtime_home / relative_path
    )
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    symlink_path.symlink_to(escaped_path, target_is_directory=target_is_directory)
    result = _run_sandbox_dispatcher(sandbox_cursor)
    assert result.returncode != 0
    assert expected_error in result.stderr


def test_credential_neutral_dispatcher_checks_runtime_state_ownership() -> None:
    dispatcher = (ROOT / ".cursor/credential-neutral.sh").read_text(encoding="utf-8")
    assert dispatcher.count('! -O "${cursor_state_path}"') == 2
    assert '! -O "${cursor_state_file}"' in dispatcher


@pytest.mark.parametrize(
    "case",
    """D:.direnvrc D:.config/direnv/direnvrc D:.config/direnv/lib/hostile.sh
    E:direnv-config/direnvrc E:direnv-config/lib/hostile.sh
    M:.config/mise/config.toml M:.config/mise/conf.d/hostile.toml
    E:mise-config/config.toml E:mise-config/conf.d/hostile.toml
    E:mise-system-config/config.toml E:mise-system-config/conf.d/hostile.toml
    E:mise-system-data/installs/hostile""".split(),
)
def test_credential_neutral_dispatcher_rejects_persisted_tool_hook(
    tmp_path: Path, case: str
) -> None:
    label, relative_path = case.split(":", 1)
    expected_error = {"D": "Direnv", "M": "Mise", "E": "must remain empty"}[label]
    sandbox_cursor = _sandbox_dispatcher(tmp_path)
    hook_marker = tmp_path / "hook-executed"
    hook_path = sandbox_cursor / "runtime-home" / relative_path
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(
        f"printf executed > {shlex.quote(str(hook_marker))}\n", encoding="utf-8"
    )
    result = _run_sandbox_dispatcher(sandbox_cursor)
    assert result.returncode != 0
    assert expected_error in result.stderr
    assert not hook_marker.exists()


@pytest.mark.parametrize(
    "case",
    """D:f:.direnvrc D:d:.config/direnv E:d:direnv-config/lib
    M:d:.config/mise E:f:mise-config/config.toml E:d:mise-config/conf.d
    E:f:mise-system-config/config.toml E:d:mise-system-config/conf.d
    E:d:mise-system-data/installs""".split(),
)
def test_credential_neutral_dispatcher_rejects_persisted_tool_hook_symlink(
    tmp_path: Path, case: str
) -> None:
    label, kind, relative_path = case.split(":", 2)
    target_is_directory = kind == "d"
    expected_error = {"D": "Direnv", "M": "Mise", "E": "must remain empty"}[label]
    sandbox_cursor = _sandbox_dispatcher(tmp_path)
    escaped_hook = tmp_path / "escaped-hook"
    if target_is_directory:
        escaped_hook.mkdir()
    else:
        escaped_hook.write_text("printf should-not-run\n", encoding="utf-8")
    hook_path = sandbox_cursor / "runtime-home" / relative_path
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.symlink_to(escaped_hook, target_is_directory=target_is_directory)
    result = _run_sandbox_dispatcher(sandbox_cursor)
    assert result.returncode != 0
    assert expected_error in result.stderr


def test_credential_neutral_direnv_ignores_inherited_global_hooks(
    tmp_path: Path,
) -> None:
    direnv = shutil.which("direnv")
    if direnv is None:
        pytest.skip("Direnv is required for the startup-hook integration test")
    assert direnv is not None
    sandbox_cursor = _sandbox_dispatcher(tmp_path)
    inherited_home = tmp_path / "inherited-home"
    inherited_config = tmp_path / "inherited-config"
    inherited_lib = inherited_config / "direnv/lib"
    inherited_lib.mkdir(parents=True)
    inherited_home.mkdir()
    markers = [tmp_path / f"hostile-hook-{index}" for index in range(3)]
    hook_paths = [
        inherited_home / ".direnvrc",
        inherited_config / "direnv/direnvrc",
        inherited_lib / "hostile.sh",
    ]
    for hook_path, marker in zip(hook_paths, markers, strict=True):
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(
            f"printf executed > {shlex.quote(str(marker))}\n", encoding="utf-8"
        )
    project = tmp_path / "project"
    project.mkdir()
    (project / ".envrc").write_text(
        "export CURSOR_REPO_ENVRC_LOADED=1\n", encoding="utf-8"
    )
    direnv_probe = (
        'printf "repo=%s\\n" "$CURSOR_REPO_ENVRC_LOADED"\n'
        'printf "direnv_config=%s\\n" "$DIRENV_CONFIG"'
    )
    runner = tmp_path / "run-direnv"
    _write_executable(
        runner,
        '#!/usr/bin/env bash\nset -euo pipefail\ncd "$2"\n"$1" allow .\n'
        '"$1" exec . /bin/bash --noprofile --norc -c '
        f"{shlex.quote(direnv_probe)}\n",
    )
    inherited_environment = os.environ | {
        "HOME": str(inherited_home),
        "XDG_CONFIG_HOME": str(inherited_config),
    }
    result = _run_sandbox_dispatcher(
        sandbox_cursor,
        "/bin/bash",
        "--noprofile",
        "--norc",
        "--",
        str(runner),
        direnv,
        str(project),
        environment=inherited_environment,
    )
    assert result.returncode == 0, result.stderr
    assert "repo=1" in result.stdout
    assert (
        f"direnv_config={sandbox_cursor / 'runtime-home/direnv-config'}"
        in result.stdout
    )
    assert not any(marker.exists() for marker in markers)


@pytest.mark.parametrize(
    ("entry_name", "as_symlink", "expected_error"),
    [
        ("mise", True, "real, user-owned executable"),
        ("mise", False, "reviewed digest"),
        ("uv", False, "Unexpected executable entry"),
    ],
)
def test_credential_neutral_dispatcher_rejects_untrusted_local_executable(
    tmp_path: Path, entry_name: str, as_symlink: bool, expected_error: str
) -> None:
    sandbox_cursor = _sandbox_dispatcher(tmp_path)
    local_bin = sandbox_cursor / "runtime-home/.local/bin"
    local_bin.mkdir(parents=True)
    executable_path = local_bin / entry_name
    if as_symlink:
        escaped_executable = tmp_path / "escaped-executable"
        escaped_executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        escaped_executable.chmod(0o755)
        executable_path.symlink_to(escaped_executable)
    else:
        executable_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable_path.chmod(0o755)
    result = _run_sandbox_dispatcher(sandbox_cursor)
    assert result.returncode != 0
    assert expected_error in result.stderr


def test_mise_config_and_ancestor_discovery_are_constrained_with_real_mise(
    tmp_path: Path,
) -> None:
    mise = shutil.which("mise")
    if mise is None:
        pytest.skip("Mise is required for the config-discovery integration test")
    assert mise is not None

    ancestor = tmp_path / "ancestor"
    project = ancestor / "project"
    project.mkdir(parents=True)
    (project / ".tool-versions").write_text("", encoding="utf-8")
    inherited_home = tmp_path / "inherited-home"
    inherited_global = tmp_path / "inherited-config/mise"
    dedicated_config = tmp_path / "dedicated-mise-config"
    dedicated_system_config = tmp_path / "dedicated-mise-system-config"
    dedicated_system_data = tmp_path / "dedicated-mise-system-data"
    for directory in (
        inherited_home,
        inherited_global,
        dedicated_config,
        dedicated_system_config,
        dedicated_system_data,
    ):
        directory.mkdir(parents=True)
    ancestor_marker = tmp_path / "ancestor-hook-executed"
    global_marker = tmp_path / "global-hook-executed"
    project_marker = tmp_path / "project-hook-executed"
    ancestor_hook = f"printf executed > {shlex.quote(str(ancestor_marker))}"
    global_hook = f"printf executed > {shlex.quote(str(global_marker))}"
    (ancestor / ".mise.toml").write_text(
        '[env]\nANCESTOR_MISE_SENTINEL = "loaded"\n'
        f"[hooks]\nenter = {json.dumps(ancestor_hook)}\n",
        encoding="utf-8",
    )
    (inherited_global / "config.toml").write_text(
        '[env]\nGLOBAL_MISE_SENTINEL = "loaded"\n'
        f"[hooks]\nenter = {json.dumps(global_hook)}\n",
        encoding="utf-8",
    )
    (project / ".mise.toml").write_text(
        '[env]\nPROJECT_MISE_SENTINEL = "loaded"\n'
        f"[hooks]\nenter = {json.dumps(f'printf executed > {project_marker}')}\n",
        encoding="utf-8",
    )
    probe = "\n".join(
        (
            'printf "ancestor=%s\\n" "${ANCESTOR_MISE_SENTINEL-unset}"',
            'printf "global=%s\\n" "${GLOBAL_MISE_SENTINEL-unset}"',
            'printf "project=%s\\n" "${PROJECT_MISE_SENTINEL-unset}"',
        )
    )
    result = _run(
        [mise, "exec", "--", "/bin/bash", "--noprofile", "--norc", "-c", probe],
        cwd=project,
        environment={
            "HOME": str(inherited_home),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "MISE_CACHE_DIR": str(tmp_path / "mise-cache"),
            "MISE_CONFIG_DIR": str(dedicated_config),
            "MISE_SYSTEM_CONFIG_DIR": str(dedicated_system_config),
            "MISE_GLOBAL_CONFIG_FILE": str(dedicated_config / "global.toml"),
            "MISE_CEILING_PATHS": str(ancestor),
            "MISE_NO_HOOKS": "1",
            "MISE_SYSTEM_DATA_DIR": str(dedicated_system_data),
            "MISE_TRUSTED_CONFIG_PATHS": str(project),
            "MISE_DATA_DIR": str(tmp_path / "mise-data"),
            "MISE_STATE_DIR": str(tmp_path / "mise-state"),
            "XDG_CONFIG_HOME": str(tmp_path / "inherited-config"),
        },
    )
    assert result.returncode == 0, result.stderr
    assert all(
        value in result.stdout
        for value in ("ancestor=unset", "global=unset", "project=loaded")
    )
    assert not any(
        path.exists() for path in (ancestor_marker, global_marker, project_marker)
    )


@pytest.mark.parametrize(
    ("ci", "expected_api_base"),
    [("", "https://api.cosma.localhost"), ("true", "http://localhost:8205")],
)
def test_credential_neutral_envrc_skips_local_overrides_and_op(
    tmp_path: Path, ci: str, expected_api_base: str
) -> None:
    sandbox = tmp_path / "repo"
    (sandbox / ".cursor").mkdir(parents=True)
    (sandbox / "web").mkdir()
    source_paths = (
        ".envrc .env .env.shared .env.dev .env.test .cursor/credential-neutral.env"
    )
    for relative_path in source_paths.split():
        destination = sandbox / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative_path, destination)
    for (
        local_override
    ) in ".env.local .env.shared.local .env.dev.local .env.test.local".split():
        (sandbox / local_override).write_text(
            "export LOCAL_OVERRIDE_SENTINEL=loaded\n", encoding="utf-8"
        )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    op_marker = tmp_path / "op-ran"
    fake_op = fake_bin / "op"
    _write_executable(
        fake_op,
        f"#!/usr/bin/env bash\nprintf ran > {shlex.quote(str(op_marker))}\n",
    )
    _write_executable(
        fake_bin / "just",
        "#!/bin/bash\n[[ ! -e web/node_modules ]]\ncommand -v op\n",
    )
    venv_bin = sandbox / ".venv/bin"
    venv_bin.mkdir(parents=True)
    venv_op_marker = tmp_path / "venv-op-ran"
    venv_op = venv_bin / "op"
    _write_executable(
        venv_op,
        f"#!/usr/bin/env bash\nprintf ran > {shlex.quote(str(venv_op_marker))}\n",
    )
    (sandbox / ".cursor/credential-neutral-bin").mkdir()
    shutil.copyfile(
        ROOT / ".cursor/credential-neutral-bin/op",
        sandbox / ".cursor/credential-neutral-bin/op",
    )
    (sandbox / ".cursor/credential-neutral-bin/op").chmod(0o755)

    shell = """strict_env() { set -u; }
PATH_add() { PATH="$1:$PATH"; }
layout() { layout_uv; }
source_env() { source "$1"; }
source_env_if_exists() { [[ ! -f "$1" ]] || source "$1"; }
env_vars_required() { :; }
source .envrc
printf "override=%s\\n" "${LOCAL_OVERRIDE_SENTINEL-unset}"
printf "api=%s\\n" "$API_BASE_URL"
printf "clerk=%s\\n" "$VITE_CLERK_PUBLIC_KEY"
printf "op_account=%s\\n" "${OP_ACCOUNT-unset}"
printf "op_path=%s\\n" "$(command -v op)"
op vault list >/dev/null 2>&1 || true
"""
    env = {
        "PATH": f"{fake_bin}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CI": ci,
        "COSMANEURA_CURSOR_CREDENTIAL_NEUTRAL": "1",
        "CURSOR_CREDENTIAL_SCRIPT_DIR": str(sandbox / ".cursor"),
        "CURSOR_JUST_BIN": str(fake_bin / "just"),
        "CURSOR_NODE_BIN": str(fake_bin / "node"),
        "CURSOR_PNPM_BIN": str(fake_bin / "just"),
    }
    result = _run(
        ["bash", "-c", shell],
        cwd=sandbox,
        environment=env,
    )
    assert result.returncode == 0, result.stderr
    assert "override=unset" in result.stdout
    assert f"api={expected_api_base}" in result.stdout
    assert "clerk=pk_test_placeholder" in result.stdout
    assert "op_account=unset" in result.stdout
    assert not op_marker.exists() and not venv_op_marker.exists()
    expected_op = str(sandbox / ".cursor/credential-neutral-bin/op")
    install = (ROOT / ".cursor/install.sh").read_text(encoding="utf-8")
    marker = "/usr/bin/env PATH="
    command = install[install.index(marker) :].split("\n\n", 1)[0]
    command = command.replace("\\\n", " ")
    result = _run(["bash", "-c", command], cwd=sandbox, environment=env)
    assert result.returncode == 0 and result.stdout.splitlines() == [expected_op] * 2
    frontend = (ROOT / ".cursor/run-frontend.sh").read_text(encoding="utf-8")
    command = frontend[frontend.rindex("/usr/bin/env") :].replace("\\\n", " ")
    result = _run(["bash", "-c", command], cwd=sandbox, environment=env)
    assert result.returncode == 0 and result.stdout.strip() == expected_op
    assert 'dirname "$CURSOR_JUST_BIN"' in frontend
    assert frontend.index("$CURSOR_PNPM_BIN") < frontend.index("$CURSOR_NODE_BIN")


@pytest.mark.parametrize(
    ("ci", "expected_api_base"),
    [("", "https://api.cosma.localhost"), ("true", "http://localhost:8205")],
)
def test_envrc_gives_the_api_its_base_url_outside_the_cursor_path(
    tmp_path: Path, ci: str, expected_api_base: str
) -> None:
    sandbox = tmp_path / "repo"
    (sandbox / "web").mkdir(parents=True)
    for relative_path in ".envrc .env .env.shared .env.dev .env.test".split():
        shutil.copyfile(ROOT / relative_path, sandbox / relative_path)

    shell = """strict_env() { set -u; }
PATH_add() { PATH="$1:$PATH"; }
layout() { :; }
source_env() { source "$1"; }
source_env_if_exists() { [[ ! -f "$1" ]] || source "$1"; }
env_vars_required() { :; }
source .envrc
printf "api=%s\\n" "$API_BASE_URL"
"""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CI": ci,
        "API_BASE_URL": "https://stale.example",
    }
    result = _run(["bash", "-c", shell], cwd=sandbox, environment=env)
    assert result.returncode == 0, result.stderr
    assert f"api={expected_api_base}" in result.stdout


@pytest.mark.parametrize("mode", ("dev", "test"))
@pytest.mark.parametrize("host", ("localhost", "127.0.0.1", "[::1]"))
def test_database_guard_accepts_only_explicit_exact_urls(mode: str, host: str) -> None:
    database = "development" if mode == "dev" else "test"
    target = f"postgresql://root:password@{host}:5432/{database}"
    environment = _database_environment(mode=mode)
    environment["DATABASE_URL" if mode == "dev" else "TEST_DATABASE_URL"] = target
    assert (
        GUARD.validate_database_environment(mode=mode, environment=environment)
        == target
    )


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://root:password@staging-db.example:5432/development",
        "postgresql://root:password@localhost:5432/production",
        "postgresql://admin:secret@localhost:5432/development",
        "postgresql://root:password@localhost:6432/development",
        "POSTGRESQL://root:password@localhost:5432/development",
        "postgresql://root:password@localhost:05432/development",
        "postgresql://root:password@localhost:5432/development?",
        "postgresql://root:password@localhost:5432/development#",
        "postgresql://root:password@local\thost:5432/development",
        "postgresql://root:password@localhost:5432/development\n",
        "postgresql://root:password@localhost:5432/development\x1f",
        " postgresql://root:password@localhost:5432/development",
        "",
    ],
)
def test_database_guard_rejects_all_noncanonical_forms(value: str) -> None:
    environment = _database_environment(mode="dev", database_url=value)
    with pytest.raises(GUARD.LocalDatabaseSafetyError):
        GUARD.validate_database_environment(mode="dev", environment=environment)


def test_test_mode_rejects_divergent_post_direnv_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    environment = _database_environment(
        mode="test",
        test_database_url="postgresql://root:password@remote.example:5432/test",
    )
    executed: list[tuple[str, list[str], dict[str, str]]] = []
    monkeypatch.setattr(GUARD.os, "environ", environment)
    monkeypatch.setattr(
        GUARD.os,
        "execvpe",
        lambda file, args, env: executed.append((file, args, env)),
    )
    assert GUARD.main(["--mode", "test", "--", "uv", "run", "alembic"]) == 2
    assert "Refusing automatic database command" in capsys.readouterr().err
    assert executed == []


def test_database_guard_passes_exact_validated_snapshot_to_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _database_environment(mode="test")
    executed: list[tuple[str, list[str], dict[str, str]]] = []
    monkeypatch.setattr(GUARD.os, "environ", environment)
    monkeypatch.setattr(
        GUARD.os,
        "execvpe",
        lambda file, args, env: executed.append((file, args, env)),
    )
    assert GUARD.main(["--mode", "test", "--", "uv", "run", "alembic"]) == 0
    assert len(executed) == 1
    file, args, child_environment = executed[0]
    assert file == "uv"
    assert args == ["uv", "run", "alembic"]
    assert child_environment is not environment
    assert child_environment["DATABASE_URL"] == DEV_LOCALHOST
    assert child_environment["TEST_DATABASE_URL"] == TEST_LOCALHOST


def test_database_guard_never_prints_a_rejected_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rejected = "postgresql://administrator:sensitive@remote.example:5432/production"
    monkeypatch.setattr(
        GUARD.os,
        "environ",
        _database_environment(mode="dev", database_url=rejected),
    )

    assert GUARD.main(["--mode", "dev"]) == 2
    captured = capsys.readouterr()
    assert rejected not in captured.out
    assert rejected not in captured.err


def test_test_boundary_rejects_divergent_environment_after_direnv(
    tmp_path: Path,
) -> None:
    remote_dev = "postgresql://root:password@remote.example:5432/development"
    remote_test = "postgresql://root:password@remote.example:5432/test"
    returncode, output = _run_test_boundary(
        tmp_path,
        database_url=remote_dev,
        test_database_url=remote_test,
    )
    assert returncode == 2
    assert remote_dev not in output
    assert remote_test not in output
    assert "Refusing automatic database command" in output


def test_test_boundary_passes_exact_post_direnv_url_to_child(tmp_path: Path) -> None:
    returncode, output = _run_test_boundary(
        tmp_path,
        test_database_url=TEST_LOCALHOST,
    )
    assert returncode == 0
    assert output == TEST_LOCALHOST


def test_cursor_scripts_use_explicit_guarded_dev_and_test_commands() -> None:
    helper = DATABASE_HELPER.read_text(encoding="utf-8")
    assert (
        'CI="" "${CURSOR_MISE_BIN:?}" exec --' in helper
        and 'UV_PYTHON="${CURSOR_PYTHON_BIN:?}"' in helper
        and '"${CURSOR_DATABASE_GUARD}" --mode dev --' in helper
    )
    assert (
        'CI=true "${CURSOR_MISE_BIN:?}" exec --' in helper
        and '"${CURSOR_DATABASE_GUARD}" --mode test --' in helper
    )

    install = (ROOT / ".cursor/install.sh").read_text(encoding="utf-8")
    assert 'source "$SCRIPT_DIR/database.sh"' in install
    required = (
        'cursor_database_dev "$CURSOR_UV_BIN" run --no-sync alembic upgrade head',
        'cursor_database_test "$CURSOR_UV_BIN" run --no-sync alembic upgrade head',
        'cursor_database_dev "$CURSOR_UV_BIN" run --no-sync python migrations/seed.py',
        'cursor_database_test "$CURSOR_UV_BIN" run --no-sync python migrations/seed.py',
    )
    assert all(command in install for command in required)
    assert all(
        value in install
        for value in ('psql "$DATABASE_URL"', 'psql "$TEST_DATABASE_URL"')
    )
    assert all(
        value not in install
        for value in ("just db_migrate", "just db_seed", "EXECUTE_IN_TEST")
    )

    start = (ROOT / ".cursor/start.sh").read_text(encoding="utf-8")
    assert 'source "$SCRIPT_DIR/database.sh"' in start and required[0] in start


def test_cursor_compose_path_uses_only_immutable_image_refs() -> None:
    base_path = ROOT / "docker-compose.yml"
    override_path = ROOT / ".cursor/docker-compose.yml"
    override = override_path.read_text(encoding="utf-8")
    images = (
        "postgres:16.9@sha256:980e5d98958b0918ff1bbb63d5f3e883debe74130ea137d11ac1f8e40a84d6dc",
        "redis:6.2.18@sha256:f3f3a0e0fc82db539bf84aed94f03d8cab9e2b57e2348512f8a1b0aae1d24444",
        "axllent/mailpit:v1.31.0@sha256:29154cb86d35bcff4c0f82f185fc6720e89248b2cd2bc43f312a2b31fddd34ad",
    )
    assert all(image in override for image in images)
    assert ":latest" not in override

    base_config = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    override_config = yaml.safe_load(override)
    base_services = base_config["services"]
    override_services = override_config["services"]
    assert set(base_services) <= set(override_services)
    merged_services = {
        name: base_services.get(name, {}) | service
        for name, service in override_services.items()
    }
    for service_name, service in merged_services.items():
        image = service.get("image")
        assert isinstance(image, str), f"{service_name} lacks a pinned image"
        assert re.fullmatch(r"[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}", image), (
            f"{service_name} image is not tag-and-digest pinned: {image}"
        )
        assert "build" not in service, f"{service_name} can execute an image build"

    services = (ROOT / ".cursor/services.sh").read_text(encoding="utf-8")
    assert '--file "$REPO_ROOT/docker-compose.yml"' in services
    assert '--file "$REPO_ROOT/.cursor/docker-compose.yml"' in services
    assert services.count("denv /usr/bin/docker compose") == 1


def test_cursor_backend_runs_python_via_uv_without_sync(tmp_path: Path) -> None:
    install = (ROOT / ".cursor/install.sh").read_text(encoding="utf-8")
    start = (ROOT / ".cursor/start.sh").read_text(encoding="utf-8")
    backend = (ROOT / ".cursor/run-backend.sh").read_text(encoding="utf-8")

    assert install.count('"$CURSOR_UV_BIN" sync --locked') == 1
    for script in (install, start, backend):
        assert all(
            "--no-sync" in line
            for line in script.splitlines()
            if '"$CURSOR_UV_BIN" run' in line
        )
    assert all(
        value not in install
        for value in ("js_generate-openapi", "pnpm dlx", "pnpm run openapi")
    )
    assert "use committed OpenAPI client" in install
    assert all(value not in install for value in (".env.dev.local", ".env.test.local"))

    uv = tmp_path / "uv"
    _write_executable(uv, '#!/bin/bash\nprintf "uv=%s\\n" "$*"\n')
    environment = os.environ | {
        "CURSOR_UV_BIN": str(uv),
        "CURSOR_PYTHON_BIN": "/bin/false",
    }

    command = backend[backend.rindex("/usr/bin/env") :].replace("\\\n", " ")
    result = _run(["bash", "-c", command], cwd=tmp_path, environment=environment)
    assert (
        result.returncode == 0
        and result.stdout.strip() == "uv=run --no-sync python main.py"
    )
