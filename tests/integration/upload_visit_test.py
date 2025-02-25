from datetime import datetime

from playwright.sync_api import Page, expect

from app import root
from app.configuration.azure import AZURE_UPLOADS_CONTAINER_NAME
from app.utils.azure import download_blob

from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.visit_audio_file import VisitAudioFile

from tests.integration.clerk import setup_clerk_testing_token
from tests.integration.server import home_url
from tests.integration.utils import login_as_dev_user
from tests.routes.utils import get_clerk_dev_user


def test_audio_file_upload(server, page: Page) -> None:
    # paranoid ORM sanity check
    assert VisitAudioFile.count() == 0

    upload_file = root / "tests/data/recording.wav"
    assert upload_file.exists()

    login_as_dev_user(page)

    page.get_by_role("button", name="Notetaking").click()
    page.get_by_role("button", name="Upload Recording").click()
    page.locator('input[name="patient_name"]').fill("A Brand New Patient")

    page.get_by_role("textbox", name="Date of Birth").fill("august 1 1999")
    page.get_by_role("textbox", name="Date of Birth").press("Enter")

    page.get_by_label("dropzone").locator("input").set_input_files(upload_file)

    page.get_by_role("button", name="Upload").click()

    expect(page.get_by_text("Notification Upload")).to_be_visible()

    assert VisitAudioFile.count() == 1

    audio_file = VisitAudioFile.first()

    assert audio_file
    assert audio_file.azure_file_name.endswith("recording.wav")
    assert audio_file.doctor_id is not None
    assert audio_file.id is not None

    # make sure the patient DOB is what we expect
    patient = Patient.get(audio_file.patient_id)
    assert patient
    assert patient.date_of_birth == datetime(1999, 8, 1)

    # let's make sure the bytes of the file are the same as what we uploaded
    bytes = download_blob(audio_file.azure_file_name, AZURE_UPLOADS_CONTAINER_NAME)
    assert bytes == upload_file.read_bytes()
