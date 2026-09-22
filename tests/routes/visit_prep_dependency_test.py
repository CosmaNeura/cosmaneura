from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request
from typeid import TypeID

import app.routes.dependencies.visit_prep as dependency
from app.services.visit_prep_access import VisitPrepAccessDecision

from activemodel.types import TypeIDType


def _request(*, view_as_active: bool) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/internal/v1/visit-prep",
            "headers": [],
        }
    )
    request.state.doctor = SimpleNamespace(id=TypeID("doctor"))
    request.state.view_as = object() if view_as_active else None
    return request


def _decision(*, eligible: bool, can_manage: bool) -> VisitPrepAccessDecision:
    return VisitPrepAccessDecision(
        eligible=eligible,
        can_manage=can_manage,
        can_send_email=False,
        can_send_sms=False,
        doctor_id=cast(TypeIDType, TypeID("doctor")),
        practice_id=cast(TypeIDType, TypeID("practice")),
        practice_member_id=cast(TypeIDType, TypeID("practice_member")),
        denial_reason=None if eligible else "feature_disabled",
        view_as_active=not can_manage,
    )


def test_read_returns_404_when_visit_prep_is_not_eligible(monkeypatch):
    monkeypatch.setattr(
        dependency,
        "resolve_visit_prep_access",
        lambda _doctor, *, view_as_active: _decision(
            eligible=False,
            can_manage=False,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        dependency.require_visit_prep_read(_request(view_as_active=False))

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.detail == "Not found"


def test_write_returns_404_before_disclosing_management_state(monkeypatch):
    monkeypatch.setattr(
        dependency,
        "resolve_visit_prep_access",
        lambda _doctor, *, view_as_active: _decision(
            eligible=False,
            can_manage=False,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        dependency.require_visit_prep_write(_request(view_as_active=True))

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


def test_view_as_may_read_but_cannot_mutate(monkeypatch):
    seen: list[bool] = []

    def resolve(_doctor, *, view_as_active):
        seen.append(view_as_active)
        return _decision(eligible=True, can_manage=False)

    monkeypatch.setattr(dependency, "resolve_visit_prep_access", resolve)
    request = _request(view_as_active=True)

    assert dependency.require_visit_prep_read(request).eligible is True
    with pytest.raises(HTTPException) as exc_info:
        dependency.require_visit_prep_write(request)

    assert seen == [True, True]
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_eligible_physician_may_use_write_dependency(monkeypatch):
    decision = _decision(eligible=True, can_manage=True)
    monkeypatch.setattr(
        dependency,
        "resolve_visit_prep_access",
        lambda _doctor, *, view_as_active: decision,
    )

    assert (
        dependency.require_visit_prep_write(_request(view_as_active=False)) is decision
    )
