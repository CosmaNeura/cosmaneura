"""Exercise worker-thread delivery without connecting to an SMTP server."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.configuration import emailer


def test_mail_can_send_repeatedly_from_a_worker_without_an_event_loop(monkeypatch):
    loops = []
    messages = []

    async def send(message):
        loops.append(asyncio.get_running_loop())
        messages.append(message)
        return "synthetic-delivery"

    monkeypatch.setattr(emailer, "_mailer", SimpleNamespace(send=send))
    monkeypatch.setattr(emailer, "render_email", lambda *_: ("<p>Test</p>", "Test"))

    with ThreadPoolExecutor(max_workers=1) as worker:
        for _ in range(2):
            assert worker.submit(
                emailer.mail,
                to="invitee@synthetic.invalid",
                subject="Synthetic invitation",
                template_path="unused.md",
                context={},
            ).result(timeout=5) == "synthetic-delivery"

    assert len(messages) == 2
    assert len(loops) == 2 and loops[0] is not loops[1]
    assert all(loop.is_closed() for loop in loops)
