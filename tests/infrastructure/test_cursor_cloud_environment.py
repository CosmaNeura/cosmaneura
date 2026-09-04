from __future__ import annotations

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
    fake_direnv.write_text(
        "\n".join(
            (
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                '[[ "$1" == "exec" && "$2" == "." ]]',
                "shift 2",
                'export PYTHON_ENV="test"',
                f'export DATABASE_URL="{database_url}"',
                f'export TEST_DATABASE_URL="{test_database_url}"',
                'exec "$@"',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    fake_direnv.chmod(0o755)
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
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    marker_value = marker.read_text(encoding="utf-8") if marker.exists() else ""
    return result.returncode, marker_value + result.stderr


def test_mise_install_is_version_and_digest_pinned() -> None:
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


def test_docker_apt_key_requires_exact_sole_primary_fingerprint() -> None:
    installer = (ROOT / "scripts/install_verified_docker_apt_key.sh").read_text(
        encoding="utf-8"
    )
    assert (
        'DOCKER_APT_KEY_PRIMARY_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"'
        in installer
    )
    assert (
        'DOCKER_APT_KEY_URL="https://download.docker.com/linux/ubuntu/gpg"' in installer
    )
    assert 'fingerprints="$(docker_primary_fingerprints "${key_path}")"' in installer
    assert (
        '[[ "${fingerprints}" != "${DOCKER_APT_KEY_PRIMARY_FINGERPRINT}" ]]'
        in installer
    )
    assert (
        'require_exact_docker_primary_fingerprint "${docker_key_download}"' in installer
    )
    assert (
        'require_exact_docker_primary_fingerprint "${docker_key_dearmored}"'
        in installer
    )
    assert (
        'sudo mv -f -- "${docker_key_target_tmp}" "${DOCKER_APT_KEY_PATH}"' in installer
    )
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
        "\n".join(
            (
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'printf "mode=%s\\n" "$1"',
                'printf "bash_env=%s\\n" "${BASH_ENV-unset}"',
                'printf "sentinel=%s\\n" "${INHERITED_SENTINEL_SECRET-unset}"',
                'printf "shellopts=%s\\n" "$SHELLOPTS"',
                'printf "ps4=%s\\n" "$PS4"',
                "if declare -F cursor_hostile_function >/dev/null; then",
                "  cursor_hostile_function",
                "  printf 'function=present\\n'",
                "else",
                "  printf 'function=absent\\n'",
                "fi",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    attack_marker = tmp_path / "bash-env-executed"
    hostile_bash_env = tmp_path / "hostile-bash-env.sh"
    hostile_bash_env.write_text(
        "\n".join(
            (
                f"printf executed > {shlex.quote(str(attack_marker))}",
                "export HOSTILE_BASH_ENV_RAN=1",
            )
        )
        + "\n",
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
        **{
            terminal["name"]: terminal["command"]
            for terminal in environment["terminals"]
        },
    }
    for mode, command in commands.items():
        command_parts = shlex.split(command)
        assert command_parts[:6] == [
            "/usr/bin/env",
            "-i",
            "/bin/bash",
            "--noprofile",
            "--norc",
            "--",
        ]
        result = subprocess.run(
            command_parts,
            cwd=tmp_path,
            env=inherited_environment,
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert f"mode={mode}" in result.stdout
        assert "bash_env=unset" in result.stdout
        assert "sentinel=unset" in result.stdout
        assert "function=absent" in result.stdout
        assert ":xtrace:" not in result.stdout
        assert "hostile-ps4-must-not-survive" not in result.stdout
        assert "must-not-survive" not in result.stdout
        assert not attack_marker.exists()


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
    result = subprocess.run(
        ["bash", str(ROOT / ".cursor" / entrypoint)],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
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
    inherited_op.write_text(
        f"#!/usr/bin/env bash\nprintf ran > {shlex.quote(str(inherited_op_marker))}\n",
        encoding="utf-8",
    )
    inherited_op.chmod(0o755)

    package_probe = tmp_path / "package-probe"
    package_probe.write_text(
        "\n".join(
            (
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'printf "sentinel=%s\\n" "${INHERITED_SENTINEL_SECRET-unset}"',
                'printf "azure=%s\\n" "${AZURE_OPENAI_API_KEY-unset}"',
                'printf "github=%s\\n" "${GITHUB_TOKEN-unset}"',
                'printf "op=%s\\n" "$(command -v op)"',
                'printf "home=%s\\n" "$HOME"',
                'printf "xdg_config=%s\\n" "$XDG_CONFIG_HOME"',
                'printf "xdg_state=%s\\n" "$XDG_STATE_HOME"',
                'printf "docker_config=%s\\n" "$DOCKER_CONFIG"',
                'printf "git_config=%s\\n" "$GIT_CONFIG_GLOBAL"',
                'printf "npm_config=%s\\n" "$NPM_CONFIG_USERCONFIG"',
                'if [[ -f "$HOME/.npmrc" ]]; then cat "$HOME/.npmrc"; fi',
                'if [[ -f "$DOCKER_CONFIG/config.json" ]]; then '
                'cat "$DOCKER_CONFIG/config.json"; fi',
                "op vault list >/dev/null 2>&1 || true",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    package_probe.chmod(0o755)

    dispatcher = ROOT / ".cursor/credential-neutral.sh"
    shell = "\n".join(
        (
            f"source {shlex.quote(str(dispatcher))}",
            f"cursor_exec_credential_neutral {shlex.quote(str(package_probe))}",
        )
    )
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(inherited_home),
            "PATH": f"{inherited_bin}:{environment['PATH']}",
            "INHERITED_SENTINEL_SECRET": "must-not-survive",
            "AZURE_OPENAI_API_KEY": "must-not-survive",
            "GITHUB_TOKEN": "must-not-survive",
            "OP_SERVICE_ACCOUNT_TOKEN": "must-not-survive",
        }
    )
    result = subprocess.run(
        ["bash", "-c", shell],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "must-not-survive" not in result.stdout
    assert "must-not-survive" not in result.stderr
    assert "sentinel=unset" in result.stdout
    assert "azure=unset" in result.stdout
    assert "github=unset" in result.stdout
    assert "/.cursor/credential-neutral-bin/op" in result.stdout
    runtime_home = ROOT / ".cursor/runtime-home"
    assert f"home={runtime_home}" in result.stdout
    assert f"xdg_config={runtime_home / '.config'}" in result.stdout
    assert f"xdg_state={runtime_home / '.local/state'}" in result.stdout
    assert f"docker_config={runtime_home / '.docker'}" in result.stdout
    assert f"git_config={runtime_home / '.gitconfig'}" in result.stdout
    assert f"npm_config={runtime_home / '.npmrc'}" in result.stdout
    assert str(inherited_home) not in result.stdout
    assert not inherited_op_marker.exists()


def test_credential_neutral_dispatcher_rejects_runtime_home_symlink(
    tmp_path: Path,
) -> None:
    sandbox_cursor = tmp_path / ".cursor"
    sandbox_cursor.mkdir()
    shutil.copyfile(
        ROOT / ".cursor/credential-neutral.sh",
        sandbox_cursor / "credential-neutral.sh",
    )
    credential_home = tmp_path / "credential-bearing-home"
    credential_home.mkdir()
    (credential_home / ".npmrc").write_text(
        "//registry.npmjs.org/:_authToken=must-not-survive\n", encoding="utf-8"
    )
    (sandbox_cursor / "runtime-home").symlink_to(
        credential_home, target_is_directory=True
    )

    shell = "\n".join(
        (
            f"source {shlex.quote(str(sandbox_cursor / 'credential-neutral.sh'))}",
            "cursor_exec_credential_neutral /bin/true",
        )
    )
    result = subprocess.run(
        ["bash", "-c", shell],
        cwd=tmp_path,
        env=dict(os.environ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "must be a real, user-owned directory" in result.stderr


@pytest.mark.parametrize(
    "relative_path",
    [
        ".cache",
        ".cache/ms-playwright",
        ".cache/uv",
        ".config",
        ".docker",
        ".gnupg",
        ".local",
        ".local/bin",
        ".local/share",
        ".local/share/mise",
        ".local/share/mise/shims",
        ".local/state",
        ".local/state/localias",
        ".pki",
        ".pki/nssdb",
        ".azure",
        ".curl",
        "tmp",
    ],
)
def test_credential_neutral_dispatcher_rejects_nested_directory_symlink(
    tmp_path: Path, relative_path: str
) -> None:
    sandbox_cursor = tmp_path / ".cursor"
    sandbox_cursor.mkdir()
    shutil.copyfile(
        ROOT / ".cursor/credential-neutral.sh",
        sandbox_cursor / "credential-neutral.sh",
    )
    runtime_home = sandbox_cursor / "runtime-home"
    escaped_directory = tmp_path / "credential-bearing-directory"
    escaped_directory.mkdir()
    symlink_path = runtime_home / relative_path
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    symlink_path.symlink_to(escaped_directory, target_is_directory=True)

    shell = "\n".join(
        (
            f"source {shlex.quote(str(sandbox_cursor / 'credential-neutral.sh'))}",
            "cursor_exec_credential_neutral /bin/true",
        )
    )
    result = subprocess.run(
        ["bash", "-c", shell],
        cwd=tmp_path,
        env=dict(os.environ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "must be a real, user-owned directory" in result.stderr


@pytest.mark.parametrize(
    "relative_path",
    [
        ".gitconfig",
        ".npmrc",
        "pip.conf",
        ".docker/config.json",
        ".curl/.curlrc",
        ".pki/nssdb/cert9.db",
        ".pki/nssdb/key4.db",
        ".pki/nssdb/pkcs11.txt",
    ],
)
def test_credential_neutral_dispatcher_rejects_config_file_symlink(
    tmp_path: Path, relative_path: str
) -> None:
    sandbox_cursor = tmp_path / ".cursor"
    sandbox_cursor.mkdir()
    shutil.copyfile(
        ROOT / ".cursor/credential-neutral.sh",
        sandbox_cursor / "credential-neutral.sh",
    )
    runtime_home = sandbox_cursor / "runtime-home"
    escaped_file = tmp_path / "credential-bearing-config"
    escaped_file.write_text("must-not-survive\n", encoding="utf-8")
    symlink_path = runtime_home / relative_path
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    symlink_path.symlink_to(escaped_file)

    shell = "\n".join(
        (
            f"source {shlex.quote(str(sandbox_cursor / 'credential-neutral.sh'))}",
            "cursor_exec_credential_neutral /bin/true",
        )
    )
    result = subprocess.run(
        ["bash", "-c", shell],
        cwd=tmp_path,
        env=dict(os.environ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "must be a real, user-owned regular file" in result.stderr


def test_credential_neutral_dispatcher_checks_runtime_state_ownership() -> None:
    dispatcher = (ROOT / ".cursor/credential-neutral.sh").read_text(encoding="utf-8")
    assert dispatcher.count('! -O "${cursor_state_path}"') == 2
    assert '! -O "${cursor_state_file}"' in dispatcher


@pytest.mark.parametrize(
    ("ci", "expected_api_base"),
    [("", "https://api.cosma.localhost"), ("true", "http://localhost:8205")],
)
def test_credential_neutral_envrc_skips_local_overrides_and_op(
    tmp_path: Path, ci: str, expected_api_base: str
) -> None:
    sandbox = tmp_path / "repo"
    (sandbox / ".cursor").mkdir(parents=True)
    for relative_path in (
        ".envrc",
        ".env",
        ".env.shared",
        ".env.dev",
        ".env.test",
        ".cursor/credential-neutral.env",
    ):
        destination = sandbox / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative_path, destination)
    for local_override in (
        ".env.local",
        ".env.shared.local",
        ".env.dev.local",
        ".env.test.local",
    ):
        (sandbox / local_override).write_text(
            "export LOCAL_OVERRIDE_SENTINEL=loaded\n", encoding="utf-8"
        )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    op_marker = tmp_path / "op-ran"
    fake_op = fake_bin / "op"
    fake_op.write_text(
        f"#!/usr/bin/env bash\nprintf ran > {shlex.quote(str(op_marker))}\n",
        encoding="utf-8",
    )
    fake_op.chmod(0o755)
    venv_bin = sandbox / ".venv/bin"
    venv_bin.mkdir(parents=True)
    venv_op_marker = tmp_path / "venv-op-ran"
    venv_op = venv_bin / "op"
    venv_op.write_text(
        f"#!/usr/bin/env bash\nprintf ran > {shlex.quote(str(venv_op_marker))}\n",
        encoding="utf-8",
    )
    venv_op.chmod(0o755)
    (sandbox / ".cursor/credential-neutral-bin").mkdir()
    shutil.copyfile(
        ROOT / ".cursor/credential-neutral-bin/op",
        sandbox / ".cursor/credential-neutral-bin/op",
    )
    (sandbox / ".cursor/credential-neutral-bin/op").chmod(0o755)

    shell = "\n".join(
        (
            "strict_env() { set -u; }",
            'PATH_add() { PATH="$1:$PATH"; }',
            "layout() { layout_uv; }",
            'source_env() { source "$1"; }',
            'source_env_if_exists() { [[ ! -f "$1" ]] || source "$1"; }',
            "env_vars_required() { :; }",
            "source .envrc",
            'printf "override=%s\\n" "${LOCAL_OVERRIDE_SENTINEL-unset}"',
            'printf "api=%s\\n" "$API_BASE_URL"',
            'printf "clerk=%s\\n" "$VITE_CLERK_PUBLIC_KEY"',
            'printf "op_account=%s\\n" "${OP_ACCOUNT-unset}"',
            'printf "op_path=%s\\n" "$(command -v op)"',
            "op vault list >/dev/null 2>&1 || true",
        )
    )
    environment = {
        "PATH": f"{fake_bin}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CI": ci,
        "COSMANEURA_CURSOR_CREDENTIAL_NEUTRAL": "1",
    }
    result = subprocess.run(
        ["bash", "-c", shell],
        cwd=sandbox,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "override=unset" in result.stdout
    assert f"api={expected_api_base}" in result.stdout
    assert "clerk=pk_test_placeholder" in result.stdout
    assert "op_account=unset" in result.stdout
    assert "op_path=" in result.stdout
    assert "/.cursor/credential-neutral-bin/op" in result.stdout
    assert not op_marker.exists()
    assert not venv_op_marker.exists()


@pytest.mark.parametrize(
    ("mode", "target"),
    [
        ("dev", "postgresql://root:password@localhost:5432/development"),
        ("dev", "postgresql://root:password@127.0.0.1:5432/development"),
        ("dev", "postgresql://root:password@[::1]:5432/development"),
        ("test", "postgresql://root:password@localhost:5432/test"),
        ("test", "postgresql://root:password@127.0.0.1:5432/test"),
        ("test", "postgresql://root:password@[::1]:5432/test"),
    ],
)
def test_database_guard_accepts_only_explicit_exact_urls(
    mode: str, target: str
) -> None:
    environment = _database_environment(mode=mode)
    if mode == "dev":
        environment["DATABASE_URL"] = target
    else:
        environment["TEST_DATABASE_URL"] = target
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


def test_test_boundary_rejects_divergent_test_pointer_after_direnv(
    tmp_path: Path,
) -> None:
    returncode, output = _run_test_boundary(
        tmp_path,
        test_database_url="postgresql://root:password@remote.example:5432/test",
    )
    assert returncode == 2
    assert TEST_LOCALHOST not in output
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
        'CI="" direnv exec . python "${CURSOR_DATABASE_GUARD}" --mode dev --' in helper
    )
    assert (
        'CI=true direnv exec . python "${CURSOR_DATABASE_GUARD}" --mode test --'
        in helper
    )

    install = (ROOT / ".cursor/install.sh").read_text(encoding="utf-8")
    assert 'source "$SCRIPT_DIR/database.sh"' in install
    assert "cursor_database_dev uv run --no-sync alembic upgrade head" in install
    assert "cursor_database_test uv run --no-sync alembic upgrade head" in install
    assert "cursor_database_dev uv run --no-sync python migrations/seed.py" in install
    assert "cursor_database_test uv run --no-sync python migrations/seed.py" in install
    assert 'psql "$DATABASE_URL"' in install
    assert 'psql "$TEST_DATABASE_URL"' in install
    assert "just db_migrate" not in install
    assert "just db_seed" not in install
    assert "EXECUTE_IN_TEST" not in install

    start = (ROOT / ".cursor/start.sh").read_text(encoding="utf-8")
    assert 'source "$SCRIPT_DIR/database.sh"' in start
    assert "cursor_database_dev uv run --no-sync alembic upgrade head" in start


def test_cursor_compose_path_uses_only_immutable_image_refs() -> None:
    base_path = ROOT / "docker-compose.yml"
    override_path = ROOT / ".cursor/docker-compose.yml"
    override = override_path.read_text(encoding="utf-8")
    assert (
        "postgres:16.9@sha256:980e5d98958b0918ff1bbb63d5f3e883debe74130ea137d11ac1f8e40a84d6dc"
        in override
    )
    assert (
        "redis:6.2.18@sha256:f3f3a0e0fc82db539bf84aed94f03d8cab9e2b57e2348512f8a1b0aae1d24444"
        in override
    )
    assert (
        "axllent/mailpit:v1.31.0@sha256:29154cb86d35bcff4c0f82f185fc6720e89248b2cd2bc43f312a2b31fddd34ad"
        in override
    )
    assert ":latest" not in override

    base_config = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    override_config = yaml.safe_load(override)
    base_services = base_config["services"]
    override_services = override_config["services"]
    merged_services = {
        service_name: {
            **base_service,
            **override_services.get(service_name, {}),
        }
        for service_name, base_service in base_services.items()
    }
    merged_services.update(
        {
            service_name: override_service
            for service_name, override_service in override_services.items()
            if service_name not in merged_services
        }
    )
    assert set(base_services) <= set(override_services)
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
    assert services.count("denv docker compose") == 1


def test_cursor_python_environment_syncs_once_and_never_implicitly_resolves() -> None:
    install = (ROOT / ".cursor/install.sh").read_text(encoding="utf-8")
    start = (ROOT / ".cursor/start.sh").read_text(encoding="utf-8")

    assert install.count("uv sync --locked") == 1
    assert all("--no-sync" in line for line in install.splitlines() if "uv run" in line)
    assert all("--no-sync" in line for line in start.splitlines() if "uv run" in line)
    assert "js_generate-openapi" not in install
    assert "pnpm dlx" not in install
    assert "pnpm run openapi" not in install
    assert "use committed OpenAPI client" in install
    assert ".env.dev.local" not in install
    assert ".env.test.local" not in install
