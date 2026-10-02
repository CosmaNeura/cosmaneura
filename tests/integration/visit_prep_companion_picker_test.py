"""Signed owner-only preparation selection and package opening; invented data."""

from contextlib import contextmanager
from dataclasses import replace
from time import monotonic
from uuid import uuid4

import pytest

from app.configuration import service_auth
from app.routes import companion_visit_prep as routes
from app.services import visit_prep_companion_list as service
from app.services.companion_records import RecordBudget, RecordUnavailable
from app.services.visit_prep_companion import VisitPrepCompanionDenied
from app.services.visit_prep_packages import list_visit_preps
from app.services.visit_preps import RecipientInput, create_visit_prep

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep import VisitPrep
from sqlmodel import select

from tests.authority_kernel.visit_prep_companion_auth_test import (
    signed as signed_identity,
)
from tests.integration.visit_prep_companion_http_test import _client
from tests.integration.visit_prep_companion_transaction_test import _case
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_packages_test import _scope

signed = signed_identity


def test_pagination_keeps_owner_selection_stable_and_excludes_the_cursor(
    monkeypatch, signed
):
    case, client, headers = _client(monkeypatch, signed)
    with get_session() as session:
        questions = session.get(VisitPrep, case.fixture.prep_id).question_set_version_id
    second = create_visit_prep(
        **_scope(case.fixture, package=False),
        idempotency_key=str(uuid4()),
        recipient=RecipientInput(
            first_name="Invented Rowan", email="rowan@example.com"
        ),
        physician_display_name="Dr. Synthetic",
        scheduled_at=case.fixture.scheduled_at,
        timezone_name="UTC",
        question_set_version_id=questions,
    )
    request = _request(case)
    first = client.post(
        service.PATH, headers=headers, json=request.model_dump(mode="json")
    )
    assert first.status_code == 200, first.text
    assert first.json()["preparations"][0]["preparation_ref"] == str(
        second.visit_prep.id
    )
    cursor = first.json()["next_before_ref"]
    assert cursor == str(second.visit_prep.id)
    following = client.post(
        service.PATH,
        headers=headers,
        json=_request(case, before_ref=cursor).model_dump(mode="json"),
    )
    assert following.status_code == 200, following.text
    assert [p["preparation_ref"] for p in following.json()["preparations"]] == [
        str(case.fixture.prep_id)
    ]
    assert following.json()["next_before_ref"] is None


def _request(case, **changes):
    data = case.request.model_dump(mode="json")
    data.pop("preparation_ref")
    return service.VisitPrepCompanionListRequest.model_validate(
        {
            **data,
            "action": "visit-prep.list",
            "limit": 1,
            "before_ref": None,
            **changes,
        }
    )


def _audits(case):
    with get_session() as session:
        return session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(case.fixture.owner.id),
                AuthorityAuditEvent.event_type.in_(
                    ["visit_prep.companion_list", "visit_prep.list_read"]
                ),
            )
        ).all()


def test_signed_picker_lists_current_owner_and_opens_its_exact_package(
    monkeypatch, signed
):
    case, client, headers = _client(monkeypatch, signed, ready=True)
    with monkeypatch.context() as isolated:
        other = _prepared(isolated, "other-picker" + uuid4().hex[:6])
    expected = list_visit_preps(**_scope(case.fixture), limit=1)
    data = _request(case)
    before = len(_audits(case))
    response = client.post(
        service.PATH, headers=headers, json=data.model_dump(mode="json")
    )
    assert response.status_code == 200, response.text
    page = response.json()
    assert [p["preparation_ref"] for p in page["preparations"]] == [
        str(p.id) for p in expected.preparations
    ]
    assert str(other.prep_id) not in response.text
    assert page["preparations"][0]["recipient_first_name"] == "José"
    assert page["preparations"][0]["recipient_role"] == "patient"
    assert page["preparations"][0]["chart_linked"] is False
    assert page["preparations"][0]["available_to_open"] is True
    assert not {"email", "phone", "answer", "brief", "patient_id"}.intersection(
        page["preparations"][0]
    )
    assert "invented health story" not in response.text
    assert response.headers["referrer-policy"] == "no-referrer"
    assert len(_audits(case)) == before + 2
    selected = case.request.model_copy(
        update={"preparation_ref": page["preparations"][0]["preparation_ref"]}
    )
    package = client.post(
        routes.PATH, headers=headers, json=selected.model_dump(mode="json")
    )
    assert package.status_code == 200
    assert (
        package.json()["package"]["sources"]["package_id"]
        == page["preparations"][0]["preparation_ref"]
    )


@pytest.mark.parametrize(
    "failure", ["workload", "physician", "connection", "cursor", "extra", "limit"]
)
def test_picker_rejects_untrusted_scope_without_confirming_patient_data(
    monkeypatch, signed, failure
):
    case, client, headers = _client(monkeypatch, signed)
    data = _request(case).model_dump(mode="json")
    if failure in {"workload", "physician"}:
        headers[
            "Authorization"
            if failure == "workload"
            else "X-Cosmaneura-Provider-Authorization"
        ] = "Bearer invented.invalid"
    elif failure == "connection":
        data["connection_sha256"] = "f" * 64
    elif failure == "cursor":
        with monkeypatch.context() as isolated:
            other = _prepared(isolated, "wrong-cursor" + uuid4().hex[:6])
        data["before_ref"] = str(other.prep_id)
    elif failure == "extra":
        data["preparation_ref"] = case.request.preparation_ref
    else:
        data["limit"] = 21
    response = client.post(service.PATH, headers=headers, json=data)
    assert response.status_code in {401, 403, 422}
    assert response.json() == {"detail": "Visit Prep is unavailable."}
    assert not _audits(case)


@pytest.mark.parametrize("failure", ["flag", "credential", "serialize", "commit"])
def test_private_selection_is_discarded_on_late_authority_or_commit_failure(
    monkeypatch, failure
):
    case = _case(monkeypatch)
    request = _request(case)
    if failure == "commit":
        original = service.global_session

        @contextmanager
        def unavailable():
            with original() as session:
                monkeypatch.setattr(
                    session,
                    "commit",
                    lambda: (_ for _ in ()).throw(
                        RuntimeError("invented commit failure")
                    ),
                )
                yield session

        monkeypatch.setattr(service, "global_session", unavailable)

    def serialize(result, at):
        if failure == "flag":
            monkeypatch.setattr(
                service_auth,
                "COMPANION_VISIT_PREP_AUTH_SETTINGS",
                replace(case.policy, enabled=False),
            )
        if failure == "credential":
            object.__setattr__(case.physician.window, "expires_at", 1)
        if failure == "serialize":
            raise ValueError("invented private words")
        return routes.serialize_selections(request, result, at)

    with pytest.raises(
        (RecordUnavailable, VisitPrepCompanionDenied, ValueError, RuntimeError)
    ):
        service.read_visit_prep_companion_list(
            workload=case.workload,
            physician=case.physician,
            request=request,
            deadline=RecordBudget(monotonic() + 15),
            serialize=serialize,
        )
    assert not _audits(case)
