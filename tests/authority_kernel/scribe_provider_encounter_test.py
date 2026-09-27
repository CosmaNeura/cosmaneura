"""Cross-device discovery never creates consent or mistakes a conflict for new work."""

from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as protocol
from app.services.scribe_workflow import ScribeWorkflowClient

from tests.authority_kernel.scribe_native_recording_context_test import exercise


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["created", "capturing", "uploaded", "processing", "speech_failed"]
)
async def test_existing_session_is_discovered_in_one_creator_read(monkeypatch, status):
    result, calls, authorizations, selected, _ = await exercise(
        monkeypatch, status=status, kind="provider_encounter"
    )
    assert isinstance(result, protocol.ProviderEncounter)
    assert result.status == "available" and result.review is not None
    assert (
        result.review.session_id == selected.session_id
        and result.review.status == status
    )
    assert len(calls) == 1 and calls[0][2].kind == "creator_review"
    assert authorizations == [True, True]
    assert str(selected.session_id) not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "legacy",
        "missing_actor",
        "revoked_after",
        "changed_scope",
        "wrong_session",
        "lost",
    ],
)
async def test_discovery_never_falls_back_after_denied_or_uncertain_read(
    monkeypatch, failure
):
    with pytest.raises(gateway.CaptureGatewayError):
        await exercise(monkeypatch, failure=failure, kind="provider_encounter")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        admission.AdmissionConsentRequired,
        admission.AdmissionConsentUnavailable,
        admission.AdmissionUnconfirmed,
        admission.AdmissionConflict,
    ],
)
async def test_only_missing_admission_can_return_not_prepared(monkeypatch, error):
    def authorize(*_):
        raise error()

    monkeypatch.setattr(gateway, "authorize_admitted_capture", authorize)
    arguments = dict(
        caller=cast(admission.CaptureCaller, None),
        binding_ref="synthetic-binding",
        workspace_id=uuid4(),
        operation=protocol.ReadOperation(kind="provider_encounter"),
        client_request_key="synthetic-discovery-key",
        client=cast(ScribeWorkflowClient, None),
    )
    if error == admission.AdmissionConsentRequired:
        result = await gateway.execute_capture_operation(**arguments)
        assert result.model_dump(mode="json") == {
            "schema_version": "1.0",
            "status": "not_prepared",
            "review": None,
        }
    else:
        with pytest.raises(error):
            await gateway.execute_capture_operation(**arguments)


def test_discovery_receipt_requires_exact_status_and_review_pair():
    with pytest.raises(ValidationError):
        protocol.ProviderEncounter(status="available", review=None)
