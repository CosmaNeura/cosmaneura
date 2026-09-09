from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import pickle
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import (  # noqa: E402
    staging_sanitizer_bootstrap_job_live_provider as provider,
)

sys.path.pop(0)

CLIENT_ID = "11111111-1111-4111-8111-111111111111"
REGISTRY_ID = (
    f"{provider.SUBSCRIPTION_SCOPE}/resourceGroups/cosmaneura-staging-rehearsal-control/"
    "providers/Microsoft.ContainerRegistry/registries/cosmaneurastgrecovery"
)
ROLE_ASSIGNMENT_NAME = "33333333-3333-4333-8333-333333333333"


def _plan(phase: str = "present") -> provider.BootstrapJobLiveReadPlan:
    return provider._compile_dormant_read_plan(
        phase=phase,
        identity_client_id=CLIENT_ID,
        private_registry_resource_id=REGISTRY_ID,
        private_registry_acrpull_role_assignment_name=ROLE_ASSIGNMENT_NAME,
    )


def test_policy_is_hash_pinned_upstream_bound_and_fully_dormant() -> None:
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
    assert policy["status"]["dormant_request_plan_compiler_implemented"] is True
    assert policy["status"]["one_shot_get_transport_implemented"] is False
    assert policy["status"]["live_provider_calls_enabled"] is False
    assert policy["status"]["activation_ready"] is False
    assert (
        "isolated_transport_boundary_unimplemented"
        in policy["status"]["activation_blockers"]
    )
    assert all(value is False for value in policy["claims"].values())


def test_policy_mutations_and_every_public_activation_request_refuse() -> None:
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

    for activation in (
        lambda: provider.validate_bootstrap_job_live_provider_policy(
            policy, executable=True
        ),
        lambda: provider.load_bootstrap_job_live_provider_policy(executable=True),
        provider.build_bootstrap_job_live_provider,
    ):
        with pytest.raises(
            provider.SanitizerBootstrapJobLiveProviderFailure,
            match=f"^{provider.ACTIVATION_FAILURE}$",
        ):
            activation()


@pytest.mark.parametrize(
    "phase,count", (("initial_absence", 3), ("present", 4), ("final_absence", 3))
)
def test_dormant_plans_have_exact_phase_order_and_statuses(
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
    assert not any(
        hasattr(plan, name)
        for name in ("execute", "read", "read_all", "token", "token_source")
    )


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
        {
            "private_registry_resource_id": REGISTRY_ID.replace(
                "cosmaneura-staging-rehearsal-control", "."
            )
        },
        {
            "private_registry_resource_id": REGISTRY_ID.replace(
                "cosmaneura-staging-rehearsal-control", ".."
            )
        },
        {
            "private_registry_resource_id": REGISTRY_ID.replace(
                "cosmaneura-staging-rehearsal-control", "resource-group."
            )
        },
        {"private_registry_acrpull_role_assignment_name": "not-a-uuid"},
    ),
)
def test_plan_compiler_rejects_redirected_or_malformed_inputs(
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
        provider._compile_dormant_read_plan(**values)


def test_dormant_plan_is_nonserializable_and_tampering_invalidates_it() -> None:
    plan = _plan()
    with pytest.raises(provider.SanitizerBootstrapJobLiveProviderFailure):
        provider.BootstrapJobLiveReadPlan()
    with pytest.raises(provider.SanitizerBootstrapJobLiveProviderFailure):
        copy.copy(plan)
    with pytest.raises(provider.SanitizerBootstrapJobLiveProviderFailure):
        copy.deepcopy(plan)
    with pytest.raises(provider.SanitizerBootstrapJobLiveProviderFailure):
        pickle.dumps(plan)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(plan, "request_plan_sha256", "0" * 64)

    object.__setattr__(plan, "request_plan_sha256", "0" * 64)
    assert not plan._is_valid()


def test_module_exposes_no_token_arm_or_response_execution_route() -> None:
    source = Path(provider.__file__).read_text()
    assert (
        list(inspect.signature(provider.build_bootstrap_job_live_provider).parameters)
        == []
    )
    assert not hasattr(provider, "AzureBootstrapJobLiveProvider")
    assert not hasattr(provider, "AuthenticatedBootstrapJobArmRead")
    assert not hasattr(provider, "adapters")
    for forbidden in (
        "urllib.request",
        "get_token(",
        '"Authorization":',
        "_default_opener",
        "_wall_clock_deadline",
        "_HttpOpener",
        "_read_json_response",
    ):
        assert forbidden not in source


def test_module_has_no_mutation_database_process_or_network_surface() -> None:
    source = Path(provider.__file__).read_text()
    for forbidden in (
        'method="POST"',
        'method="PUT"',
        'method="PATCH"',
        'method="DELETE"',
        "subprocess",
        "psycopg",
        "asyncpg",
        "socket",
        "http.client",
        "import requests",
        "from requests",
    ):
        assert forbidden not in source
