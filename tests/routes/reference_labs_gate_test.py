import pytest

import app.commands.uploads.sas as uploads
import app.jobs.extract_labs as extract_labs
import app.services.reference_labs_gate as labs
from app.server import api_app

from app.models.doctor import Doctor
from app.models.lab_panel import LabPanel, LabPanelSource, LabPanelStatus

from tests.factories import PatientFactory
from tests.routes.utils import get_clerk_dev_user


@pytest.mark.parametrize("release_enabled", [False, True])
@pytest.mark.parametrize(
    "method,route,parameter,body",
    [
        ("get", "lab_panel_list", "patient_id", None),
        (
            "post",
            "lab_panel_create",
            "patient_id",
            {"results": [{"name": "TSH", "value": 2}]},
        ),
        (
            "post",
            "generate_signed_lab_upload_url",
            None,
            {"file_name": "synthetic.pdf", "file_size": 123},
        ),
        (
            "post",
            "lab_panel_upload_complete",
            "patient_id",
            {"registration_token": "A" * 43},
        ),
        ("delete", "lab_panel_delete", "lab_panel_id", None),
        ("get", "lab_evaluations", "patient_id", None),
        ("get", "lab_range_list", None, None),
        ("put", "lab_range_upsert", "biomarker", {"standard_low": 1}),
        ("delete", "lab_range_reset", "biomarker", None),
    ],
)
def test_lab_routes_require_both_release_and_cohort_without_side_effects(
    authenticated_client, monkeypatch, release_enabled, method, route, parameter, body
):
    _, _, user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=user.id)
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    panel = LabPanel(
        doctor_id=doctor.id,
        patient_id=patient.id,
        source=LabPanelSource.upload,
        status=LabPanelStatus.pending,
        azure_file_name="synthetic.pdf",
    ).save()
    monkeypatch.setattr(labs, "REFERENCE_LABS_ENABLED", release_enabled)
    # Exercise both a disabled release with an admitted provider and an enabled
    # release with an unadmitted provider. Neither may reach the lab operation.
    monkeypatch.setattr(
        labs, "reference_toolkit_enabled_for", lambda _: not release_enabled
    )

    def forbidden(*args, **kwargs):
        pytest.fail("denied labs must not issue/consume uploads or queue extraction")

    monkeypatch.setattr(uploads, "issue_upload", forbidden)
    monkeypatch.setattr(uploads, "consume_upload", forbidden)
    monkeypatch.setattr(extract_labs, "queue", forbidden)
    values = {
        "patient_id": str(patient.id),
        "lab_panel_id": str(panel.id),
        "biomarker": "tsh",
    }
    path = api_app.url_path_for(
        route, **({parameter: values[parameter]} if parameter else {})
    )
    response = authenticated_client.request(
        method, path, **({"json": body} if body else {})
    )
    assert response.status_code == 404
    assert LabPanel.one(id=panel.id).status == LabPanelStatus.pending
    assert LabPanel.one(id=panel.id).deleted_at is None
