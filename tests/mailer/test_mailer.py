import os

import pytest

from app.configuration.emailer import mail

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_SMTP_TESTS") != "true",
    reason="requires a live SMTP server; set RUN_LIVE_SMTP_TESTS=true to enable",
)


def test_mailer():
    _result = mail(
        template_path="mail/notification.md",
        context={
            "name": "John Doe",
        },
        subject="Hello!",
        to="example@example.com",
    )
