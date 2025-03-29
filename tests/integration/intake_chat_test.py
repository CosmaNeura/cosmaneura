
from furl import furl
from playwright.sync_api import Page, expect

from tests.constants import PYTHON_TEST_SERVER_HOST
from tests.factories import SimpleIncompleteIntakeFactory


def test_intake_chat(server, page: Page) -> None:
    intake = SimpleIncompleteIntakeFactory.build().save()

    intake_url = furl(str(intake.public_url()))
    intake_url.set(host=PYTHON_TEST_SERVER_HOST)

    # this tests mocks out 3, let's make sure that doesn't change
    assert len(intake.intake_questions) == 3

    page.goto(intake_url.url)

    page.get_by_role("textbox", name="Include a long and detailed").click()
    page.get_by_role("textbox", name="Include a long and detailed").fill(
        "so many\n\non multiple lines, too "
    )
    page.get_by_role("button", name="Send").click()
    page.get_by_role("textbox", name="Include a long and detailed").click()
    page.get_by_role("textbox", name="Include a long and detailed").fill(
        "so many, hard to list"
    )
    page.get_by_role("button", name="Send").click()
    page.get_by_role("textbox", name="Include a long and detailed").click()
    page.get_by_role("textbox", name="Include a long and detailed").fill(
        "yeah lots\n\n\n\n\n\nad way down here"
    )
    page.get_by_role("button", name="Send").click()
    expect(page.locator("body")).to_contain_text("Intake Complete")
