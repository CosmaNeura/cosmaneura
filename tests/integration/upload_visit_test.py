from playwright.sync_api import Page, expect

from app import root

from app.models.doctor import Doctor
from app.models.visit_audio_file import VisitAudioFile

from tests.integration.clerk import setup_clerk_testing_token
from tests.integration.server import home_url
from tests.integration.utils import login_as_dev_user
from tests.routes.utils import get_clerk_dev_user


def test_audio_file_upload(server, page: Page) -> None:
    login_as_dev_user(page)

    page.get_by_role("button", name="Notetaking").click()
    page.get_by_role("button", name="Upload Recording").click()
    page.locator('input[name="audio_file"]').set_input_files(
        root / "tests/data/recording.wav"
    )
    page.locator('input[name="patient_name"]').fill("A Brand New Patient")

    page.get_by_test_id("date-of-birth-button").click()
    page.get_by_role("gridcell", name="1", exact=True).nth(1).click()
    page.get_by_test_id("date-of-birth-button").click()

    page.get_by_role("button", name="Upload").click()

    expect(page.get_by_text("Notification Upload")).to_be_visible()

    assert VisitAudioFile.count() == 1

    audio_file = VisitAudioFile.first()

    assert audio_file.azure_file_name.endswith("recording.wav")
    assert audio_file.doctor_id is not None
    assert audio_file.id is not None
