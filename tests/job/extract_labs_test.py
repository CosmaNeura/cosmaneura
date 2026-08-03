from typing import cast

import app.jobs.extract_labs as job
from app.jobs.extract_labs import perform, persist_results

from app.models.lab_panel import LabPanel, LabPanelSource, LabPanelStatus
from app.models.lab_result import LabResult

from tests.factories import DoctorFactory, PatientFactory


def _pending_upload_panel(doctor, patient) -> LabPanel:
    return LabPanel(
        doctor_id=doctor.id,
        patient_id=patient.id,
        source=LabPanelSource.upload,
        status=LabPanelStatus.pending,
        azure_file_name="labs.pdf",
    ).save()


def test_persist_normalizes_and_writes_rows():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    panel = _pending_upload_panel(doctor, patient)

    count = persist_results(
        panel,
        [
            {"name": "Vitamin D", "value": 32, "unit": "ng/mL"},
            {"name": "TSH", "value": 2.1, "unit": None},
            {"name": "unlabeled goo", "value": None},  # skipped: no numeric value
            {"name": "", "value": 5},  # skipped: no name
        ],
    )
    assert count == 2

    rows = cast(
        list[LabResult], list(LabResult.where(LabResult.lab_panel_id == panel.id).all())
    )
    by_key = {r.biomarker: r for r in rows}
    assert by_key["vitamin_d_25oh"].value == 32.0
    assert by_key["vitamin_d_25oh"].raw_name == "Vitamin D"
    # unit defaulted from the registry when the source omitted it
    assert by_key["tsh"].unit == "mIU/L"


def test_unrecognized_biomarker_kept_as_slug():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    panel = _pending_upload_panel(doctor, patient)

    persist_results(panel, [{"name": "Exotic Marker", "value": 1.0, "unit": "x"}])
    row = cast(LabResult, LabResult.where(LabResult.lab_panel_id == panel.id).one())
    assert row.biomarker == "exotic marker"
    assert row.raw_name == "Exotic Marker"


def test_perform_extracts_and_completes_live_path(monkeypatch):
    # exercise the real (non-offline) path with mocked Azure + model IO
    monkeypatch.setattr(job, "should_use_test_openai_fallback", lambda: False)
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    panel = _pending_upload_panel(doctor, patient)

    monkeypatch.setattr(job, "download_pages", lambda p: ["data:image/png;base64,x"])
    monkeypatch.setattr(
        job,
        "extract_lab_values",
        lambda pages: [{"name": "Ferritin", "value": 45, "unit": "ng/mL"}],
    )

    perform(panel.id)

    panel = LabPanel.one(id=panel.id)
    assert panel.status == LabPanelStatus.completed
    assert LabResult.where(LabResult.lab_panel_id == panel.id).count() == 1


def test_perform_marks_failed_on_download_error(monkeypatch):
    monkeypatch.setattr(job, "should_use_test_openai_fallback", lambda: False)
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    panel = _pending_upload_panel(doctor, patient)

    def boom(p):
        raise RuntimeError("azure down")

    monkeypatch.setattr(job, "download_pages", boom)
    perform(panel.id)

    panel = LabPanel.one(id=panel.id)
    assert panel.status == LabPanelStatus.failed


def test_perform_offline_completes_empty(monkeypatch):
    # The offline short-circuit fires only when PYTHON_ENV=test AND the Azure
    # OpenAI credentials are absent. CI has them configured, so relying on the
    # ambient environment made this pass locally and fail in CI. Force the
    # branch under test, same as test_perform_marks_failed_on_download_error
    # forces the opposite one.
    monkeypatch.setattr(job, "should_use_test_openai_fallback", lambda: True)
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    panel = _pending_upload_panel(doctor, patient)

    perform(panel.id)

    panel = LabPanel.one(id=panel.id)
    assert panel.status == LabPanelStatus.completed
    assert LabResult.where(LabResult.lab_panel_id == panel.id).count() == 0


def test_perform_idempotent_on_completed(monkeypatch):
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    panel = LabPanel(
        doctor_id=doctor.id,
        patient_id=patient.id,
        source=LabPanelSource.upload,
        status=LabPanelStatus.completed,
        azure_file_name="labs.pdf",
    ).save()

    def fail(p):
        raise AssertionError("should not download for a completed panel")

    monkeypatch.setattr(job, "download_pages", fail)
    perform(panel.id)  # must not raise
