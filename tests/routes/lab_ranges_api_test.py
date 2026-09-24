import pytest
from fastapi import status
from fastapi.testclient import TestClient

import app.services.reference_labs_gate as lab_gate
from app.server import api_app

from app.models.doctor import Doctor
from app.models.lab_panel import LabPanel, LabPanelSource, LabPanelStatus
from app.models.lab_result import LabResult

from tests.factories import PatientFactory
from tests.routes.utils import get_clerk_dev_user


@pytest.fixture(autouse=True)
def _enable_labs_for_existing_feature_tests(monkeypatch):
    monkeypatch.setattr(lab_gate, "REFERENCE_LABS_ENABLED", True)


def _authed_doctor() -> Doctor:
    _, _, clerk_user = get_clerk_dev_user()
    return Doctor.find_or_create_by(clerk_id=clerk_user.id)


def _result(doctor, patient, biomarker, value, unit, collected=None):
    panel = LabPanel(
        doctor_id=doctor.id,
        patient_id=patient.id,
        source=LabPanelSource.manual,
        status=LabPanelStatus.completed,
        collected_on=collected,
    ).save()
    LabResult(
        lab_panel_id=panel.id,
        doctor_id=doctor.id,
        patient_id=patient.id,
        biomarker=biomarker,
        raw_name=biomarker,
        value=value,
        unit=unit,
    ).save()
    return panel


def test_evaluation_classifies_against_default_ranges(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    # vitamin D 35 → within standard (30-100) but below optimal (40-60)
    _result(doctor, patient, "vitamin_d_25oh", 35, "ng/mL")
    # TSH 6.0 → outside standard (0.4-4.0)
    _result(doctor, patient, "tsh", 6.0, "mIU/L")

    resp = authenticated_client.get(
        api_app.url_path_for("lab_evaluations", patient_id=str(patient.id))
    )
    assert resp.status_code == status.HTTP_200_OK
    by_marker = {e["biomarker"]: e for e in resp.json()["entries"]}
    assert by_marker["vitamin_d_25oh"]["classification"] == "within_standard"
    assert by_marker["vitamin_d_25oh"]["optimal_low"] == 40
    assert by_marker["tsh"]["classification"] == "outside_standard"


def test_evaluation_uses_latest_value_per_biomarker(authenticated_client: TestClient):
    from datetime import date

    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    _result(doctor, patient, "tsh", 6.0, "mIU/L", collected=date(2026, 1, 1))
    _result(doctor, patient, "tsh", 1.5, "mIU/L", collected=date(2026, 6, 1))

    resp = authenticated_client.get(
        api_app.url_path_for("lab_evaluations", patient_id=str(patient.id))
    )
    entries = resp.json()["entries"]
    tsh = [e for e in entries if e["biomarker"] == "tsh"]
    assert len(tsh) == 1
    assert tsh[0]["value"] == 1.5  # most recent panel
    assert tsh[0]["classification"] == "optimal"


def test_provider_override_changes_classification(authenticated_client: TestClient):
    doctor = _authed_doctor()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    _result(doctor, patient, "vitamin_d_25oh", 35, "ng/mL")

    # tighten optimal so 35 is now optimal
    resp = authenticated_client.put(
        api_app.url_path_for("lab_range_upsert", biomarker="vitamin_d_25oh"),
        json={
            "unit": "ng/mL",
            "standard_low": 30,
            "standard_high": 100,
            "optimal_low": 30,
            "optimal_high": 60,
            "source_url": "https://lamkinclinic.com/lab-reference-library/",
        },
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["is_override"] is True

    resp = authenticated_client.get(
        api_app.url_path_for("lab_evaluations", patient_id=str(patient.id))
    )
    vd = next(e for e in resp.json()["entries"] if e["biomarker"] == "vitamin_d_25oh")
    assert vd["classification"] == "optimal"
    assert vd["source_url"] == "https://lamkinclinic.com/lab-reference-library/"


def test_range_list_and_reset(authenticated_client: TestClient):
    resp = authenticated_client.get(api_app.url_path_for("lab_range_list"))
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert any(r["biomarker"] == "tsh" for r in body)
    assert all(r["is_override"] is False for r in body)  # defaults only

    # override then reset
    authenticated_client.put(
        api_app.url_path_for("lab_range_upsert", biomarker="tsh"),
        json={
            "standard_low": 0.5,
            "standard_high": 3.0,
            "optimal_low": 1.0,
            "optimal_high": 2.0,
        },
    )
    resp = authenticated_client.get(api_app.url_path_for("lab_range_list"))
    tsh = next(r for r in resp.json() if r["biomarker"] == "tsh")
    assert tsh["is_override"] is True
    assert tsh["standard_high"] == 3.0

    resp = authenticated_client.delete(
        api_app.url_path_for("lab_range_reset", biomarker="tsh")
    )
    assert resp.status_code == status.HTTP_204_NO_CONTENT

    resp = authenticated_client.get(api_app.url_path_for("lab_range_list"))
    tsh = next(r for r in resp.json() if r["biomarker"] == "tsh")
    assert tsh["is_override"] is False
    assert tsh["standard_high"] == 4.0  # back to default


def test_upsert_unknown_biomarker_is_404(authenticated_client: TestClient):
    _authed_doctor()
    resp = authenticated_client.put(
        api_app.url_path_for("lab_range_upsert", biomarker="not_a_marker"),
        json={"standard_low": 1},
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_rejects_transposed_bounds(authenticated_client: TestClient):
    """A low above its high makes every value read as out of range.

    classify() flags a value that is below `low` OR above `high`, so inverted
    bounds satisfy both tests for every value: every lab that provider views
    would be marked abnormal, with nothing anywhere to explain why.
    """

    resp = authenticated_client.put(
        api_app.url_path_for("lab_range_upsert", biomarker="vitamin_d_25oh"),
        json={
            "unit": "ng/mL",
            "standard_low": 100,
            "standard_high": 30,
            "optimal_low": 40,
            "optimal_high": 60,
        },
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_rejects_transposed_optimal_bounds(authenticated_client: TestClient):
    resp = authenticated_client.put(
        api_app.url_path_for("lab_range_upsert", biomarker="vitamin_d_25oh"),
        json={
            "unit": "ng/mL",
            "standard_low": 30,
            "standard_high": 100,
            "optimal_low": 60,
            "optimal_high": 40,
        },
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_rejects_negative_bounds(authenticated_client: TestClient):
    resp = authenticated_client.put(
        api_app.url_path_for("lab_range_upsert", biomarker="vitamin_d_25oh"),
        json={"unit": "ng/mL", "standard_low": -5, "standard_high": 100},
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_allows_one_sided_bounds(authenticated_client: TestClient):
    "an unbounded side is legitimate — 'lower is better, target under 5.4'"

    resp = authenticated_client.put(
        api_app.url_path_for("lab_range_upsert", biomarker="hba1c"),
        json={
            "unit": "%",
            "standard_low": None,
            "standard_high": 5.7,
            "optimal_low": None,
            "optimal_high": 5.4,
        },
    )
    assert resp.status_code == status.HTTP_200_OK
