from __future__ import annotations

import copy
import email.message
import hashlib
import inspect
import io
import json
import pickle
import sys
import urllib.error
import urllib.parse
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import (  # noqa: E402
    staging_restore_runtime_adapters as adapters,
)
from scripts import (  # noqa: E402
    staging_sanitizer_bootstrap_job_live_provider as provider,
)

sys.path.pop(0)

CLIENT_ID = "11111111-1111-4111-8111-111111111111"
WRONG_CLIENT_ID = "22222222-2222-4222-8222-222222222222"
IDENTITY_ENDPOINT = "http://127.0.0.1:42356/msi/token"
IDENTITY_HEADER = "opaque-platform-secret"
TOKEN = "header.payload.signature"
NOW_EPOCH = 2_000_000_000
NOW = datetime(2033, 5, 18, 3, 33, 20, tzinfo=UTC)
REGISTRY_ID = (
    f"{provider.SUBSCRIPTION_SCOPE}/resourceGroups/cosmaneura-staging-rehearsal-control/"
    "providers/Microsoft.ContainerRegistry/registries/cosmaneurastgrecovery"
)
ROLE_ASSIGNMENT_NAME = "33333333-3333-4333-8333-333333333333"


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
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        if not value.url:
            value.url = request.full_url
        return value


@contextmanager
def _deadline(_timeout_seconds: int) -> Iterator[None]:
    yield


def _token_body(*, client_id: str = CLIENT_ID) -> bytes:
    return json.dumps(
        {
            "access_token": TOKEN,
            "client_id": client_id,
            "expires_on": str(NOW_EPOCH + 3600),
            "resource": adapters.ARM_RESOURCE,
            "token_type": "Bearer",
        },
        separators=(",", ":"),
    ).encode()


def _token_source(
    opener: FakeOpener, *, client_id: str = CLIENT_ID
) -> adapters.ManagedIdentityTokenSource:
    return adapters.ManagedIdentityTokenSource(
        client_id=client_id,
        identity_endpoint=IDENTITY_ENDPOINT,
        identity_header=IDENTITY_HEADER,
        opener=opener,
        clock=lambda: NOW_EPOCH,
    )


def _plan(phase: str = "present") -> provider.BootstrapJobLiveReadPlan:
    return provider._seal_reviewed_read_plan(
        phase=phase,
        identity_client_id=CLIENT_ID,
        private_registry_resource_id=REGISTRY_ID,
        private_registry_acrpull_role_assignment_name=ROLE_ASSIGNMENT_NAME,
    )


def _http_error(url: str, status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url, status, "refused", email.message.Message(), io.BytesIO(b"secret")
    )


def _provider(
    *,
    phase: str = "present",
    arm_responses: list[Any] | None = None,
    client_id: str = CLIENT_ID,
    clock: Any = lambda: NOW,
    monotonic: Any = lambda: 1.0,
) -> tuple[
    provider.AzureBootstrapJobLiveProvider,
    provider.BootstrapJobLiveReadPlan,
    FakeOpener,
    FakeOpener,
]:
    plan = _plan(phase)
    identity_opener = FakeOpener([FakeResponse(_token_body(client_id=client_id))])
    if arm_responses is None:
        arm_responses = [
            FakeResponse(json.dumps({"key": request.key}).encode())
            for request in plan._requests
        ]
    arm_opener = FakeOpener(arm_responses)
    live = provider.AzureBootstrapJobLiveProvider._from_reviewed_plan(
        plan=plan,
        token_source=_token_source(identity_opener, client_id=client_id),
        arm_opener=arm_opener,
        clock=clock,
        monotonic=monotonic,
        deadline_factory=_deadline,
    )
    return live, plan, identity_opener, arm_opener


def _headers(request: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in request.header_items()}


def test_policy_is_hash_pinned_upstream_bound_and_dormant() -> None:
    raw = provider.POLICY_PATH.read_bytes()
    policy = provider.load_bootstrap_job_live_provider_policy()

    assert hashlib.sha256(raw).hexdigest() == provider.POLICY_SHA256
    assert provider.attachment.CONTRACT_SHA256 == provider.ATTACHMENT_CONTRACT_SHA256
    assert policy == provider._expected_policy()
    assert policy["upstream_policy"]["sha256"] == (
        f"sha256:{provider.attachment.CONTRACT_SHA256}"
    )
    assert policy["transport"]["method"] == "GET"
    assert policy["transport"]["maximum_token_requests"] == 1
    assert policy["transport"]["maximum_arm_get_requests_per_plan"] == 4
    assert policy["status"]["one_shot_get_transport_implemented"] is True
    assert policy["status"]["live_provider_calls_enabled"] is False
    assert policy["status"]["activation_ready"] is False
    assert all(value is False for value in policy["claims"].values())


def test_policy_mutations_and_public_activation_refuse() -> None:
    policy = provider.load_bootstrap_job_live_provider_policy()
    mutations = (
        ("method", "PUT"),
        ("maximum_token_requests", 2),
        ("retries_permitted", True),
        ("caller_body_headers_method_query_or_url_permitted", True),
    )
    for key, value in mutations:
        changed = copy.deepcopy(policy)
        changed["transport"][key] = value
        with pytest.raises(
            provider.SanitizerBootstrapJobLiveProviderFailure,
            match="^bootstrap_job_live_provider.policy_invalid$",
        ):
            provider.validate_bootstrap_job_live_provider_policy(changed)

    with pytest.raises(
        provider.SanitizerBootstrapJobLiveProviderFailure,
        match=f"^{provider.ACTIVATION_FAILURE}$",
    ):
        provider.load_bootstrap_job_live_provider_policy(executable=True)
    with pytest.raises(
        provider.SanitizerBootstrapJobLiveProviderFailure,
        match=f"^{provider.ACTIVATION_FAILURE}$",
    ):
        provider.build_bootstrap_job_live_provider()


@pytest.mark.parametrize(
    "phase,count", (("initial_absence", 3), ("present", 4), ("final_absence", 3))
)
def test_sealed_plans_have_exact_phase_order_and_statuses(
    phase: str, count: int
) -> None:
    plan = _plan(phase)
    policy = provider.load_bootstrap_job_live_provider_policy()

    assert plan._is_valid()
    assert len(plan._requests) == count
    assert [item.key for item in plan._requests] == [
        item["key"] for item in policy["plans"][phase]
    ]
    assert [item.expected_status for item in plan._requests] == [
        item["expected_status"] for item in policy["plans"][phase]
    ]
    assert all(item._is_valid() for item in plan._requests)


@pytest.mark.parametrize(
    "change",
    (
        {"phase": "unknown"},
        {"identity_client_id": "AAAAAAAA-1111-4111-8111-111111111111"},
        {"private_registry_resource_id": REGISTRY_ID + "/repositories/other"},
        {
            "private_registry_resource_id": REGISTRY_ID.replace(
                "/subscriptions/", "/tenants/"
            )
        },
        {"private_registry_acrpull_role_assignment_name": "not-a-uuid"},
    ),
)
def test_plan_builder_rejects_redirected_or_malformed_inputs(
    change: dict[str, str],
) -> None:
    values = {
        "phase": "present",
        "identity_client_id": CLIENT_ID,
        "private_registry_resource_id": REGISTRY_ID,
        "private_registry_acrpull_role_assignment_name": ROLE_ASSIGNMENT_NAME,
        **change,
    }
    with pytest.raises(
        provider.SanitizerBootstrapJobLiveProviderFailure,
        match="^bootstrap_job_live_provider.plan_invalid$",
    ):
        provider._seal_reviewed_read_plan(**values)


def test_plan_is_exact_type_sealed_tamper_evident_and_nonserializable() -> None:
    plan = _plan()
    with pytest.raises(provider.SanitizerBootstrapJobLiveProviderFailure):
        provider.BootstrapJobLiveReadPlan()
    with pytest.raises(provider.SanitizerBootstrapJobLiveProviderFailure):
        copy.copy(plan)
    with pytest.raises(provider.SanitizerBootstrapJobLiveProviderFailure):
        copy.deepcopy(plan)
    with pytest.raises(provider.SanitizerBootstrapJobLiveProviderFailure):
        pickle.dumps(plan)

    object.__setattr__(plan, "request_plan_sha256", "0" * 64)
    assert not plan._is_valid()


def test_present_plan_uses_one_token_and_exact_get_sequence() -> None:
    live, plan, identity_opener, arm_opener = _provider()
    responses = live.read_all()

    assert len(identity_opener.calls) == 1
    assert len(arm_opener.calls) == 4
    assert len(responses) == 4
    for call, request_item, response in zip(
        arm_opener.calls, plan._requests, responses, strict=True
    ):
        request, timeout = call
        query = urllib.parse.urlencode({"api-version": request_item.api_version})
        assert request.full_url == (
            f"{adapters.ARM_ORIGIN}{request_item.resource_id}?{query}"
        )
        assert request.method == "GET"
        assert request.data is None
        assert timeout == adapters.REQUEST_TIMEOUT_SECONDS
        assert _headers(request) == {
            "accept": "application/json",
            "accept-encoding": "identity",
            "authorization": f"Bearer {TOKEN}",
        }
        assert response._is_authenticated_for(plan, request_item)
        assert response.status == 200
        assert response.body_sha256 == hashlib.sha256(response._body).hexdigest()

    with pytest.raises(
        provider.SanitizerBootstrapJobLiveProviderFailure,
        match="^bootstrap_job_live_provider.already_used$",
    ):
        live.read_all()
    assert len(identity_opener.calls) == 1
    assert len(arm_opener.calls) == 4


@pytest.mark.parametrize("phase", ("initial_absence", "final_absence"))
def test_absence_plans_accept_only_exact_404s_without_retaining_error_bodies(
    phase: str,
) -> None:
    plan = _plan(phase)
    errors = []
    for request_item in plan._requests:
        query = urllib.parse.urlencode({"api-version": request_item.api_version})
        url = f"{adapters.ARM_ORIGIN}{request_item.resource_id}?{query}"
        errors.append(_http_error(url, 404))
    live, actual_plan, identity_opener, arm_opener = _provider(
        phase=phase, arm_responses=errors
    )

    responses = live.read_all()

    assert len(identity_opener.calls) == 1
    assert len(arm_opener.calls) == len(plan._requests)
    assert [item.status for item in responses] == [404] * len(responses)
    assert all(item._body == b"" for item in responses)
    assert all(
        item._is_authenticated_for(actual_plan, request_item)
        for item, request_item in zip(responses, actual_plan._requests, strict=True)
    )


def test_transport_requires_exact_plan_identity_and_configuration() -> None:
    plan = _plan()
    identity_opener = FakeOpener([FakeResponse(_token_body(client_id=WRONG_CLIENT_ID))])
    with pytest.raises(
        provider.SanitizerBootstrapJobLiveProviderFailure,
        match="^bootstrap_job_live_provider.transport_invalid$",
    ):
        provider.AzureBootstrapJobLiveProvider._from_reviewed_plan(
            plan=plan,
            token_source=_token_source(identity_opener, client_id=WRONG_CLIENT_ID),
            arm_opener=FakeOpener([]),
            deadline_factory=_deadline,
        )
    with pytest.raises(
        provider.SanitizerBootstrapJobLiveProviderFailure,
        match="^bootstrap_job_live_provider.transport_invalid$",
    ):
        provider.AzureBootstrapJobLiveProvider()


@pytest.mark.parametrize(
    "response,code",
    (
        (FakeResponse(b"{}", status=201), "provider_refused"),
        (FakeResponse(b"not-json"), "response_invalid"),
        (
            FakeResponse(
                b"{}",
                headers={"Content-Type": "text/plain", "Content-Length": "2"},
            ),
            "response_invalid",
        ),
        (
            FakeResponse(
                b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(adapters.MAXIMUM_PROVIDER_BYTES + 1),
                },
            ),
            "response_too_large",
        ),
        (RuntimeError("provider secret"), "provider_unavailable"),
    ),
)
def test_provider_failures_are_bounded_and_content_free(
    response: Any, code: str
) -> None:
    live, _plan_value, _identity, _arm = _provider(arm_responses=[response])
    with pytest.raises(
        provider.SanitizerBootstrapJobLiveProviderFailure,
        match=rf"^bootstrap_job_live_provider\.{code}$",
    ) as caught:
        live.read_all()
    assert "secret" not in str(caught.value)


def test_status_or_url_mismatch_refuses_without_retry() -> None:
    plan = _plan()
    first = plan._requests[0]
    query = urllib.parse.urlencode({"api-version": first.api_version})
    expected_url = f"{adapters.ARM_ORIGIN}{first.resource_id}?{query}"
    for response, code in (
        (_http_error(expected_url, 404), "provider_refused"),
        (
            FakeResponse(b"{}", url="https://example.invalid/redirect"),
            "redirect_refused",
        ),
    ):
        live, _plan_value, _identity, arm = _provider(arm_responses=[response])
        with pytest.raises(
            provider.SanitizerBootstrapJobLiveProviderFailure,
            match=rf"^bootstrap_job_live_provider\.{code}$",
        ):
            live.read_all()
        assert len(arm.calls) == 1


def test_invalid_clock_and_elapsed_deadline_refuse_before_provider_call() -> None:
    for clock, monotonic, code in (
        (lambda: datetime(2033, 5, 18), lambda: 1.0, "clock_invalid"),
        (lambda: NOW, iter((1.0, 200.0)).__next__, "deadline"),
    ):
        live, _plan_value, identity, arm = _provider(clock=clock, monotonic=monotonic)
        with pytest.raises(
            provider.SanitizerBootstrapJobLiveProviderFailure,
            match=rf"^bootstrap_job_live_provider\.{code}$",
        ):
            live.read_all()
        assert len(arm.calls) == 0
        assert len(identity.calls) in {0, 1}


def test_response_is_sealed_tamper_evident_and_nonserializable() -> None:
    live, plan, _identity, _arm = _provider()
    response = live.read_all()[0]
    request_item = plan._requests[0]

    with pytest.raises(provider.SanitizerBootstrapJobLiveProviderFailure):
        provider.AuthenticatedBootstrapJobArmRead()
    with pytest.raises(provider.SanitizerBootstrapJobLiveProviderFailure):
        copy.copy(response)
    with pytest.raises(provider.SanitizerBootstrapJobLiveProviderFailure):
        pickle.dumps(response)

    object.__setattr__(response, "status", 404)
    assert not response._is_authenticated_for(plan, request_item)


def test_module_has_no_mutation_database_process_or_public_runtime_surface() -> None:
    source = Path(provider.__file__).read_text()
    assert 'method="GET"' in source
    for forbidden in (
        'method="POST"',
        'method="PUT"',
        'method="PATCH"',
        'method="DELETE"',
        "subprocess",
        "psycopg",
        "asyncpg",
        "socket",
    ):
        assert forbidden not in source
    assert (
        list(inspect.signature(provider.build_bootstrap_job_live_provider).parameters)
        == []
    )
