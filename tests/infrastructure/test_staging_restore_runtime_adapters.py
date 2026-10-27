from __future__ import annotations

import hashlib
import json
import os
import signal
import ssl
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import staging_restore_runtime_adapters as adapters  # noqa: E402

sys.path.pop(0)

CLIENT_ID = "11111111-1111-4111-8111-111111111111"
IDENTITY_ENDPOINT = "http://127.0.0.1:42356/msi/token"
IDENTITY_HEADER = "opaque-platform-secret"
NOW = 2_000_000_000
TOKEN = "header.payload.signature"
VERIFIER_DIGEST = f"sha256:{'d' * 64}"


class Headers:
    def __init__(self, values: Mapping[str, str | list[str]]) -> None:
        self.values = {
            name.lower(): value if isinstance(value, list) else [value]
            for name, value in values.items()
        }

    def get_all(self, name: str) -> list[str] | None:
        return self.values.get(name.lower())


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        url: str = "",
        headers: Mapping[str, str | list[str]] | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.url = url
        self.headers = Headers(
            headers
            or {
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
            }
        )

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def geturl(self) -> str:
        return self.url

    def read(self, maximum: int) -> bytes:
        return self.body[:maximum]


class FakeOpener:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[Any, int]] = []

    def open(self, request: Any, *, timeout: int) -> Any:
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if not response.url:
            response.url = request.full_url
        return response


class AlarmResponse(FakeResponse):
    def read(self, maximum: int) -> bytes:
        signal.raise_signal(signal.SIGALRM)
        return super().read(maximum)


def _token_body(**changes: Any) -> bytes:
    value = {
        "access_token": TOKEN,
        "client_id": CLIENT_ID,
        "expires_on": str(NOW + 3_600),
        "resource": adapters.ARM_RESOURCE,
        "token_type": "Bearer",
    }
    value.update(changes)
    return json.dumps(value, separators=(",", ":")).encode()


def _token_source(
    opener: FakeOpener,
) -> adapters.ManagedIdentityTokenSource:
    return adapters.ManagedIdentityTokenSource(
        client_id=CLIENT_ID,
        identity_endpoint=IDENTITY_ENDPOINT,
        identity_header=IDENTITY_HEADER,
        opener=opener,
        clock=lambda: NOW,
    )


def _provider(
    *,
    provider_response: FakeResponse,
    token_body: bytes | None = None,
) -> tuple[adapters.AzureSourceObservationProvider, FakeOpener, FakeOpener]:
    identity = FakeOpener([FakeResponse(token_body or _token_body())])
    provider = FakeOpener([provider_response])
    return (
        adapters.AzureSourceObservationProvider(
            _token_source(identity), opener=provider
        ),
        identity,
        provider,
    )


def _provider_call(provider: adapters.AzureSourceObservationProvider) -> Any:
    return provider.get_flexible_server(
        management_origin=adapters.ARM_ORIGIN,
        resource_id=adapters.SOURCE_RESOURCE_ID,
        api_version=adapters.POSTGRES_API_VERSION,
        timeout_seconds=adapters.REQUEST_TIMEOUT_SECONDS,
    )


def _headers(request: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in request.header_items()}


def _verifier_arguments() -> tuple[str, ...]:
    policy = json.loads(adapters.attestation.POLICY_PATH.read_text())
    return adapters.attestation._build_verifier_arguments(
        policy,
        {"workflow_sha": "a" * 40, "protected_ref": "refs/heads/master"},
    )


def _offline_files() -> dict[str, bytes]:
    return {
        "evidence-manifest.json": b'{"manifest":true}\n',
        "attestation.json": b'{"attestation":true}',
        "github-trusted-root.jsonl": b'{"root":true}\n',
    }


def _runtime_verifier_arguments() -> tuple[str, ...]:
    identity = (
        "https://github.com/CosmaNeura/cosmaneura/.github/workflows/"
        "build_and_publish_local.yml@refs/heads/master"
    )
    dynamic = {7: identity, 24: "a" * 40, 26: "a" * 40}
    return tuple(
        (
            adapters.RUNTIME_SIGNATURE_STATIC_ARGUMENTS[index]
            if index in adapters.RUNTIME_SIGNATURE_STATIC_ARGUMENTS
            else dynamic[index]
        )
        for index in range(29)
    )


def _runtime_offline_files() -> dict[str, bytes]:
    return {
        "runtime-record.json": b'{"runtime":true}',
        "runtime-record.sigstore.json": b'{"signature":true}',
        "github-trusted-root.jsonl": b'{"root":true}\n',
    }


class FakeVerifierRuntime:
    def __init__(self, expected_files: Mapping[str, bytes] | None = None) -> None:
        self.loader_calls: list[tuple[Path, str]] = []
        self.runner_calls: list[dict[str, Any]] = []
        self.result: tuple[Any, Any, Any] = (0, b"[]", b"")
        self.loader_digest = VERIFIER_DIGEST
        self.expected_files = dict(expected_files or _offline_files())

    @contextmanager
    def loader(self, path: Path, expected_sha256: str) -> Iterator[tuple[int, str]]:
        self.loader_calls.append((path, expected_sha256))
        yield 73, self.loader_digest

    def runner(self, **kwargs: Any) -> tuple[int, bytes, bytes]:
        self.runner_calls.append(kwargs)
        workspace = kwargs["cwd"]
        assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
        assert stat.S_IMODE((workspace / "gh").stat().st_mode) == 0o700
        for name, raw in self.expected_files.items():
            path = workspace / name
            assert path.read_bytes() == raw
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        return self.result


def _verifier(
    runtime: FakeVerifierRuntime | None = None,
    *,
    profile: str = adapters.EVIDENCE_ATTESTATION_PROFILE,
) -> tuple[adapters.DigestPinnedGitHubCliVerifier, FakeVerifierRuntime]:
    runtime = runtime or FakeVerifierRuntime()
    return (
        adapters.DigestPinnedGitHubCliVerifier(
            executable_path=Path("/opt/cosmaneura/bin/gh"),
            expected_sha256=VERIFIER_DIGEST,
            expected_version=adapters.GH_VERSION,
            profile=profile,
            executable_loader=runtime.loader,
            process_runner=runtime.runner,
        ),
        runtime,
    )


def _copy_system_ca_bundle(destination: Path) -> Path:
    candidates = (
        adapters.RUNTIME_CA_BUNDLE_PATH,
        Path(ssl.get_default_verify_paths().cafile or ""),
        Path("/etc/ssl/cert.pem"),
    )
    source = next(path for path in candidates if path.is_file())
    destination.write_bytes(source.read_bytes())
    destination.chmod(0o644)
    return destination


def test_default_http_clients_pin_tls_and_disable_proxies_and_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_bundle = _copy_system_ca_bundle(tmp_path / "runtime-ca.pem")
    monkeypatch.setenv("SSL_CERT_FILE", "/attacker/ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/attacker/certs")
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "tls-keys.log"))
    monkeypatch.setattr(adapters, "RUNTIME_CA_BUNDLE_PATH", ca_bundle)
    opener = adapters._default_opener()
    https_handlers = [
        handler
        for handler in opener.handlers
        if isinstance(handler, urllib.request.HTTPSHandler)
    ]
    assert len(https_handlers) == 1
    context = getattr(https_handlers[0], "_context")
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.keylog_filename is None
    assert context.cert_store_stats()["x509_ca"] > 0

    proxy_handlers = [
        handler
        for handler in opener.handlers
        if isinstance(handler, urllib.request.ProxyHandler)
    ]
    # urllib omits an explicitly empty ProxyHandler from the installed chain.
    assert proxy_handlers == []
    assert any(
        isinstance(handler, adapters._NoRedirects) for handler in opener.handlers
    )
    assert not (tmp_path / "tls-keys.log").exists()


def test_runtime_tls_context_rejects_mutable_or_linked_ca_bundle(
    tmp_path: Path,
) -> None:
    ca_bundle = _copy_system_ca_bundle(tmp_path / "mutable-ca.pem")
    ca_bundle.chmod(0o666)
    with pytest.raises(adapters.RuntimeAdapterFailure, match="configuration_invalid"):
        adapters._runtime_tls_context(ca_bundle)

    target = _copy_system_ca_bundle(tmp_path / "target-ca.pem")
    link = tmp_path / "linked-ca.pem"
    link.symlink_to(target)
    with pytest.raises(adapters.RuntimeAdapterFailure, match="configuration_invalid"):
        adapters._runtime_tls_context(link)


def test_adapter_constants_match_the_dormant_policy_interfaces() -> None:
    provider_policy = json.loads(adapters.observation.POLICY_PATH.read_text())
    assert provider_policy["provider"] == {
        "kind": "azure-resource-manager",
        "management_origin": adapters.ARM_ORIGIN,
        "oauth_resource": adapters.ARM_RESOURCE,
        "http_method": "GET",
        "api_version": adapters.POSTGRES_API_VERSION,
        "request_timeout_seconds": adapters.REQUEST_TIMEOUT_SECONDS,
        "maximum_response_bytes": adapters.MAXIMUM_PROVIDER_BYTES,
        "response_media_type": "application/json",
        "redirects_permitted": False,
        "maximum_requests": 1,
    }
    assert provider_policy["source"]["resource_id"] == adapters.SOURCE_RESOURCE_ID
    assert _verifier_arguments() == tuple(
        adapters.STATIC_VERIFIER_ARGUMENTS.get(index, "a" * 40) for index in range(29)
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:42356/msi/token",
        "http://example.com:42356/msi/token",
        "http://user@127.0.0.1:42356/msi/token",
        "http://127.0.0.1/msi/token",
        "http://127.0.0.1:42356/msi/token?resource=evil",
        "http://127.0.0.1:42356/a/../token",
        "http://127.0.0.1:42356/%2e%2e/token",
    ],
)
def test_managed_identity_rejects_nonlocal_or_ambiguous_endpoint(
    endpoint: str,
) -> None:
    with pytest.raises(
        adapters.RuntimeAdapterFailure,
        match="managed_identity.configuration_invalid",
    ):
        adapters.ManagedIdentityTokenSource(
            client_id=CLIENT_ID,
            identity_endpoint=endpoint,
            identity_header=IDENTITY_HEADER,
        )


def test_managed_identity_uses_exact_identity_audience_once() -> None:
    opener = FakeOpener([FakeResponse(_token_body())])
    source = _token_source(opener)

    assert (
        source.get_token(
            resource=adapters.ARM_RESOURCE,
            timeout_seconds=adapters.REQUEST_TIMEOUT_SECONDS,
        )
        == TOKEN
    )
    with pytest.raises(adapters.RuntimeAdapterFailure, match="already_used"):
        source.get_token(
            resource=adapters.ARM_RESOURCE,
            timeout_seconds=adapters.REQUEST_TIMEOUT_SECONDS,
        )
    assert len(opener.calls) == 1
    request, timeout = opener.calls[0]
    assert (request.method, timeout) == ("GET", 30)
    parsed = urllib.parse.urlsplit(request.full_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == IDENTITY_ENDPOINT
    assert urllib.parse.parse_qs(parsed.query) == {
        "api-version": [adapters.IDENTITY_API_VERSION],
        "client_id": [CLIENT_ID],
        "resource": [adapters.ARM_RESOURCE],
    }
    headers = _headers(request)
    assert headers["x-identity-header"] == IDENTITY_HEADER
    assert headers["metadata"] == "true"
    assert "authorization" not in headers


def test_managed_identity_reads_exact_environment_plumbing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://[::1]:42356/msi/token")
    monkeypatch.setenv("IDENTITY_HEADER", IDENTITY_HEADER)
    opener = FakeOpener([FakeResponse(_token_body())])
    source = adapters.ManagedIdentityTokenSource.from_environment(
        client_id=CLIENT_ID,
        opener=opener,
        clock=lambda: NOW,
    )
    assert source.get_token(resource=adapters.ARM_RESOURCE, timeout_seconds=30) == TOKEN


@pytest.mark.parametrize(
    ("body", "headers", "error"),
    [
        (
            _token_body(client_id="22222222-2222-4222-8222-222222222222"),
            None,
            "managed_identity.response_invalid",
        ),
        (
            _token_body(resource="https://storage.azure.com/"),
            None,
            "managed_identity.response_invalid",
        ),
        (
            _token_body(expires_on=str(NOW + 30)),
            None,
            "managed_identity.response_invalid",
        ),
        (
            _token_body(expires_on="\u00b2"),
            None,
            "managed_identity.response_invalid",
        ),
        (
            b'{"access_token":"one","access_token":"two"}',
            None,
            "managed_identity.response_invalid",
        ),
        (
            b"x" * (adapters.MAXIMUM_TOKEN_BYTES + 1),
            {
                "Content-Type": "application/json",
                "Content-Length": str(adapters.MAXIMUM_TOKEN_BYTES + 1),
            },
            "http_response.too_large",
        ),
        (
            _token_body(),
            {"Content-Type": ["application/json", "text/plain"]},
            "http_response.invalid",
        ),
    ],
    ids=[
        "wrong-client",
        "wrong-resource",
        "expired",
        "unicode-digit-expiry",
        "duplicate-key",
        "oversized",
        "duplicate-content-type",
    ],
)
def test_managed_identity_rejects_unbound_or_malformed_responses(
    body: bytes,
    headers: Mapping[str, str | list[str]] | None,
    error: str,
) -> None:
    opener = FakeOpener([FakeResponse(body, headers=headers)])
    with pytest.raises(adapters.RuntimeAdapterFailure, match=error):
        _token_source(opener).get_token(
            resource=adapters.ARM_RESOURCE, timeout_seconds=30
        )


def test_managed_identity_errors_are_bounded_and_allow_only_known_resources() -> None:
    secret = "must-not-escape-managed-identity"
    opener = FakeOpener([RuntimeError(secret)])
    source = _token_source(opener)
    with pytest.raises(adapters.RuntimeAdapterFailure) as raised:
        source.get_token(resource=adapters.ARM_RESOURCE, timeout_seconds=30)
    assert secret not in str(raised.value)

    source = _token_source(FakeOpener([]))
    with pytest.raises(adapters.RuntimeAdapterFailure, match="request_invalid"):
        source.get_token(resource="https://evil.example/", timeout_seconds=30)

    source = _token_source(FakeOpener([]))
    with pytest.raises(adapters.RuntimeAdapterFailure, match="request_invalid"):
        source.get_token(resource=adapters.ARM_RESOURCE, timeout_seconds=30.0)  # type: ignore[arg-type]


@pytest.mark.skipif(
    not hasattr(signal, "raise_signal") or not hasattr(signal, "setitimer"),
    reason="POSIX wall-clock deadline is unavailable",
)
@pytest.mark.parametrize("stage", ["identity", "arm"])
def test_http_deadline_interrupt_cannot_be_swallowed(stage: str) -> None:
    token_response: FakeResponse = (
        AlarmResponse(_token_body())
        if stage == "identity"
        else FakeResponse(_token_body())
    )
    arm_response: FakeResponse = (
        AlarmResponse(b"{}") if stage == "arm" else FakeResponse(b"{}")
    )
    identity = FakeOpener([token_response])
    provider = adapters.AzureSourceObservationProvider(
        _token_source(identity), opener=FakeOpener([arm_response])
    )
    previous_handler = signal.getsignal(signal.SIGALRM)

    with pytest.raises(adapters.RuntimeAdapterFailure, match="http_request.timeout"):
        _provider_call(provider)

    assert signal.getsignal(signal.SIGALRM) == previous_handler
    delay, interval = signal.getitimer(signal.ITIMER_REAL)
    assert delay == interval == 0


@pytest.mark.skipif(
    not hasattr(signal, "raise_signal") or not hasattr(signal, "setitimer"),
    reason="POSIX wall-clock deadline is unavailable",
)
def test_http_deadline_interrupt_during_teardown_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear = adapters._clear_wall_clock_deadline
    calls = 0

    def interrupt_once(previous_handler: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise adapters._DeadlineExpired
        clear(previous_handler)

    monkeypatch.setattr(adapters, "_clear_wall_clock_deadline", interrupt_once)
    with pytest.raises(adapters.RuntimeAdapterFailure, match="http_request.timeout"):
        with adapters._wall_clock_deadline(30):
            pass
    assert calls == 2
    delay, interval = signal.getitimer(signal.ITIMER_REAL)
    assert delay == interval == 0


def test_http_deadline_refuses_a_multithreaded_process() -> None:
    started = threading.Event()
    release = threading.Event()

    def wait() -> None:
        started.set()
        release.wait(timeout=5)

    worker = threading.Thread(target=wait)
    worker.start()
    try:
        assert started.wait(timeout=5)
        with pytest.raises(
            adapters.RuntimeAdapterFailure,
            match="http_request.deadline_unavailable",
        ):
            with adapters._wall_clock_deadline(30):
                pass
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()


def test_source_provider_performs_one_exact_raw_byte_get() -> None:
    raw = b'{"provider":"exact raw bytes"}\n'
    provider, identity_opener, arm_opener = _provider(
        provider_response=FakeResponse(raw)
    )

    response = _provider_call(provider)

    assert response == adapters.observation.ProviderObservationResponse(
        status=200,
        content_type="application/json; charset=utf-8",
        body=raw,
    )
    assert len(identity_opener.calls) == len(arm_opener.calls) == 1
    request, timeout = arm_opener.calls[0]
    assert timeout == 30
    assert request.method == "GET"
    assert request.full_url == (
        f"{adapters.ARM_ORIGIN}{adapters.SOURCE_RESOURCE_ID}"
        f"?api-version={adapters.POSTGRES_API_VERSION}"
    )
    headers = _headers(request)
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert headers["accept-encoding"] == "identity"
    assert "x-identity-header" not in headers
    with pytest.raises(adapters.RuntimeAdapterFailure, match="already_used"):
        _provider_call(provider)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"management_origin": "https://evil.example"}, "request_invalid"),
        ({"resource_id": "/subscriptions/other"}, "request_invalid"),
        ({"api_version": "2023-01-01"}, "request_invalid"),
        ({"timeout_seconds": 29}, "request_invalid"),
        ({"timeout_seconds": 30.0}, "request_invalid"),
    ],
)
def test_source_provider_rejects_target_changes_before_credentials(
    changes: dict[str, Any], error: str
) -> None:
    identity = FakeOpener([])
    arm = FakeOpener([])
    provider = adapters.AzureSourceObservationProvider(
        _token_source(identity), opener=arm
    )
    arguments = {
        "management_origin": adapters.ARM_ORIGIN,
        "resource_id": adapters.SOURCE_RESOURCE_ID,
        "api_version": adapters.POSTGRES_API_VERSION,
        "timeout_seconds": adapters.REQUEST_TIMEOUT_SECONDS,
    }
    arguments.update(changes)
    with pytest.raises(adapters.RuntimeAdapterFailure, match=error):
        provider.get_flexible_server(**arguments)
    assert identity.calls == arm.calls == []


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            FakeResponse(b"{}", url="https://evil.example/redirected"),
            "http_response.invalid",
        ),
        (
            FakeResponse(
                b"x" * (adapters.MAXIMUM_PROVIDER_BYTES + 1),
                headers={"Content-Type": "application/json"},
            ),
            "http_response.too_large",
        ),
        (
            FakeResponse(
                b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Content-Encoding": "gzip",
                },
            ),
            "http_response.invalid",
        ),
    ],
)
def test_source_provider_refuses_redirect_encoding_and_oversize(
    response: FakeResponse, error: str
) -> None:
    provider, _identity, _arm = _provider(provider_response=response)
    with pytest.raises(adapters.RuntimeAdapterFailure, match=error):
        _provider_call(provider)


def test_offline_verifier_stages_exact_files_and_scrubs_environment() -> None:
    verifier, runtime = _verifier()

    result = verifier.verify(
        arguments=_verifier_arguments(),
        files=_offline_files(),
        timeout_seconds=30,
        maximum_output_bytes=4_194_304,
    )

    assert result == adapters.attestation.AttestationVerifierResponse(
        returncode=0,
        stdout=b"[]",
        stderr=b"",
        verifier_version=adapters.GH_VERSION,
        verifier_sha256=VERIFIER_DIGEST,
    )
    assert runtime.loader_calls == [(Path("/opt/cosmaneura/bin/gh"), VERIFIER_DIGEST)]
    assert len(runtime.runner_calls) == 1
    call = runtime.runner_calls[0]
    assert call["executable_fd"] == 73
    assert call["arguments"] == _verifier_arguments()
    assert call["timeout_seconds"] == 30
    assert call["maximum_output_bytes"] == 4_194_304
    environment = call["environment"]
    assert set(environment) == {
        "ALL_PROXY",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GH_CONFIG_DIR",
        "GH_NO_UPDATE_NOTIFIER",
        "GH_PROMPT_DISABLED",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LC_ALL",
        "NO_PROXY",
        "TMPDIR",
    }
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:9"
    assert not Path(call["cwd"]).exists()
    with pytest.raises(adapters.RuntimeAdapterFailure, match="already_used"):
        verifier.verify(
            arguments=_verifier_arguments(),
            files=_offline_files(),
            timeout_seconds=30,
            maximum_output_bytes=4_194_304,
        )


def test_runtime_signature_profile_stages_only_its_exact_files_and_is_one_shot() -> (
    None
):
    files = _runtime_offline_files()
    verifier, runtime = _verifier(
        FakeVerifierRuntime(files),
        profile=adapters.RUNTIME_RECORD_SIGNATURE_PROFILE,
    )
    result = verifier.verify(
        arguments=_runtime_verifier_arguments(),
        files=files,
        timeout_seconds=30,
        maximum_output_bytes=4_194_304,
    )
    assert result.returncode == 0
    assert runtime.runner_calls[0]["arguments"] == _runtime_verifier_arguments()
    with pytest.raises(adapters.RuntimeAdapterFailure, match="already_used"):
        verifier.verify(
            arguments=_runtime_verifier_arguments(),
            files=files,
            timeout_seconds=30,
            maximum_output_bytes=4_194_304,
        )


@pytest.mark.parametrize(
    ("profile", "arguments", "files"),
    [
        (
            adapters.EVIDENCE_ATTESTATION_PROFILE,
            _runtime_verifier_arguments(),
            _runtime_offline_files(),
        ),
        (
            adapters.RUNTIME_RECORD_SIGNATURE_PROFILE,
            _verifier_arguments(),
            _offline_files(),
        ),
        (
            adapters.RUNTIME_RECORD_SIGNATURE_PROFILE,
            _runtime_verifier_arguments(),
            {**_runtime_offline_files(), "attestation.json": b"cross-profile"},
        ),
    ],
)
def test_offline_verifier_profiles_refuse_cross_profile_requests(
    profile: str, arguments: tuple[str, ...], files: dict[str, bytes]
) -> None:
    verifier, runtime = _verifier(profile=profile)
    with pytest.raises(adapters.RuntimeAdapterFailure):
        verifier.verify(
            arguments=arguments,
            files=files,
            timeout_seconds=30,
            maximum_output_bytes=4_194_304,
        )
    assert runtime.loader_calls == runtime.runner_calls == []


def test_runtime_signature_profile_rejects_identity_and_file_variations() -> None:
    bad_arguments = list(_runtime_verifier_arguments())
    bad_arguments[7] = "https://github.com/attacker/workflow.yml@refs/heads/main"
    bad_digest_arguments = list(_runtime_verifier_arguments())
    bad_digest_arguments[26] = "b" * 40
    requests = [
        (tuple(bad_arguments), _runtime_offline_files()),
        (tuple(bad_digest_arguments), _runtime_offline_files()),
        (
            _runtime_verifier_arguments(),
            {**_runtime_offline_files(), "runtime-record.json": b""},
        ),
    ]
    assert requests, (
        "there is nothing to check, so the assertions below would pass vacuously"
    )
    for arguments, files in requests:
        verifier, runtime = _verifier(profile=adapters.RUNTIME_RECORD_SIGNATURE_PROFILE)
        with pytest.raises(adapters.RuntimeAdapterFailure):
            verifier.verify(
                arguments=arguments,
                files=files,
                timeout_seconds=30,
                maximum_output_bytes=4_194_304,
            )
        assert runtime.loader_calls == runtime.runner_calls == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda arguments, _files: arguments.__setitem__(18, "--owner"),
        lambda arguments, _files: arguments.__setitem__(24, "not-a-sha"),
        lambda arguments, _files: arguments.__setitem__(26, "b" * 40),
        lambda _arguments, files: files.update({"extra": b"secret"}),
        lambda _arguments, files: files.update({"attestation.json": b""}),
    ],
)
def test_offline_verifier_rejects_argument_and_file_variations_before_execution(
    mutate: Any,
) -> None:
    arguments = list(_verifier_arguments())
    files = _offline_files()
    mutate(arguments, files)
    verifier, runtime = _verifier()
    with pytest.raises(adapters.RuntimeAdapterFailure):
        verifier.verify(
            arguments=tuple(arguments),
            files=files,
            timeout_seconds=30,
            maximum_output_bytes=4_194_304,
        )
    assert runtime.loader_calls == runtime.runner_calls == []


@pytest.mark.parametrize(
    ("result", "loader_digest", "error"),
    [
        ((0, "not-bytes", b""), VERIFIER_DIGEST, "result_invalid"),
        ((0, b"x" * 4_194_305, b""), VERIFIER_DIGEST, "result_invalid"),
        ((0, b"[]", b""), f"sha256:{'e' * 64}", "executable_invalid"),
    ],
)
def test_offline_verifier_rejects_invalid_runtime_results(
    result: tuple[Any, Any, Any], loader_digest: str, error: str
) -> None:
    runtime = FakeVerifierRuntime()
    runtime.result = result
    runtime.loader_digest = loader_digest
    verifier, _runtime = _verifier(runtime)
    with pytest.raises(adapters.RuntimeAdapterFailure, match=error):
        verifier.verify(
            arguments=_verifier_arguments(),
            files=_offline_files(),
            timeout_seconds=30,
            maximum_output_bytes=4_194_304,
        )


def test_offline_verifier_constructor_requires_absolute_pinned_linux_version() -> None:
    with pytest.raises(adapters.RuntimeAdapterFailure, match="configuration_invalid"):
        adapters.DigestPinnedGitHubCliVerifier(
            executable_path=Path("gh"),
            expected_sha256=VERIFIER_DIGEST,
            expected_version=adapters.GH_VERSION,
        )
    with pytest.raises(adapters.RuntimeAdapterFailure, match="configuration_invalid"):
        adapters.DigestPinnedGitHubCliVerifier(
            executable_path=Path("/opt/gh"),
            expected_sha256=VERIFIER_DIGEST,
            expected_version="latest",
        )
    with pytest.raises(adapters.RuntimeAdapterFailure, match="configuration_invalid"):
        adapters.DigestPinnedGitHubCliVerifier(
            executable_path=Path("/opt/gh"),
            expected_sha256=VERIFIER_DIGEST,
            expected_version=adapters.GH_VERSION,
            profile="caller-selected",
        )


@pytest.mark.skipif(sys.platform != "linux", reason="sealed verifier is Linux-only")
def test_linux_loader_executes_the_same_digest_pinned_bytes() -> None:
    shell = Path("/bin/sh").resolve(strict=True)
    digest = f"sha256:{hashlib.sha256(shell.read_bytes()).hexdigest()}"
    with pytest.raises(adapters.RuntimeAdapterFailure, match="executable_invalid"):
        with adapters._sealed_linux_executable(shell, f"sha256:{'0' * 64}"):
            pass
    with adapters._sealed_linux_executable(shell, digest) as (descriptor, observed):
        assert observed == digest
        assert os.pread(descriptor, 4, 0) == b"\x7fELF"
        returncode, stdout, stderr = adapters._run_bounded_process(
            executable_fd=descriptor,
            arguments=("gh", "-c", "printf verified"),
            cwd=Path("/tmp"),
            environment={"PATH": "/usr/bin:/bin"},
            timeout_seconds=2,
            maximum_output_bytes=64,
        )
    assert (returncode, stdout, stderr) == (0, b"verified", b"")


@pytest.mark.skipif(sys.platform != "linux", reason="process bounds are Linux-only")
@pytest.mark.parametrize(
    ("command", "timeout", "maximum", "error"),
    [
        ("sleep 2", 1, 64, "timeout"),
        ("printf '%0100d' 0", 2, 64, "output_too_large"),
    ],
)
def test_linux_runner_kills_timeout_and_output_overflow(
    command: str, timeout: int, maximum: int, error: str
) -> None:
    shell = Path("/bin/sh").resolve(strict=True)
    digest = f"sha256:{hashlib.sha256(shell.read_bytes()).hexdigest()}"
    with adapters._sealed_linux_executable(shell, digest) as (descriptor, _observed):
        with pytest.raises(adapters.RuntimeAdapterFailure, match=error):
            adapters._run_bounded_process(
                executable_fd=descriptor,
                arguments=("gh", "-c", command),
                cwd=Path("/tmp"),
                environment={"PATH": "/usr/bin:/bin"},
                timeout_seconds=timeout,
                maximum_output_bytes=maximum,
            )


@pytest.mark.skipif(sys.platform != "linux", reason="process bounds are Linux-only")
def test_linux_runner_kills_background_descendants_after_leader_exit(
    tmp_path: Path,
) -> None:
    shell = Path("/bin/sh").resolve(strict=True)
    digest = f"sha256:{hashlib.sha256(shell.read_bytes()).hexdigest()}"
    with adapters._sealed_linux_executable(shell, digest) as (descriptor, _observed):
        result = adapters._run_bounded_process(
            executable_fd=descriptor,
            arguments=(
                "gh",
                "-c",
                "sleep 30 </dev/null >/dev/null 2>&1 & echo $! > descendant.pid",
            ),
            cwd=tmp_path,
            environment={"PATH": "/usr/bin:/bin"},
            timeout_seconds=2,
            maximum_output_bytes=64,
        )
    assert result == (0, b"", b"")
    descendant = Path(f"/proc/{int((tmp_path / 'descendant.pid').read_text())}/stat")
    for _attempt in range(100):
        try:
            state = descendant.read_text().split()[2]
        except (FileNotFoundError, ProcessLookupError):
            break
        if state == "Z":
            break
        time.sleep(0.01)
    else:
        pytest.fail("background verifier descendant remained live")


def test_adapters_are_wired_only_in_dormant_executor_factory() -> None:
    executor_source = (
        Path(adapters.attestation.ROOT)
        .joinpath("scripts/staging_restore_trusted_executor.py")
        .read_text()
    )
    assert "staging_restore_runtime_adapters" in executor_source
    assert "receive_staging_broker_launch_receipt" in executor_source
    assert "PUBLIC_ENTRYPOINT_INTEGRATED = False" in executor_source
    adapter_symbols = (
        "staging_restore_runtime_adapters",
        "receive_staging_broker_launch_receipt",
        "read_staging_restore_evidence_material",
        "build_private_evidence_material_reader",
        "ManagedIdentityPrivateEvidenceMaterialReader",
        "ManagedIdentityTokenSource",
        "AzureSourceObservationProvider",
        "DigestPinnedGitHubCliVerifier",
    )
    arm_path = "observe_staging_controller_receipt_v2_arm.py"
    database_path = "observe_staging_controller_receipt_v2_database.py"
    isolation_inventory_path = (
        "observe_staging_controller_receipt_v2_isolation_inventory.py"
    )
    executable_paths = [
        *Path(adapters.attestation.ROOT).joinpath("scripts").glob("*.py"),
        *Path(adapters.attestation.ROOT).joinpath("scripts").glob("*.sh"),
        *Path(adapters.attestation.ROOT).joinpath(".github/workflows").glob("*.yml"),
        *Path(adapters.attestation.ROOT).joinpath(".github/workflows").glob("*.yaml"),
    ]
    for path in executable_paths:
        if path.name in {
            "staging_restore_runtime_adapters.py",
            "receive_staging_broker_launch_receipt.py",
        }:
            continue
        text = path.read_text()
        forbidden_symbols = adapter_symbols
        if path.name == "staging_restore_launch_material.py":
            forbidden_symbols = tuple(
                symbol
                for symbol in adapter_symbols
                if symbol != "receive_staging_broker_launch_receipt"
            )
        elif path.name == "read_staging_runtime_record_material.py":
            forbidden_symbols = tuple(
                symbol
                for symbol in adapter_symbols
                if symbol
                not in {
                    "staging_restore_runtime_adapters",
                    "DigestPinnedGitHubCliVerifier",
                }
            )
        elif path.name == "read_staging_restore_evidence_material.py":
            forbidden_symbols = tuple(
                symbol
                for symbol in adapter_symbols
                if symbol
                not in {
                    "staging_restore_runtime_adapters",
                    "read_staging_restore_evidence_material",
                    "build_private_evidence_material_reader",
                    "ManagedIdentityPrivateEvidenceMaterialReader",
                }
            )
        elif path.name in {
            "read_staging_controller_receipt_v2.py",
            "read_staging_controller_receipt_v2_prefix.py",
            database_path,
        }:
            forbidden_symbols = tuple(
                symbol
                for symbol in adapter_symbols
                if symbol != "staging_restore_runtime_adapters"
            )
        elif path.name == "read_staging_restore_launch_record.py":
            forbidden_symbols = tuple(
                symbol
                for symbol in adapter_symbols
                if symbol != "receive_staging_broker_launch_receipt"
            )
        elif path.name in {"staging_restore_safe_submit.py", arm_path}:
            forbidden_symbols = tuple(
                symbol
                for symbol in adapter_symbols
                if symbol
                not in {
                    "staging_restore_runtime_adapters",
                    "ManagedIdentityTokenSource",
                }
            )
        elif path.name == isolation_inventory_path:
            forbidden_symbols = tuple(
                symbol
                for symbol in adapter_symbols
                if symbol
                not in {
                    "staging_restore_runtime_adapters",
                    "ManagedIdentityTokenSource",
                }
            )
        elif path.name == "staging_restore_trusted_executor.py":
            forbidden_symbols = ()
        assert all(symbol not in text for symbol in forbidden_symbols), path
    for policy_path in (
        adapters.observation.POLICY_PATH,
        adapters.attestation.POLICY_PATH,
        Path(adapters.attestation.ROOT).joinpath(
            "contracts/staging-rehearsal/v1/restore-trusted-executor-policy.json"
        ),
        Path(adapters.attestation.ROOT).joinpath(
            "contracts/staging-rehearsal/v1/restore-runtime-binding-policy.json"
        ),
    ):
        policy = json.loads(policy_path.read_text())
        assert policy["activation_ready"] is False
        assert set(policy["activation"].values()) == {False}
