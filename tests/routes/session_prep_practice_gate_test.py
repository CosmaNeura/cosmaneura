from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from app.routes.dependencies import practice_gate
from app.routes.dependencies.session_prep_account import (
    SESSION_PREP_ACTIVE_ACCOUNT_ROUTE_METHODS,
)
from app.services.session_prep_provider_access import (
    SessionPrepProviderAccessDecision,
)

from app.models.doctor import Doctor


def _request(
    method: str,
    route_name: str,
    doctor: Doctor,
    *,
    view_as: object | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "path": "/internal/v1/test",
            "headers": [],
            "server": ("synthetic.invalid", 443),
            "route": SimpleNamespace(name=route_name),
            "state": {"doctor": doctor, "view_as": view_as},
        }
    )


def _doctor() -> Doctor:
    return Doctor(
        clerk_id="user_session_prep_practice_gate",
        email="session-prep-practice-gate@example.com",
        biography="",
    )


def _enable_session_prep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        practice_gate.session_prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SimpleNamespace(enabled=True),
    )


def test_exact_session_prep_route_methods_use_active_account_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_prep(monkeypatch)
    doctor = _doctor()
    checked: list[Doctor] = []

    def session_prep_access(current: Doctor) -> SessionPrepProviderAccessDecision:
        checked.append(current)
        return SessionPrepProviderAccessDecision(allowed=True, denial_reason=None)

    monkeypatch.setattr(
        practice_gate,
        "resolve_session_prep_provider_access",
        session_prep_access,
    )
    monkeypatch.setattr(
        practice_gate,
        "resolve_doctor_access",
        lambda *_args, **_kwargs: pytest.fail(
            "ordinary practice gate must not run for an active Session Prep account"
        ),
    )

    practice_gated_routes = {
        route_name: methods
        for route_name, methods in SESSION_PREP_ACTIVE_ACCOUNT_ROUTE_METHODS.items()
        if route_name not in practice_gate.EXEMPT_ROUTE_NAMES
    }
    for route_name, methods in practice_gated_routes.items():
        for method in methods:
            assert (
                practice_gate.require_practice_in_force(
                    _request(method, route_name, doctor)
                )
                is None
            )

    assert checked == [doctor] * len(practice_gated_routes)


@pytest.mark.parametrize(
    ("method", "route_name"),
    [
        ("POST", "note_detail"),
        ("GET", "correct_session_prep_source_note"),
        ("GET", "generate_signed_visit_recording_upload_url"),
        ("POST", "create_billing_checkout"),
        ("GET", "new_unreviewed_route"),
    ],
)
def test_unrelated_or_wrong_method_routes_keep_ordinary_practice_gate(
    method: str,
    route_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_session_prep(monkeypatch)
    doctor = _doctor()
    ordinary_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        practice_gate,
        "resolve_session_prep_provider_access",
        lambda *_args, **_kwargs: pytest.fail(
            "the active-account resolver is limited to exact route/method pairs"
        ),
    )

    def deny_ordinary(_doctor: Doctor, **kwargs: object) -> SimpleNamespace:
        ordinary_calls.append(kwargs)
        return SimpleNamespace(
            allowed=False,
            denial_reason="practice_membership_required",
        )

    monkeypatch.setattr(practice_gate, "resolve_doctor_access", deny_ordinary)

    with pytest.raises(HTTPException) as error:
        practice_gate.require_practice_in_force(_request(method, route_name, doctor))

    assert error.value.status_code == 403
    assert len(ordinary_calls) == 1


@pytest.mark.parametrize("failure", ["rollout_off", "account_stopped", "view_as"])
def test_active_account_exception_fails_closed(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doctor = _doctor()
    _enable_session_prep(monkeypatch)
    if failure == "rollout_off":
        monkeypatch.setattr(
            practice_gate.session_prep_configuration,
            "SESSION_PREP_PROVIDER_SETTINGS",
            SimpleNamespace(enabled=False),
        )
    monkeypatch.setattr(
        practice_gate,
        "resolve_session_prep_provider_access",
        lambda _doctor: SessionPrepProviderAccessDecision(
            allowed=failure != "account_stopped",
            denial_reason=(
                "scribe_cancelled_read_only" if failure == "account_stopped" else None
            ),
        ),
    )
    monkeypatch.setattr(
        practice_gate,
        "resolve_doctor_access",
        lambda *_args, **_kwargs: SimpleNamespace(
            allowed=False,
            denial_reason="practice_membership_required",
        ),
    )

    with pytest.raises(HTTPException) as error:
        practice_gate.require_practice_in_force(
            _request(
                "GET",
                "note_detail",
                doctor,
                view_as=object() if failure == "view_as" else None,
            )
        )

    assert error.value.status_code == 403
