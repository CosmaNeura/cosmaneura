"""Working dual-JWT endpoint against an owned synthetic PostgreSQL database."""

import json
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.configuration import service_auth
from app.routes import companion_visit_prep
from app.routes.visit_prep_synthesis import _synthesis_response
from app.services.visit_prep_synthesis_publication import get_visit_prep_synthesis

from tests.authority_kernel.visit_prep_companion_auth_test import (
    signed as signed_identity,
)
from tests.integration.visit_prep_companion_transaction_test import _audits, _case
from tests.services.visit_prep_packages_test import _scope
from tests.services.visit_prep_synthesis_artifact_test import _artifact

signed = signed_identity


def _client(monkeypatch, signed, *, ready=False):
    case = _case(monkeypatch)
    if ready:
        _artifact(case.fixture).save()
    workload = {
        **signed.workload,
        "iss": case.policy.issuer,
        "aud": case.policy.audience,
        "tid": case.policy.tenant_id,
        "azp": case.workload.client_id,
    }
    physician = {
        **signed.physician,
        "iss": case.physician.issuer,
        "sub": case.physician.subject,
        "sid": case.physician.session_id,
    }
    headers = {
        "Authorization": "Bearer " + signed.encode(workload),
        "X-Cosmaneura-Provider-Authorization": "Bearer " + signed.encode(physician),
        "X-Cosmaneura-Visit-Prep-Version": "1",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    app = FastAPI()
    companion_visit_prep.install(app)
    return case, TestClient(app), headers


@pytest.mark.parametrize("ready", [False, True])
def test_signed_http_request_delivers_exact_dashboard_package(
    monkeypatch, signed, ready
):
    case, client, headers = _client(monkeypatch, signed, ready=ready)
    dashboard = _synthesis_response(
        get_visit_prep_synthesis(
            **_scope(case.fixture), visit_prep_id=case.fixture.prep_id
        )
    ).model_dump(mode="json")
    before = len(_audits(case))
    response = client.post(
        companion_visit_prep.PATH,
        headers=headers,
        json=case.request.model_dump(mode="json"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["package"] == dashboard
    assert response.json()["request_nonce"] == str(case.request.request_nonce)
    assert response.json()["workspace_id"] == str(case.request.workspace_id)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "set-cookie" not in response.headers
    assert len(_audits(case)) == before + 2
    assert companion_visit_prep.PATH not in client.app.openapi()["paths"]


@pytest.mark.parametrize(
    "change,status",
    [
        ("workload", 401),
        ("physician", 401),
        ("version", 426),
        ("encoding", 422),
        ("selector", 403),
        ("extra", 422),
        ("duplicate", 422),
        ("disabled", 503),
        ("serializer", 503),
    ],
)
def test_unavailable_requests_return_no_package_or_audit(
    monkeypatch, signed, change, status
):
    case, client, headers = _client(monkeypatch, signed, ready=True)
    data = case.request.model_dump(mode="json")
    if change in {"workload", "physician"}:
        name = (
            "Authorization"
            if change == "workload"
            else "X-Cosmaneura-Provider-Authorization"
        )
        headers[name] = "Bearer invented-invalid-token"
    elif change == "version":
        headers["X-Cosmaneura-Visit-Prep-Version"] = "2"
    elif change == "encoding":
        headers["Accept-Encoding"] = "gzip"
    elif change == "selector":
        data["connection_sha256"] = "0" * 64
    elif change == "extra":
        data["patient_words"] = "invented-private-marker"
    elif change == "disabled":
        monkeypatch.setattr(
            service_auth,
            "COMPANION_VISIT_PREP_AUTH_SETTINGS",
            replace(case.policy, enabled=False),
        )
    elif change == "serializer":

        def fail(*args):
            raise ValueError("invented-private-marker")

        monkeypatch.setattr(companion_visit_prep, "serialize_package", fail)
    body = json.dumps(data)
    if change == "duplicate":
        body = body[:-1] + ',"contract_version":"1"}'
    response = client.post(companion_visit_prep.PATH, headers=headers, content=body)
    assert response.status_code == status
    assert response.json() == {"detail": "Visit Prep is unavailable."}
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert not _audits(case)
