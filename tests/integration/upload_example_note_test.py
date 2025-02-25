from playwright.sync_api import Page, expect

from app import root

from app.models.doctor_note_file import DoctorNoteFile

from tests.integration.utils import login_as_dev_user


def test_example_note_upload(server, page: Page) -> None:
    # paranoid as we build the ORM
    assert DoctorNoteFile.count() == 0

    doctor = login_as_dev_user(page)

    page.get_by_role("button", name="Settings").click()
    page.get_by_role("textbox", name="Upload Example Visit Note").set_input_files(
        root / "tests/data/example-note.pdf"
    )

    page.locator('[id="downshift-\\:ra\\:-input"]').click()
    page.locator('[id="downshift-\\:ra\\:-input"]').fill("Example Note")
    page.get_by_role("button", name="Upload Example Visit Notes").click()

    expect(page.get_by_text("Notification Upload")).to_be_visible()

    assert DoctorNoteFile.count() == 1

    doctor_note = DoctorNoteFile.first()

    assert doctor_note
    assert doctor_note.note_type == "Example Note"
    assert doctor_note.doctor_id == doctor.id
