"""Exercise diagnostic output with synthetic configuration and an offline SDK."""

from types import SimpleNamespace

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
    ServiceRequestError,
)

import debug_azure_blob as diagnostics

KEY = "HEAD-synthetic-account-key-TAIL"
ACCOUNT_URL = (
    "https://synthetic-user:synthetic-password@storage.invalid/?sig=synthetic-sas"
)
CONTAINER = "synthetic-container-private"
PREFIX = "synthetic-prefix-private"
BLOB = "synthetic-blob-private"
REMOTE_DETAIL = "synthetic-remote-private"
ERROR_DETAIL = f"{ACCOUNT_URL} {KEY} {BLOB} {REMOTE_DETAIL}"


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch):
    state = SimpleNamespace(
        calls=[], fail_at=None, error=None, exists=True, yielded=0, fail_after=0
    )

    def call(phase, *args):
        state.calls.append((phase, args))
        if state.fail_at == phase and (
            phase != "list_iteration" or state.yielded >= state.fail_after
        ):
            raise state.error

    class Blob:
        def exists(self):
            call("exists")
            return state.exists

        def get_blob_properties(self):
            call("blob_properties")
            return SimpleNamespace(
                size=42,
                content_settings=SimpleNamespace(content_type=REMOTE_DETAIL),
                last_modified=REMOTE_DETAIL,
            )

    class Container:
        def get_container_properties(self):
            call("container_properties")
            return SimpleNamespace(last_modified=REMOTE_DETAIL)

        def get_blob_client(self, name):
            call("blob_client", name)
            return Blob()

        def list_blobs(self, *, name_starts_with):
            call("list", name_starts_with)
            for _ in range(5):
                call("list_iteration")
                state.yielded += 1
                yield SimpleNamespace(name=BLOB)

    class Service:
        def get_container_client(self, name):
            call("container", name)
            return Container()

    def client(account_url, *, credential):
        call("client", account_url, credential)
        return Service()

    def no_network(*args, **kwargs):
        pytest.fail("Diagnostic tests must never access a network")

    monkeypatch.setattr("socket.socket.connect", no_network)
    monkeypatch.setattr("socket.getaddrinfo", no_network)
    monkeypatch.setattr(
        diagnostics, "_read_config", lambda: (ACCOUNT_URL, KEY, CONTAINER)
    )
    monkeypatch.setattr(diagnostics, "BlobServiceClient", client)
    monkeypatch.setattr(
        "sys.argv",
        [
            "debug_azure_blob.py",
            "--prefix",
            PREFIX,
            "--check-blob",
            BLOB,
            "--limit",
            "2",
        ],
    )
    return state


def assert_redacted(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    output = captured.out + captured.err
    for value in (
        KEY,
        "HEAD",
        "TAIL",
        ACCOUNT_URL,
        "synthetic-user",
        "synthetic-password",
        "synthetic-sas",
        CONTAINER,
        PREFIX,
        BLOB,
        REMOTE_DETAIL,
    ):
        assert value not in output
    return output


@pytest.mark.parametrize("exists", [True, False])
def test_success_retains_read_checks_and_limit_without_echoing_values(
    sdk, capsys, exists
):
    sdk.exists = exists
    assert diagnostics.main() == 0
    output = assert_redacted(capsys)
    assert "account_url: configured" in output
    assert "account_key: configured" in output
    assert "container: configured" in output
    assert f"Blob exists: {exists}" in output
    assert "Total listed: 2" in output
    assert sdk.yielded == 2
    assert ("client", (ACCOUNT_URL, KEY)) in sdk.calls
    assert ("container", (CONTAINER,)) in sdk.calls
    assert ("blob_client", (BLOB,)) in sdk.calls
    assert ("list", (PREFIX,)) in sdk.calls
    assert any(phase == "blob_properties" for phase, _ in sdk.calls) is exists


@pytest.mark.parametrize("missing", [0, 1, 2])
def test_missing_configuration_returns_two_without_constructing_client(
    sdk, monkeypatch, capsys, missing
):
    values: list[str | None] = [ACCOUNT_URL, KEY, CONTAINER]
    values[missing] = None
    monkeypatch.setattr(diagnostics, "_read_config", lambda: tuple(values))
    assert diagnostics.main() == 2
    output = assert_redacted(capsys)
    assert "missing" in output
    assert sdk.calls == []


@pytest.mark.parametrize(
    ("exception", "category"),
    [
        (ClientAuthenticationError, "Authentication failed"),
        (ResourceNotFoundError, "Container or blob not found"),
        (ServiceRequestError, "Network/service request error"),
        (HttpResponseError, "Azure HTTP error"),
        (RuntimeError, "Unexpected error"),
    ],
)
@pytest.mark.parametrize(
    "phase",
    ["client", "container_properties", "exists", "blob_properties", "list_iteration"],
)
def test_sdk_failures_emit_only_fixed_categories(
    sdk, capsys, exception, category, phase
):
    sdk.fail_at = phase
    sdk.error = exception(ERROR_DETAIL)
    assert diagnostics.main() == 1
    output = assert_redacted(capsys)
    assert category in output
    assert "Total listed" not in output


def test_configuration_failure_is_redacted(sdk, monkeypatch, capsys):
    def fail():
        raise RuntimeError(ERROR_DETAIL)

    monkeypatch.setattr(diagnostics, "_read_config", fail)
    assert diagnostics.main() == 1
    assert "Unexpected error" in assert_redacted(capsys)
    assert sdk.calls == []


def test_partial_listing_failure_cannot_report_success(sdk, capsys):
    sdk.fail_at = "list_iteration"
    sdk.fail_after = 1
    sdk.error = RuntimeError(ERROR_DETAIL)
    assert diagnostics.main() == 1
    output = assert_redacted(capsys)
    assert "Unexpected error" in output
    assert "Total listed" not in output
    assert sdk.yielded == 1


def test_container_override_is_used_without_echo(sdk, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["debug_azure_blob.py", "--container", BLOB])
    assert diagnostics.main() == 0
    assert_redacted(capsys)
    assert ("container", (BLOB,)) in sdk.calls
    assert ("blob_client", (BLOB,)) not in sdk.calls
    assert ("list", (None,)) in sdk.calls


def test_help_remains_available_without_loading_configuration(sdk, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["debug_azure_blob.py", "--help"])
    with pytest.raises(SystemExit) as raised:
        diagnostics.main()
    assert raised.value.code == 0
    assert "--check-blob" in assert_redacted(capsys)
    assert sdk.calls == []


@pytest.mark.parametrize(
    "argument", ["--unknown=synthetic-sas", "--limit=synthetic-sas"]
)
def test_invalid_arguments_do_not_echo_input(sdk, monkeypatch, capsys, argument):
    monkeypatch.setattr("sys.argv", ["debug_azure_blob.py", argument])
    with pytest.raises(SystemExit) as raised:
        diagnostics.main()
    assert raised.value.code == 2
    assert "Invalid arguments" in assert_redacted(capsys)
    assert sdk.calls == []
