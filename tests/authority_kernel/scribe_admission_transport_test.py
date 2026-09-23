"""Bounded consent/session transport failures must not broaden effects."""

from uuid import uuid4

import httpx
import pytest

from app.services.scribe_workflow import HttpScribeWorkflowClient, ScribeWorkflowError

from tests.services.scribe_workflow_test import StaticCredential, consent_payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["redirect", "oversized", "non_json", "malformed", "transport"]
)
async def test_post_failures_are_bounded_not_redirected_and_do_not_reflect_content(
    kind,
):
    seen = []

    def handler(request):
        seen.append(request)
        if kind == "redirect":
            return httpx.Response(
                307, headers={"Location": "https://other.invalid/private_sentinel"}
            )
        if kind == "oversized":
            return httpx.Response(
                201, headers={"Content-Type": "application/json"}, content=b" " * 16385
            )
        if kind == "non_json":
            return httpx.Response(
                201, headers={"Content-Type": "text/plain"}, text="private_sentinel"
            )
        if kind == "malformed":
            return httpx.Response(201, json={"private_sentinel": "private_sentinel"})
        raise httpx.ReadError("private_sentinel", request=request)

    async with httpx.AsyncClient(
        base_url="https://scribe.internal.example",
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as transport:
        client = HttpScribeWorkflowClient(
            base_url="https://scribe.internal.example",
            scope="api://scribe/.default",
            timeout_seconds=5,
            credential=StaticCredential(),
            client=transport,
        )
        with pytest.raises(ScribeWorkflowError) as error:
            await client.create_consent(
                uuid4(), consent_payload(), idempotency_key=uuid4().hex
            )
        assert "private_sentinel" not in str(error.value)
        assert error.value.__cause__ is None
        assert len(seen) == 1


@pytest.mark.parametrize(
    "origin",
    [
        "http://scribe.invalid",
        "https://user:secret@scribe.invalid",
        "https://scribe.invalid/v1",
        "https://scribe.invalid?tenant=x",
        "https://scribe.invalid:444",
    ],
)
def test_non_origin_configuration_is_rejected(origin):
    with pytest.raises(ValueError):
        HttpScribeWorkflowClient(
            base_url=origin,
            scope="api://scribe/.default",
            timeout_seconds=5,
            credential=StaticCredential(),
        )


@pytest.mark.asyncio
async def test_injected_client_cannot_choose_another_origin():
    async with httpx.AsyncClient(base_url="https://other.invalid") as transport:
        with pytest.raises(ValueError):
            HttpScribeWorkflowClient(
                base_url="https://scribe.internal.example",
                scope="api://scribe/.default",
                timeout_seconds=5,
                credential=StaticCredential(),
                client=transport,
            )
