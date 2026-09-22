from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
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


@pytest.fixture
def acceptance():
    return _load("provider_v7_acceptance", "provider_v7_acceptance.py")


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "reordered",
        "missing",
        "unknown",
        "duplicate",
        "pending_audit",
        "pending_usage",
        "complete",
        "count",
        "pending_count",
        "failed_count",
    ],
)
def test_acceptance_requires_only_exact_closed_failures(
    acceptance, monkeypatch, mutation
) -> None:
    rows = [
        SimpleNamespace(
            interaction_ref=ref, audit_status="failed", usage_status="failed"
        )
        for ref in ("synthetic-one", "synthetic-two")
    ]
    monkeypatch.setattr(
        acceptance,
        "KNOWN_FAILED",
        frozenset(
            hashlib.sha256(row.interaction_ref.encode()).hexdigest() for row in rows
        ),
    )
    report = SimpleNamespace(
        matching_interaction_count=2,
        pending_audit_count=0,
        pending_usage_count=0,
        failed_audit_count=2,
        failed_usage_count=2,
        interactions=rows,
    )
    if mutation == "reordered":
        rows.reverse()
    elif mutation == "missing":
        rows.pop()
    elif mutation == "unknown":
        rows[0].interaction_ref = "unknown"
    elif mutation == "duplicate":
        rows[0] = rows[1]
    elif mutation in ("pending_audit", "pending_usage", "complete"):
        attribute = "usage_status" if mutation == "pending_usage" else "audit_status"
        setattr(rows[0], attribute, "complete" if mutation == "complete" else "pending")
    elif mutation == "count":
        report.matching_interaction_count = 3
    elif mutation == "pending_count":
        report.pending_usage_count = 1
    elif mutation == "failed_count":
        report.failed_audit_count = 1
    service = ModuleType("app.services.authority_reconciliation")
    setattr(service, "incomplete_ingestion_status", lambda **_kwargs: report)
    monkeypatch.setitem(sys.modules, service.__name__, service)
    if mutation in (None, "reordered"):
        acceptance.assert_status()
    else:
        with pytest.raises(RuntimeError, match="baseline mismatch"):
            acceptance.assert_status()


@pytest.mark.parametrize("status_only", [False, True])
def test_acceptance_wraps_unchanged_candidate_with_status_checks(
    acceptance, monkeypatch, capsys, status_only
) -> None:
    calls = []
    candidate = {
        "_require_staging": lambda: calls.append("gates"),
        "run": lambda *origins: calls.append(origins),
    }

    def load(path):
        assert path == "/app/infra-staging/provider_v7_smoke.py"
        return candidate

    monkeypatch.setattr(acceptance.runpy, "run_path", load)
    monkeypatch.setattr(acceptance, "assert_status", lambda: calls.append("status"))
    origins = ("https://platform.test", "https://provider.test", "https://public.test")
    monkeypatch.setattr(
        sys, "argv", ["probe", "--status-only"] if status_only else ["probe", *origins]
    )
    assert acceptance.main() == 0
    assert calls == (
        ["gates", "status"] if status_only else ["gates", "status", origins, "status"]
    )
    assert capsys.readouterr().out == (
        "V7_STATUS PASS\n" if status_only else "V7_PROVIDER_SMOKE PASS\n"
    )


def test_acceptance_stops_before_chat_on_baseline_failure(
    acceptance, monkeypatch, capsys
) -> None:
    def fail():
        raise RuntimeError("sensitive-value")

    def forbidden(*_args):
        pytest.fail("Candidate chat must not run after a failed baseline")

    monkeypatch.setattr(
        acceptance.runpy,
        "run_path",
        lambda _path: {"_require_staging": lambda: None, "run": forbidden},
    )
    monkeypatch.setattr(acceptance, "assert_status", fail)
    monkeypatch.setattr(sys, "argv", ["probe", "one", "two", "three"])
    assert acceptance.main() == 1
    assert capsys.readouterr().out == "V7_PROVIDER_SMOKE FAIL\n"


def test_acceptance_rechecks_baseline_after_candidate_chat(
    acceptance, monkeypatch, capsys
) -> None:
    calls = []

    def status():
        calls.append("status")
        if len(calls) > 1:
            raise RuntimeError("sensitive-value")

    monkeypatch.setattr(
        acceptance.runpy,
        "run_path",
        lambda _path: {
            "_require_staging": lambda: None,
            "run": lambda *_args: calls.append("chat"),
        },
    )
    monkeypatch.setattr(acceptance, "assert_status", status)
    monkeypatch.setattr(sys, "argv", ["probe", "one", "two", "three"])
    assert acceptance.main() == 1
    assert calls == ["status", "chat", "status"]
    assert capsys.readouterr().out == "V7_PROVIDER_SMOKE FAIL\n"


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
    monkeypatch.delenv("PYTHON_ENV", raising=False)
    monkeypatch.setenv("MOUNT_PROFILE", "public")
    monkeypatch.delenv("PLATFORM_AGENT_API_ENABLED", raising=False)
    selected_clients: list[str] = []

    def credential(*, client_id: str):
        selected_clients.append(client_id)
        return SimpleNamespace(
            get_token=lambda _scope: SimpleNamespace(token="token-sensitive-value")
        )

    monkeypatch.setattr("azure.identity.ManagedIdentityCredential", credential)
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
            "00000000-0000-0000-0000-000000000001",
        ],
    )

    assert public_probe.main() == 0
    assert selected_clients == ["00000000-0000-0000-0000-000000000001"]
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
        "'api://00000000-0000-0000-0000-000000000000/.default' "
        "'00000000-0000-0000-0000-000000000001'"
    )
    assert "WORKLOAD_TOKEN" not in wrapper
    assert "provider_v7_public_probe.py" in wrapper
    assert "provider_v7_acceptance.py" in wrapper
    assert "/usr/bin/expect" in wrapper
    assert '--command "$command"' not in wrapper
    assert '.name == "API_BASE_URL"' in wrapper
    assert '"$PLATFORM_ORIGIN"' in wrapper
    assert '"https://$PLATFORM_FQDN"' not in wrapper
    assert "EXPECTED_PUBLIC_CLIENT_ID" in wrapper
    assert '"$PUBLIC_CLIENT_ID"' in wrapper
    assert "EXPECTED_PLATFORM_IMAGE_DIGEST" in wrapper
    assert 'EXPECTED_PLATFORM_CONTAINER_NAME="cosmaneura-staging-app-web"' in wrapper
    assert '--arg expected_container "$EXPECTED_PLATFORM_CONTAINER_NAME"' in wrapper
    assert ".containers[0].name == $expected_container" in wrapper
    assert ".containers[0].name == $app" not in wrapper
    assert "cosmaneura/cosmaneura@$EXPECTED_PLATFORM_IMAGE_DIGEST" in wrapper
    assert "cosmaneura/cosmaneura:$HEAD" not in wrapper
    assert "properties.latestReadyRevisionName" in wrapper
    assert "PLATFORM_READY_REVISION" in wrapper
    assert "containerapp revision show" in wrapper
    assert '--revision "$PLATFORM_READY_REVISION"' in wrapper
    assert "properties.template.containers[0].image --output tsv" not in wrapper
    assert len(command) < 1000
    assert "V7_SMOKE PASS" in wrapper


def test_current_image_status_command_stays_below_exec_limit() -> None:
    wrapper = (ROOT / "infra-staging" / "run-provider-v7-status.sh").read_text()
    source = (ROOT / "infra-staging" / "provider_v7_acceptance.py").read_bytes()
    payload = base64.b64encode(zlib.compress(source, 9)).decode()
    command = (
        'PYTHONPATH=/app /opt/venv/bin/python -c "import base64,zlib;'
        f"exec(zlib.decompress(base64.b64decode('{payload}')))\" --status-only"
    )
    assert len(command) < 2000
    assert "provider_v7_acceptance.py" in wrapper
    assert "--status-only" in wrapper
    assert "interaction_ref" in source.decode()
    assert "/usr/bin/expect" in wrapper
    assert "V7_STATUS PASS" in wrapper


def test_full_smoke_rejects_alternate_platform_origin_before_exec(tmp_path) -> None:
    wrapper = ROOT / "infra-staging" / "run-provider-v7-smoke.sh"
    fake_az = tmp_path / "az"
    az_log = tmp_path / "az.log"
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
        '  *latestReadyRevision*) echo \'{"latestRevision":"platform--ready","latestReadyRevision":"platform--ready","activeRevisionsMode":"Single","traffic":[{"latestRevision":true,"weight":100}]}\' ;;\n'
        '  *\'containerapp revision show\'*) echo \'{"containers":[{"name":"cosmaneura-staging-app-web","image":"cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","env":[{"name":"API_BASE_URL","value":"https://attacker.example","secretRef":null}]}],"health":"Healthy","running":"Running","provisioning":"Provisioned"}\' ;;\n'
        "  *PLATFORM_AGENT_API_SCOPE*) echo api://00000000-0000-0000-0000-000000000000/.default ;;\n"
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
            "EXPECTED_PLATFORM_IMAGE_DIGEST": "sha256:" + "a" * 64,
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
