from __future__ import annotations

import inspect
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import (
    observe_staging_controller_receipt_v2_isolation_inventory as isolation,
)
from scripts import staging_restore_runtime_adapters as adapters
from scripts import validate_staging_controller_receipt_v2 as content
from scripts import validate_staging_restore_runtime_binding as runtime_binding

from tests.infrastructure import (
    test_staging_controller_receipt_v2_arm_observation as arm_helpers,
)
from tests.infrastructure import (
    test_staging_controller_receipt_v2_database_observation as database_helpers,
)
from tests.infrastructure import test_staging_runtime_record_private_readers as wire

TOKEN = "isolation-inventory-token"
Failure = isolation.ControllerReceiptV2IsolationInventoryFailure
SECRET_MARKERS = (TOKEN, "secret provider body", "Bearer")


@contextmanager
def _no_deadline(_seconds: int) -> Iterator[None]:
    yield


class Deadlines:
    def __init__(self, fail_on: int = 0) -> None:
        self.fail_on = fail_on
        self.calls: list[int] = []

    @contextmanager
    def __call__(self, seconds: int) -> Iterator[None]:
        self.calls.append(seconds)
        call = len(self.calls)
        yield
        if call == self.fail_on:
            raise TimeoutError("secret provider body")


def _assert_sanitized(error: BaseException) -> None:
    assert all(marker not in repr(error) for marker in SECRET_MARKERS)
    assert error.__cause__ is None
    assert error.__context__ is None


def _database_observation(monkeypatch: pytest.MonkeyPatch) -> Any:
    case = database_helpers._case(monkeypatch)
    return database_helpers._observe(case)


def _resource(item: isolation._KnownResource) -> dict[str, Any]:
    return {
        "id": item.resource_id,
        "name": isolation._hierarchical_name(item.resource_id),
        "type": item.resource_type,
        "location": item.location,
    }


def _extra_resource(
    plan: isolation._Plan,
    resource_type: str,
    name: str = "extra",
) -> dict[str, Any]:
    resource_id = f"{plan.target_resource_group_id}/providers/{resource_type}/{name}"
    return {
        "id": resource_id,
        "name": name,
        "type": resource_type,
        "location": plan.target_location,
    }


def _case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mutate: Any = None,
    response_options: dict[str, Any] | None = None,
    response_index: int = 0,
    clock: Any = None,
    monotonic: Any = None,
    deadline_factory: Any = _no_deadline,
) -> dict[str, Any]:
    database_observation = _database_observation(monkeypatch)
    policy = isolation.load_controller_receipt_v2_isolation_inventory_policy()
    plan = isolation._derive_plan(
        database_observation, policy=policy, now=arm_helpers.NOW
    )
    collections = {
        "firewall_rules": {"value": []},
        "vnet_peerings": {"value": []},
        "public_ip_addresses": {"value": []},
        "resource_group_inventory": {
            "value": [_resource(item) for item in plan.known_resources]
        },
    }
    if mutate is not None:
        mutate(collections, plan)
    runtime = runtime_binding._parse_policy(
        database_observation._production_authority._runtime_policy
    )
    identity = wire.Opener(
        [wire.Response(wire._token(runtime, adapters.ARM_RESOURCE, TOKEN))],
        name="identity",
    )
    responses = []
    for index, request in enumerate(plan.requests):
        options = response_options if index == response_index else {}
        responses.append(
            wire.Response(
                content._canonical(collections[request.key]), **(options or {})
            )
        )
    provider = wire.Opener(responses, name="arm")
    observer = isolation.ManagedIdentityControllerReceiptV2IsolationInventoryObserver._from_reviewed_configuration(
        clock=clock or (lambda: arm_helpers.NOW),
        identity_opener=identity,
        arm_opener=provider,
        epoch_clock=lambda: wire.EPOCH_NOW,
        monotonic=monotonic or (lambda: 10.0),
        deadline_factory=deadline_factory,
    )
    return locals()


def _observe(case: dict[str, Any]) -> Any:
    return case["observer"].observe(database_observation=case["database_observation"])


def test_exact_four_gets_mint_only_narrow_point_in_time_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    result = _observe(case)
    document = result.authority
    assert result._is_authenticated()
    assert len(case["identity"].calls) == 1
    identity_request, identity_timeout = case["identity"].calls[0]
    assert identity_timeout == 30
    assert parse_qs(urlsplit(identity_request.full_url).query) == {
        "api-version": ["2019-08-01"],
        "client_id": [case["plan"].provision_client_id],
        "resource": [adapters.ARM_RESOURCE],
    }
    assert len(case["provider"].calls) == 4
    for (request, timeout), expected in zip(
        case["provider"].calls, case["plan"].requests, strict=True
    ):
        split = urlsplit(request.full_url)
        assert request.method == "GET"
        assert timeout == 30
        assert split.path == expected.resource_id
        assert parse_qs(split.query) == {"api-version": [expected.api_version]}
    facts = document["facts"]
    assert facts["public_access"] == "Disabled"
    assert all(
        type(facts[name]) is int and facts[name] == 0
        for name in (
            "firewall_rule_count",
            "vnet_peering_count",
            "public_ip_count",
            "unknown_resource_count",
            "target_resource_group_storage_account_count",
            "target_resource_group_redis_resource_count",
        )
    )
    assert len(document["response_manifest"]) == 4
    assert document["claims"]["point_in_time_isolation_inventory_authenticated"]
    for claim in (
        "blob_or_redis_copy_absence_proven",
        "controller_receipt_exists",
        "create_only_behavior_proven",
        "database_mutation_authority",
        "execution_history_authenticated",
        "provider_facts_authenticated",
        "runtime_authority",
        "sanitizer_authority",
        "writer_identity_use_authenticated",
        "executable",
    ):
        assert document["claims"][claim] is False
    assert TOKEN not in repr(result)
    assert TOKEN.encode() not in result.authority_bytes


def test_public_surface_policy_and_activation_stay_dark() -> None:
    method = (
        isolation.ManagedIdentityControllerReceiptV2IsolationInventoryObserver.observe
    )
    assert set(inspect.signature(method).parameters) == {
        "self",
        "database_observation",
    }
    assert not inspect.signature(
        isolation.build_controller_receipt_v2_isolation_inventory_observer
    ).parameters
    policy = isolation.load_controller_receipt_v2_isolation_inventory_policy()
    assert policy["activation_ready"] is False
    assert policy["activation"]["isolation_inventory_observer_implemented"] is True
    assert all(
        value is False
        for name, value in policy["activation"].items()
        if name != "isolation_inventory_observer_implemented"
    )
    assert policy["transport"]["maximum_arm_get_requests"] == 4
    changed = deepcopy(policy)
    changed["transport"]["retry_permitted"] = True
    with pytest.raises(Failure, match="policy_invalid"):
        isolation.validate_controller_receipt_v2_isolation_inventory_policy(changed)
    with pytest.raises(Failure, match="policy_disabled"):
        isolation.build_controller_receipt_v2_isolation_inventory_observer()
    with pytest.raises(Failure, match="configuration_invalid"):
        isolation.ManagedIdentityControllerReceiptV2IsolationInventoryObserver()
    with pytest.raises(Failure, match="authority_unsealed"):
        isolation.ControllerReceiptV2IsolationInventoryAuthority()


def test_nested_resources_are_allowed_but_not_assumed_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def omit_nested(collections: dict[str, Any], plan: isolation._Plan) -> None:
        collections["resource_group_inventory"]["value"] = [
            _resource(item) for item in plan.known_resources if item.required
        ]

    result = _observe(_case(monkeypatch, mutate=omit_nested))
    assert result.authority["facts"]["unknown_resource_count"] == 0


@pytest.mark.parametrize(
    "collection", ("firewall_rules", "vnet_peerings", "public_ip_addresses")
)
def test_forbidden_network_inventory_fails_closed(
    monkeypatch: pytest.MonkeyPatch, collection: str
) -> None:
    def mutate(collections: dict[str, Any], _plan: isolation._Plan) -> None:
        collections[collection]["value"] = [{}]

    case = _case(monkeypatch, mutate=mutate)
    with pytest.raises(Failure, match="isolation_present"):
        _observe(case)


@pytest.mark.parametrize(
    "resource_type",
    (
        "Microsoft.Storage/storageAccounts",
        "microsoft.cache/REDIS",
        "Microsoft.Cache/redisEnterprise",
        "Microsoft.Compute/virtualMachines",
    ),
)
def test_forbidden_or_unknown_rg_resource_fails_closed(
    monkeypatch: pytest.MonkeyPatch, resource_type: str
) -> None:
    def mutate(collections: dict[str, Any], plan: isolation._Plan) -> None:
        collections["resource_group_inventory"]["value"].append(
            _extra_resource(plan, resource_type)
        )

    case = _case(monkeypatch, mutate=mutate)
    with pytest.raises(Failure, match="isolation_present"):
        _observe(case)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda items, _plan: items.pop(0),
        lambda items, _plan: items.append(dict(items[0])),
        lambda items, _plan: items.append(
            {
                **items[0],
                "id": str(items[0]["id"])
                .replace("/resourceGroups/", "/resourcegroups/")
                .replace("/providers/Microsoft", "/providers/microsoft"),
                "name": str(items[0]["name"]).lower(),
                "type": str(items[0]["type"]).lower(),
            }
        ),
        lambda items, _plan: items[0].update(type="Microsoft.Network/virtualNetworks"),
        lambda items, _plan: items[0].update(name="wrong"),
        lambda items, _plan: items[0].update(location="eastus"),
        lambda items, _plan: items[0].update(
            id="/subscriptions/00000000-0000-4000-8000-000000000099/resourceGroups/wrong/providers/Microsoft.DBforPostgreSQL/flexibleServers/wrong",
            name="wrong",
        ),
    ),
)
def test_scope_id_type_name_location_missing_and_collision_refuse(
    monkeypatch: pytest.MonkeyPatch, mutation: Any
) -> None:
    def mutate(collections: dict[str, Any], plan: isolation._Plan) -> None:
        mutation(collections["resource_group_inventory"]["value"], plan)

    case = _case(monkeypatch, mutate=mutate)
    with pytest.raises(Failure, match="inventory_invalid"):
        _observe(case)


@pytest.mark.parametrize(
    ("mutation", "failure"),
    (
        (
            lambda document: document.update(nextLink="https://secret.invalid"),
            "response_invalid",
        ),
        (lambda document: document.update(value={}), "response_invalid"),
        (lambda document: document.clear(), "response_invalid"),
    ),
)
def test_json_shape_and_pagination_refuse(
    monkeypatch: pytest.MonkeyPatch, mutation: Any, failure: str
) -> None:
    def mutate(collections: dict[str, Any], _plan: isolation._Plan) -> None:
        mutation(collections["firewall_rules"])

    case = _case(monkeypatch, mutate=mutate)
    with pytest.raises(Failure, match=failure):
        _observe(case)


def test_invalid_json_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(monkeypatch)
    case["provider"].responses[0] = wire.Response(b"{")
    with pytest.raises(Failure, match="response_invalid"):
        _observe(case)


@pytest.mark.parametrize(
    ("options", "failure"),
    (
        ({"status": 403}, "provider_refused"),
        ({"content_type": "text/plain"}, "provider_unavailable"),
        (
            {
                "content_length": False,
                "extra_headers": (("Content-Length", "9999999"),),
            },
            "provider_unavailable",
        ),
        (
            {"url": "https://management.azure.com/wrong?api-version=2024-08-01"},
            "provider_unavailable",
        ),
    ),
)
def test_http_status_framing_media_and_final_url_refuse(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, Any], failure: str
) -> None:
    case = _case(monkeypatch, response_options=options)
    with pytest.raises(Failure, match=failure):
        _observe(case)


def test_provider_order_is_normalized_before_inventory_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _observe(_case(monkeypatch)).authority["facts"]

    def reverse(collections: dict[str, Any], _plan: isolation._Plan) -> None:
        collections["resource_group_inventory"]["value"].reverse()

    second = _observe(_case(monkeypatch, mutate=reverse)).authority["facts"]
    assert (
        first["known_resource_inventory_sha256"]
        == second["known_resource_inventory_sha256"]
    )


def test_strict_integer_counts_reject_bool_and_float() -> None:
    facts = {
        "public_access": "Disabled",
        "known_resource_inventory_sha256": "sha256:" + "a" * 64,
        "firewall_rule_count": 0,
        "vnet_peering_count": 0,
        "public_ip_count": 0,
        "unknown_resource_count": 0,
        "target_resource_group_storage_account_count": 0,
        "target_resource_group_redis_resource_count": 0,
    }
    for value in (False, 0.0):
        changed = dict(facts, firewall_rule_count=value)
        with pytest.raises(Failure, match="inventory_invalid"):
            isolation._validate_fact_document(changed)
    for digest in ("sha256:abc", "sha256:" + "g" * 64):
        changed = dict(facts, known_resource_inventory_sha256=digest)
        with pytest.raises(Failure, match="inventory_invalid"):
            isolation._validate_fact_document(changed)


def test_stale_or_tampered_sealed_input_refuses_before_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(
        monkeypatch,
        clock=lambda: arm_helpers.NOW + timedelta(seconds=61),
    )
    with pytest.raises(Failure, match="authority_invalid"):
        _observe(case)
    assert case["identity"].calls == []

    case = _case(monkeypatch)
    object.__setattr__(case["database_observation"], "rehearsal_id", "tampered")
    with pytest.raises(Failure, match="authority_invalid"):
        _observe(case)
    assert case["identity"].calls == []


def test_one_shot_is_concurrency_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(monkeypatch)

    class ThreadTestTokenSource:
        def __init__(self) -> None:
            self.calls = 0

        def get_token(self, *, resource: str, timeout_seconds: int) -> str:
            assert (resource, timeout_seconds) == (adapters.ARM_RESOURCE, 30)
            self.calls += 1
            return TOKEN

    token_source = ThreadTestTokenSource()
    monkeypatch.setattr(
        adapters.ManagedIdentityTokenSource,
        "from_environment",
        classmethod(lambda _cls, **_kwargs: token_source),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_observe, case) for _ in range(2)]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except Failure as error:
            outcomes.append(error)
    assert sum(isinstance(value, Failure) for value in outcomes) == 1
    assert (
        sum(
            isinstance(value, isolation.ControllerReceiptV2IsolationInventoryAuthority)
            for value in outcomes
        )
        == 1
    )
    assert token_source.calls == 1
    assert case["identity"].calls == []
    assert len(case["provider"].calls) == 4


def test_deadline_and_clock_failures_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadlines = Deadlines(fail_on=1)
    case = _case(monkeypatch, deadline_factory=deadlines)
    with pytest.raises(Failure, match="provider_unavailable") as error:
        _observe(case)
    _assert_sanitized(error.value)
    assert len(case["provider"].calls) == 1

    values = iter((10.0, 10.0, 9.0))
    case = _case(monkeypatch, monotonic=lambda: next(values))
    with pytest.raises(Failure, match="clock_invalid") as error:
        _observe(case)
    _assert_sanitized(error.value)


def test_production_default_deadlines_do_not_nest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch, deadline_factory=adapters._wall_clock_deadline)
    assert _observe(case)._is_authenticated()


def test_failure_and_authority_seals_hide_provider_content_and_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)

    def fail() -> None:
        raise RuntimeError(f"{TOKEN} secret provider body Bearer")

    case["provider"].effects.append(fail)
    with pytest.raises(Failure, match="provider_unavailable") as error:
        _observe(case)
    _assert_sanitized(error.value)

    case = _case(monkeypatch)
    result = _observe(case)
    object.__setattr__(result, "public_access_authenticated", False)
    assert not result._is_authenticated()
    object.__setattr__(result, "public_access_authenticated", True)
    object.__setattr__(
        result._responses[0], "body", b'{"value":["secret provider body"]}'
    )
    assert not result._is_authenticated()
    with pytest.raises(Failure, match="authority_invalid"):
        _ = result.authority


def test_no_mutating_or_live_default_path() -> None:
    source = inspect.getsource(isolation)
    for token in ('method="POST"', 'method="PUT"', 'method="DELETE"', "subprocess"):
        assert token not in source
