from __future__ import annotations

# pyright: reportMissingImports=false
import json
import socket
import urllib.error
from email.message import Message
from io import BytesIO
from pathlib import Path

import pytest

from infra.network_smoke import probe

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _address_record(address: str) -> tuple[int, int, int, str, tuple[str, int]]:
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))


def test_resolve_private_addresses_rejects_public_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        probe.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            _address_record("10.42.2.62"),
            _address_record("203.0.113.10"),
        ],
    )

    with pytest.raises(probe.ProbeFailure, match="dns_not_private"):
        probe.resolve_private_addresses("scribe.example.invalid")


def test_run_probe_requires_private_dns_and_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        probe.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_address_record("10.42.2.62")],
    )

    statuses = iter((200, 401))
    requests: list[tuple[str, int]] = []

    def request_status(*args: object, method: str, timeout_seconds: int) -> int:
        del args
        requests.append((method, timeout_seconds))
        return next(statuses)

    monkeypatch.setattr(probe, "_request_status", request_status)

    result = probe.run_probe(
        "https://scribe.example.invalid/healthz",
        "https://scribe.example.invalid/v1/consents",
    )

    assert result == {
        "dns": "private",
        "health_status": 200,
        "result": "passed",
        "unauthenticated_workflow_status": 401,
    }
    assert requests == [("GET", 60), ("POST", 60)]


def test_main_uses_image_owned_timeout_by_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SCRIBE_HEALTH_URL", "https://scribe.example.invalid/healthz")
    monkeypatch.setenv(
        "SCRIBE_UNAUTHENTICATED_WORKFLOW_URL",
        "https://scribe.example.invalid/v1/consents",
    )
    observed: dict[str, int] = {}

    def run_probe(*args: object, timeout_seconds: int) -> dict[str, object]:
        del args
        observed["timeout_seconds"] = timeout_seconds
        return {
            "dns": "private",
            "health_status": 200,
            "result": "passed",
            "unauthenticated_workflow_status": 401,
        }

    monkeypatch.setattr(probe, "run_probe", run_probe)

    assert probe.main() == 0
    assert observed == {"timeout_seconds": 60}
    assert json.loads(capsys.readouterr().out)["result"] == "passed"


def test_main_rejects_timeout_above_image_owned_maximum(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PROBE_TIMEOUT_SECONDS", "61")

    assert probe.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "code": "timeout_out_of_range",
        "result": "failed",
    }


def test_job_keeps_timeout_separate_and_disables_platform_retry() -> None:
    configuration = (REPOSITORY_ROOT / "infra/parallel_network_smoke.tf").read_text()

    assert "replica_timeout_in_seconds   = 300" in configuration
    assert "replica_retry_limit          = 0" in configuration
    assert "PROBE_TIMEOUT_SECONDS" not in configuration


def test_request_status_returns_http_error_without_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = urllib.error.HTTPError(
        url="https://scribe.example.invalid/v1/consents",
        code=401,
        msg="unauthorized",
        hdrs=Message(),
        fp=BytesIO(b"sensitive response body"),
    )
    monkeypatch.setattr(
        probe.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    assert (
        probe._request_status(
            "https://scribe.example.invalid/v1/consents",
            method="POST",
            timeout_seconds=10,
        )
        == 401
    )


def test_main_logs_only_sanitized_failure_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "SCRIBE_HEALTH_URL", "https://patient-name.example.invalid/healthz"
    )
    monkeypatch.setenv(
        "SCRIBE_UNAUTHENTICATED_WORKFLOW_URL",
        "https://patient-name.example.invalid/v1/consents",
    )
    monkeypatch.setattr(
        probe,
        "run_probe",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            probe.ProbeFailure("dns_not_private")
        ),
    )

    assert probe.main() == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {"code": "dns_not_private", "result": "failed"}
    assert "patient-name" not in output
