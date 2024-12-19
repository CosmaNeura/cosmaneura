from playwright.sync_api import Page, expect

from app import root

from app.models.user import User
from app.models.visit_audio_file import VisitAudioFile

from tests.integration.clerk import setup_clerk_testing_token
from tests.integration.server import home_url
from tests.utils import get_clerk_dev_user


def test_audio_file_upload(server, page: Page) -> None:
    username, password, user = get_clerk_dev_user()

    # paranoid testing to ensure database cleaning is working
    assert User.count() == 0

    setup_clerk_testing_token(page)

    page.goto(home_url())

    page.get_by_label("Email address").fill(username)
    page.get_by_role("button", name="Continue", exact=True).click()

    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Continue").click()

    page.get_by_role("button", name="Notetaking").click()
    page.get_by_role("button", name="Upload Recording").click()
    page.locator('[id="\\:rl\\:-form-item"]').set_input_files(
        root / "tests/data/recording.wav"
    )
    page.get_by_role("button", name="Upload").click()
    page.get_by_role("status").click()

    expect(page.get_by_role("status")).to_be_visible()

    assert VisitAudioFile.count() == 1

    audio_file = list(VisitAudioFile.all())[0]

    assert audio_file.azure_file_name.endswith("recording.wav")
    assert audio_file.user_id is not None
    assert audio_file.id is not None
