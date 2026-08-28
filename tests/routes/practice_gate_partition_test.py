from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, status
from fastapi.routing import APIRoute
from starlette.requests import Request

from app.routes.dependencies import practice_gate
from app.routes.internal import internal_api_app
from app.services.doctor_access import (
    DoctorAccessDecision,
    DoctorAccessDenialReason,
)
from app.services.public_entitlements import CAPABILITY_KEYS

from app.models.doctor import Doctor


def _request(route_name: str, *, path: str = "/internal/v1/protected") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 443),
        "route": SimpleNamespace(name=route_name),
    }
    request = Request(scope)
    request.state.doctor = cast(Doctor, object())
    return request


def _allowed() -> DoctorAccessDecision:
    return DoctorAccessDecision(
        allowed=True,
        authorization_basis="organization_roster",
        denial_reason=None,
        baa_in_force=True,
        entitlement_in_force=True,
    )


def _denied(reason: DoctorAccessDenialReason) -> DoctorAccessDecision:
    return DoctorAccessDecision(
        allowed=False,
        authorization_basis=None,
        denial_reason=reason,
        baa_in_force=False,
        entitlement_in_force=False,
    )


def test_every_authenticated_internal_route_has_one_current_gate_partition() -> None:
    """Keep the gate classification exhaustive as the internal router evolves."""

    routes = [route for route in internal_api_app.routes if isinstance(route, APIRoute)]
    route_names = [route.name for route in routes]
    assert len(route_names) == len(set(route_names)), (
        "route names must remain unique because the gate dispatches on them"
    )

    names = set(route_names)
    billing_special = (
        practice_gate.ENTITLEMENT_EXEMPT_ROUTE_NAMES
        | practice_gate.CREDENTIAL_EXEMPT_ROUTE_NAMES
    )
    admin = {
        route.name
        for route in routes
        if route.path.startswith(practice_gate.ADMIN_PATH_PREFIX)
    }
    configured = (
        practice_gate.EXEMPT_ROUTE_NAMES
        | billing_special
        | set(practice_gate.CAPABILITY_ROUTE_NAMES)
    )

    assert configured <= names, (
        f"stale gate classifications: {sorted(configured - names)}"
    )
    assert set(practice_gate.CAPABILITY_ROUTE_NAMES.values()) <= set(CAPABILITY_KEYS)

    partitions = {
        "capability": set(practice_gate.CAPABILITY_ROUTE_NAMES),
        "onboarding_or_control_plane": practice_gate.EXEMPT_ROUTE_NAMES,
        "billing_recovery": billing_special,
        "internal_admin": admin,
    }
    for route_name in sorted(names):
        categories = [
            category
            for category, members in partitions.items()
            if route_name in members
        ]
        assert len(categories) == 1, (
            f"{route_name!r} must have exactly one gate classification; "
            f"found {categories or 'none'}"
        )


@pytest.mark.parametrize(
    ("route_name", "capability"),
    sorted(practice_gate.CAPABILITY_ROUTE_NAMES.items()),
)
def test_every_capability_route_fails_closed_when_rollout_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
    capability: str,
) -> None:
    request = _request(route_name)
    doctor = request.state.doctor
    observed: list[str | None] = []

    def resolve(
        candidate: Doctor,
        *,
        require_entitlement: bool,
        require_credential: bool,
    ) -> DoctorAccessDecision:
        assert candidate is doctor
        assert require_entitlement is True
        assert require_credential is True
        return _allowed()

    def rollout(candidate: Doctor, key: str | None) -> bool:
        assert candidate is doctor
        observed.append(key)
        return False

    monkeypatch.setattr(practice_gate, "resolve_doctor_access", resolve)
    monkeypatch.setattr(practice_gate, "doctor_capability_rollout_in_force", rollout)

    with pytest.raises(HTTPException) as exc_info:
        practice_gate.require_practice_in_force(request)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == (
        "This capability is not enabled for the public pilot."
    )
    assert observed == [capability]


_MEMBERSHIP_DENIALS: tuple[DoctorAccessDenialReason, ...] = (
    "doctor_disabled",
    "practice_membership_required",
    "practice_membership_revoked",
    "practice_membership_inactive",
    "practice_not_found",
    "practice_access_not_authorized",
)


@pytest.mark.parametrize("route_name", sorted(practice_gate.CAPABILITY_ROUTE_NAMES))
@pytest.mark.parametrize("denial_reason", _MEMBERSHIP_DENIALS)
def test_every_capability_route_rejects_disabled_or_wrong_practice_access(
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
    denial_reason: DoctorAccessDenialReason,
) -> None:
    def resolve(
        _candidate: Doctor,
        *,
        require_entitlement: bool,
        require_credential: bool,
    ) -> DoctorAccessDecision:
        assert require_entitlement is True
        assert require_credential is True
        return _denied(denial_reason)

    def unexpected_rollout(_candidate: Doctor, _key: str | None) -> bool:
        raise AssertionError("a denied common access decision must stop at the gate")

    monkeypatch.setattr(practice_gate, "resolve_doctor_access", resolve)
    monkeypatch.setattr(
        practice_gate,
        "doctor_capability_rollout_in_force",
        unexpected_rollout,
    )

    with pytest.raises(HTTPException) as exc_info:
        practice_gate.require_practice_in_force(_request(route_name))

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.parametrize(
    "route_name",
    sorted(
        practice_gate.ENTITLEMENT_EXEMPT_ROUTE_NAMES
        | practice_gate.CREDENTIAL_EXEMPT_ROUTE_NAMES
    ),
)
def test_billing_recovery_routes_skip_only_the_declared_common_gates(
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
) -> None:
    observed: list[tuple[bool, bool]] = []

    def resolve(
        _candidate: Doctor,
        *,
        require_entitlement: bool,
        require_credential: bool,
    ) -> DoctorAccessDecision:
        observed.append((require_entitlement, require_credential))
        return _allowed()

    def unexpected_rollout(_candidate: Doctor, _key: str | None) -> bool:
        raise AssertionError("billing recovery must not enter a clinical rollout")

    monkeypatch.setattr(practice_gate, "resolve_doctor_access", resolve)
    monkeypatch.setattr(
        practice_gate,
        "doctor_capability_rollout_in_force",
        unexpected_rollout,
    )

    practice_gate.require_practice_in_force(_request(route_name))

    assert observed == [
        (
            route_name not in practice_gate.ENTITLEMENT_EXEMPT_ROUTE_NAMES,
            route_name not in practice_gate.CREDENTIAL_EXEMPT_ROUTE_NAMES,
        )
    ]


@pytest.mark.parametrize("route_name", sorted(practice_gate.EXEMPT_ROUTE_NAMES))
def test_declared_onboarding_and_control_plane_routes_do_not_enter_access_gate(
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError(
            "an explicitly exempt route must return before access lookup"
        )

    monkeypatch.setattr(practice_gate, "resolve_doctor_access", unexpected)
    practice_gate.require_practice_in_force(_request(route_name))


_ADMIN_ROUTES = tuple(
    (route.name, route.path)
    for route in internal_api_app.routes
    if isinstance(route, APIRoute)
    and route.path.startswith(practice_gate.ADMIN_PATH_PREFIX)
)


@pytest.mark.parametrize(("route_name", "path"), _ADMIN_ROUTES)
def test_internal_admin_routes_rely_on_their_independent_admin_gate(
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
    path: str,
) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("the independent require_admin gate owns admin routes")

    monkeypatch.setattr(practice_gate, "resolve_doctor_access", unexpected)
    practice_gate.require_practice_in_force(_request(route_name, path=path))


def test_unclassified_public_capability_route_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str | None] = []
    monkeypatch.setattr(
        practice_gate,
        "resolve_doctor_access",
        lambda *_a, **_k: _allowed(),
    )
    monkeypatch.setattr(
        practice_gate,
        "doctor_capability_rollout_in_force",
        lambda _doctor, capability: observed.append(capability) or False,
    )

    with pytest.raises(HTTPException) as exc_info:
        practice_gate.require_practice_in_force(
            _request("future_unclassified_clinical_route")
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert observed == [None]
