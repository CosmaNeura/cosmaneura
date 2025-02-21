from playwright.sync_api import Page, expect

from app import root

from app.models.doctor import Doctor
from app.models.visit_audio_file import VisitAudioFile

from tests.integration.clerk import setup_clerk_testing_token
from tests.integration.server import home_url
from tests.routes.utils import get_clerk_dev_user


def login_as_dev_user(page: Page) -> None:
    username, password, user = get_clerk_dev_user()

    # paranoid testing to ensure database cleaning is working
    assert Doctor.count() == 0

    setup_clerk_testing_token(page)

    page.goto(home_url())

    page.get_by_label("Email address").fill(username)
    page.get_by_role("button", name="Continue", exact=True).click()

    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Continue").click()


def test_example_note_upload(server, page: Page) -> None:
    login_as_dev_user(page)

    page.get_by_role("button", name="Settings").click()
    page.get_by_role("textbox", name="Upload Example Visit Note").set_input_files(
        root / "tests/data/example-note.pdf"
    )

    page.locator('[id="downshift-\\:ra\\:-input"]').click()
    page.locator('[id="downshift-\\:ra\\:-input"]').fill("Example Note")
    page.get_by_role("button", name="Upload Example Visit Notes").click()

    expect(page.get_by_text("Notification Upload")).to_be_visible()
