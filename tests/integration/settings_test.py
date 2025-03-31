from playwright.sync_api import Page, expect

from app import root

from app.models.doctor_note_file import DoctorNoteFile

from tests.integration.utils import login_as_dev_user


def test_example_note_upload(server, page: Page) -> None:
    # paranoid as we build the ORM
    assert DoctorNoteFile.count() == 0

    # page.set_default_timeout(120_000)
    # page.set_default_navigation_timeout(120_000)
    doctor = login_as_dev_user(page)

    # page.unroute("https://resolved-emu-53.clerk.accounts.dev/v1/**")

    page.get_by_role("button", name="Settings").click()
    page.get_by_role("textbox", name="Upload Example Visit Note").set_input_files(
        root / "tests/data/example-note.pdf"
    )

    # super brittle!
    # page.pause()

    # breakpoint()
    # page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    # page.locator('[id="downshift-\\:rb\\:-input"]').click()
    # page.locator('[id="downshift-\\:rb\\:-input"]').fill("Example Note")
    page.locator('[name="note_type"]').click()
    page.locator('[name="note_type"]').fill("Example Note")
    page.get_by_role("button", name="Upload Example Visit Note").click()

    expect(page.get_by_text("Notification Upload")).to_be_visible()

    assert DoctorNoteFile.count() == 1

    doctor_note = DoctorNoteFile.first()

    assert doctor_note
    assert doctor_note.note_type == "Example Note"
    assert doctor_note.doctor_id == doctor.id


def test_biography_update(server, page: Page):
    doctor = login_as_dev_user(page)

    biography_text = "This is my biography"

    page.get_by_role("button", name="Settings").click()
    page.get_by_role("textbox", name="Biography").fill(biography_text)
    page.get_by_role("button", name="Save").click()

    expect(page.get_by_text("Notification Biography Updated")).to_be_visible()

    doctor.refresh()

    assert doctor.biography == biography_text
