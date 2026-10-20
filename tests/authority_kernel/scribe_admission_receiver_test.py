"""Bounded first-party admission endpoint; no network or database."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI

from app.routes.scribe_capture import get_admission_client_factory
from app.services import scribe_capture_admission as admission
from app.services.scribe_workflow import DisabledScribeWorkflowClient

from tests.authority_kernel.scribe_capture_receiver_test import client, headers

PATH = "/service/v1/agent/scribe/capture-admissions"


def request_body():
    return {
        "binding_ref": "scribe_capture_binding_01j00000000000000000000000",
        "workspace_id": str(uuid4()),
        "request_nonce": str(uuid4()),
        "attestation": {
            "modality": "in_person",
            "participant_regions": ["US-NY"],
            "method": "verbal_attestation",
            "all_parties_consented": True,
            "granted_at": datetime.now(UTC).isoformat(),
        },
    }


def request_headers():
    return {**headers(), "Idempotency-Key": uuid4().hex}


def test_disabled_facade_does_not_construct_workload_client():
    connection = client(enabled=False)
    assert isinstance(connection.app, FastAPI)
    connection.app.dependency_overrides[get_admission_client_factory] = lambda: (
        lambda: pytest.fail("client constructed")
    )
    response = connection.post(PATH, content="private_sentinel")
    assert response.status_code == 503
    assert "private_sentinel" not in response.text


@pytest.mark.parametrize(
    "mutation",
    [
        "identity",
        "false",
        "truthy",
        "method",
        "region",
        "extra",
        "key",
        "binding",
        "nested_identity",
    ],
)
def test_invalid_admission_input_is_rejected_before_service(monkeypatch, mutation):
    payload = request_body()
    request = request_headers()
    if mutation == "identity":
        payload["tenant_id"] = "private_sentinel"
    elif mutation == "false":
        payload["attestation"]["all_parties_consented"] = False
    elif mutation == "truthy":
        payload["attestation"]["all_parties_consented"] = 1
    elif mutation == "method":
        payload["attestation"]["method"] = "pre_visit_form"
    elif mutation == "region":
        payload["attestation"]["participant_regions"] = ["private_sentinel"]
    elif mutation == "extra":
        payload["attestation"]["evidence_object_id"] = str(uuid4())
    elif mutation == "key":
        request.pop("Idempotency-Key")
    elif mutation == "binding":
        payload["binding_ref"] = "private_sentinel"
    else:
        payload["attestation"]["patient_id"] = "private_sentinel"
    monkeypatch.setattr(
        admission, "admit_capture", lambda **_: pytest.fail("service reached")
    )
    response = client().post(PATH, headers=request, json=payload)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert "private_sentinel" not in response.text


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (admission.AdmissionConsentRequired, 409, "CAPTURE_CONSENT_REQUIRED"),
        (
            admission.AdmissionRecordingConsentRequired,
            409,
            "CAPTURE_RECORDING_CONSENT_REQUIRED",
        ),
        (admission.AdmissionConsentUnavailable, 409, "CAPTURE_CONSENT_UNAVAILABLE"),
        (admission.AdmissionConflict, 409, "CAPTURE_REQUEST_CONFLICT"),
        (admission.AdmissionInProgress, 202, "CAPTURE_IN_PROGRESS"),
        (admission.AdmissionUnconfirmed, 503, "CAPTURE_ADMISSION_UNCONFIRMED"),
    ],
)
def test_phase_outcomes_are_safe_and_explicit(monkeypatch, error, status, code):
    async def reject(**_):
        raise error("private_sentinel")

    monkeypatch.setattr(admission, "admit_capture", reject)
    response = client().post(PATH, headers=request_headers(), json=request_body())
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.headers["cache-control"] == "no-store"
    assert "private_sentinel" not in response.text


def test_admitted_receipt_is_bound_and_client_is_closed(monkeypatch):
    payload = request_body()
    seen = []
    closed = []

    class RecordingClient(DisabledScribeWorkflowClient):
        async def close(self):
            closed.append(True)

    connection = client()
    assert isinstance(connection.app, FastAPI)
    connection.app.dependency_overrides[get_admission_client_factory] = lambda: (
        RecordingClient
    )

    async def admit(**kwargs):
        seen.append(kwargs)
        return admission.AdmissionSnapshot(
            operation_ref="scribe_admission_01j00000000000000000000000",
            binding_ref=kwargs["binding_ref"],
            workspace_id=kwargs["workspace_id"],
            appointment_ref="appointment_01j00000000000000000000000",
            consent_record_id=uuid4(),
            session_id=uuid4(),
        )

    monkeypatch.setattr(admission, "admit_capture", admit)
    response = connection.post(PATH, headers=request_headers(), json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "admitted"
    assert response.json()["request_nonce"] == payload["request_nonce"]
    assert response.json()["binding_ref"] == payload["binding_ref"]
    assert response.headers["x-cosmaneura-capture-version"] == "1"
    assert closed == [True]
    assert (
        seen[0]["caller"].workload_client_id == "11111111-1111-4111-8111-111111111111"
    )
    assert "mctx_" not in response.text
