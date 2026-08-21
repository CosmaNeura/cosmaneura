from __future__ import annotations

import base64
import importlib.util
import os
import re
import subprocess
import sys
import zlib
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).parents[2]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "infra-staging" / filename
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def smoke():
    return _load("provider_v7_smoke_test_module", "provider_v7_smoke.py")


@pytest.fixture
def public_probe():
    return _load("provider_v7_public_probe_test_module", "provider_v7_public_probe.py")


class Response:
    def __init__(
        self, status_code: int, body: dict, content_type: str = "application/json"
    ):
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": content_type}

    def json(self) -> dict:
        return self._body


def test_platform_probe_requires_exact_staging_gates(smoke, monkeypatch) -> None:
    for name, value in {
        "PYTHON_ENV": "staging",
        "AUTHORITY_KERNEL_ENABLED": "true",
        "AGENT_API_ENABLED": "true",
    }.items():
        monkeypatch.setenv(name, value)

    smoke._require_staging()
    monkeypatch.setenv("AGENT_API_ENABLED", "false")
    with pytest.raises(smoke.SmokeFailure):
        smoke._require_staging()


def test_health_check_requires_provider_only_split(smoke) -> None:
    responses = {
        "https://provider.test/healthz": Response(
            200,
            {
                "status": "ok",
                "mock_mode": False,
                "auth_mode": "disabled",
                "mount_profile": "provider_assistant",
                "platform_agent_api_enabled": True,
            },
        ),
        "https://public.test/healthz": Response(
            200,
            {
                "status": "ok",
                "mock_mode": False,
                "auth_mode": "disabled",
                "mount_profile": "public",
                "platform_agent_api_enabled": False,
            },
        ),
    }
    client = SimpleNamespace(get=lambda url: responses[url])
    smoke._assert_health(client, "https://provider.test", "https://public.test")

    responses["https://public.test/healthz"]._body["platform_agent_api_enabled"] = True
    with pytest.raises(smoke.SmokeFailure):
        smoke._assert_health(client, "https://provider.test", "https://public.test")


def test_clerk_session_is_revoked_when_provider_probe_fails(smoke, monkeypatch) -> None:
    revoked: list[str] = []

    class Sessions:
        def create(self, **_kwargs):
            return SimpleNamespace(id="session-sensitive-value")

        def create_token(self, **_kwargs):
            return SimpleNamespace(jwt="token-sensitive-value")

        def revoke(self, *, session_id: str):
            revoked.append(session_id)
            return SimpleNamespace(status="revoked")

    clerk_module = ModuleType("app.configuration.clerk")
    setattr(clerk_module, "clerk", SimpleNamespace(sessions=Sessions()))
    monkeypatch.setitem(sys.modules, "app.configuration.clerk", clerk_module)
    monkeypatch.setattr(smoke, "_require_staging", lambda: None)
    monkeypatch.setattr(
        smoke,
        "_eligible_candidate",
        lambda: ("subject-sensitive-value", "appointment_opaque"),
    )
    monkeypatch.setattr(
        smoke,
        "_assert_health",
        lambda *_args: (_ for _ in ()).throw(smoke.SmokeFailure()),
    )

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(httpx, "Client", Client)

    with pytest.raises(smoke.SmokeFailure) as caught:
        smoke.run(
            "https://platform.test", "https://provider.test", "https://public.test"
        )
    assert caught.value.__context__ is None
    assert revoked == ["session-sensitive-value"]


def test_main_emits_only_fixed_failure(smoke, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        smoke, "run", lambda *_args: (_ for _ in ()).throw(RuntimeError("secret-value"))
    )
    monkeypatch.setattr(sys, "argv", ["probe", "one", "two", "three"])

    assert smoke.main() == 1
    assert capsys.readouterr().out == "V7_PROVIDER_SMOKE FAIL\n"


def test_status_mode_is_value_free(smoke, monkeypatch, capsys) -> None:
    monkeypatch.setattr(smoke, "_require_staging", lambda: None)
    monkeypatch.setattr(smoke, "_assert_status", lambda: None)
    monkeypatch.setattr(sys, "argv", ["probe", "--status-only"])
    assert smoke.main() == 0
    assert capsys.readouterr().out == "V7_STATUS PASS\n"


def test_public_probe_uses_managed_identity_and_requires_exact_denial(
    public_probe, monkeypatch, capsys
) -> None:
    for name, value in {
        "PYTHON_ENV": "staging",
        "MOUNT_PROFILE": "public",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PLATFORM_AGENT_API_ENABLED", raising=False)
    monkeypatch.setattr(
        "azure.identity.ManagedIdentityCredential",
        lambda: SimpleNamespace(
            get_token=lambda _scope: SimpleNamespace(token="token-sensitive-value")
        ),
    )
    seen: dict = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Response(
            403,
            {"status": 403, "code": "WORKLOAD_NOT_ALLOWED", "retryable": False},
            "application/problem+json; charset=utf-8",
        )

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "probe",
            "https://platform.test",
            "api://00000000-0000-0000-0000-000000000000/.default",
        ],
    )

    assert public_probe.main() == 0
    assert seen["headers"]["Authorization"] == "Bearer token-sensitive-value"
    assert capsys.readouterr().out == "V7_PUBLIC_DENIAL PASS\n"


def test_operator_wrapper_never_accepts_a_workload_token() -> None:
    wrapper = (ROOT / "infra-staging" / "run-provider-v7-smoke.sh").read_text()
    source = (ROOT / "infra-staging" / "provider_v7_public_probe.py").read_bytes()
    payload = base64.b64encode(zlib.compress(source, level=9)).decode()
    command = (
        '/opt/venv/bin/python -c "import base64,zlib;'
        f"exec(zlib.decompress(base64.b64decode('{payload}')))\" "
        "'https://cosmaneura-staging-app.example.northcentralus.azurecontainerapps.io' "
        "'api://00000000-0000-0000-0000-000000000000/.default'"
    )
    assert "WORKLOAD_TOKEN" not in wrapper
    assert "provider_v7_public_probe.py" in wrapper
    assert "/app/infra-staging/provider_v7_smoke.py" in wrapper
    assert "/usr/bin/expect" in wrapper
    assert '--command "$command"' not in wrapper
    assert "name=='API_BASE_URL'" in wrapper
    assert '"$PLATFORM_ORIGIN"' in wrapper
    assert '"https://$PLATFORM_FQDN"' not in wrapper
    assert "cosmaneura/cosmaneura:$HEAD" in wrapper
    assert len(command) < 1000
    assert "V7_SMOKE PASS" in wrapper


def test_current_image_status_command_stays_below_exec_limit() -> None:
    wrapper = (ROOT / "infra-staging" / "run-provider-v7-status.sh").read_text()
    source = re.search(r"<<'PY' \|\| true\n(.*?)\nPY\n", wrapper, re.S)
    assert source
    payload = base64.b64encode(zlib.compress(source.group(1).encode(), 9)).decode()
    command = (
        'PYTHONPATH=/app /opt/venv/bin/python -c "import base64,zlib;'
        f"exec(zlib.decompress(base64.b64decode('{payload}')))\""
    )
    assert len(command) < 1000
    assert "interaction_ref" in source.group(1)
    assert "/usr/bin/expect" in wrapper
    assert "V7_STATUS PASS" in wrapper


def test_full_smoke_rejects_alternate_platform_origin_before_exec(tmp_path) -> None:
    wrapper = ROOT / "infra-staging" / "run-provider-v7-smoke.sh"
    fake_az = tmp_path / "az"
    az_log = tmp_path / "az.log"
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    fake_az.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$FAKE_AZ_LOG"\n'
        'case "$*" in\n'
        "  *properties.configuration.ingress.fqdn*)\n"
        '    case "$*" in\n'
        "      *cosmaneura-staging-app*) echo platform.example.invalid ;;\n"
        "      *cosmaneura-sovereign-staging-app*) echo provider.example.invalid ;;\n"
        "      *) echo public.example.invalid ;;\n"
        "    esac ;;\n"
        "  *API_BASE_URL*) echo https://attacker.example ;;\n"
        "  *PLATFORM_AGENT_API_SCOPE*) echo api://00000000-0000-0000-0000-000000000000/.default ;;\n"
        '  *properties.template.containers\\[0\\].image*) echo "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura:$FAKE_HEAD" ;;\n'
        "esac\n"
    )
    fake_az.chmod(0o755)
    result = subprocess.run(
        ["bash", str(wrapper)],
        capture_output=True,
        text=True,
        timeout=5,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "FAKE_AZ_LOG": str(az_log),
            "FAKE_HEAD": head,
        },
    )

    assert result.returncode == 1
    assert result.stdout == "V7_SMOKE FAIL\n"
    assert "containerapp exec" not in az_log.read_text()


def test_expect_transport_is_silent_and_uses_interactive_shell(tmp_path) -> None:
    fake_az = tmp_path / "az"
    argv_log = tmp_path / "argv"
    fake_az.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" > "$FAKE_AZ_ARGV"\n'
        "printf '$ '\n"
        "IFS= read -r no_echo\n"
        "[ \"$no_echo\" = 'stty -echo' ] || exit 1\n"
        "printf '$ '\n"
        "IFS= read -r command\n"
        'printf "\\n%s\\n$ " "$FAKE_MARKER"\n'
        "IFS= read -r echo_on\n"
        "[ \"$echo_on\" = 'stty echo' ] || exit 1\n"
        "printf '$ '\n"
        "IFS= read -r final\n"
        '[ "$final" = exit ]\n'
    )
    fake_az.chmod(0o755)
    marker = "V7_SYNTHETIC PASS"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_AZ_ARGV": str(argv_log),
        "FAKE_MARKER": marker,
    }
    helper = ROOT / "infra-staging" / "containerapp-exec.expect"
    assert helper.is_file()
    if not Path("/usr/bin/expect").is_file():
        pytest.skip("operator PTY transport requires /usr/bin/expect")
    result = subprocess.run(
        [str(helper), "staging-rg", "staging-app", "printf ignored", marker],
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert argv_log.read_text().strip() == (
        "containerapp exec --resource-group staging-rg --name staging-app "
        "--command /bin/sh"
    )
