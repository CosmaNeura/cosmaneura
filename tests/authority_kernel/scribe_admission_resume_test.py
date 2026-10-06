"""CosmaBot's real admission shapes preserve the saved recording and consent."""

from uuid import uuid4

import pytest

from app.services import scribe_capture_admission as admission

from tests.authority_kernel.scribe_admission_receiver_test import (
    PATH,
    request_body,
    request_headers,
)
from tests.authority_kernel.scribe_capture_admission_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_admission_test import (
    call_arguments,
    journal,
)
from tests.authority_kernel.scribe_capture_admission_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_capture_receiver_test import client
from tests.authority_kernel.scribe_configured_capture_test import (
    SELECTION,
    install_configured,
)

admission_case = _admission_case
capture_engine = _capture_engine


@pytest.mark.parametrize("mode", ["resume_current", "expected_session_id"])
def test_receiver_accepts_explicit_resume_and_returns_structured_family(
    monkeypatch, mode
):
    payload = request_body()
    payload["attestation"] = None
    session_id = uuid4()
    payload[mode] = True if mode == "resume_current" else str(session_id)

    async def resume(**kwargs):
        assert kwargs[mode] == (True if mode == "resume_current" else session_id)
        assert kwargs["attestation"] is kwargs["configured_capture"] is None
        return admission.AdmissionSnapshot(
            operation_ref="scribe_admission_01j00000000000000000000000",
            binding_ref=kwargs["binding_ref"],
            workspace_id=kwargs["workspace_id"],
            appointment_ref="appointment_01j00000000000000000000000",
            consent_record_id=uuid4(),
            session_id=session_id,
            configured_capture=SELECTION,
        )

    monkeypatch.setattr(admission, "admit_capture", resume)
    response = client().post(PATH, headers=request_headers(), json=payload)
    assert response.status_code == 200
    assert response.json()["session_id"] == str(session_id)
    assert response.json()["configured_capture"] == SELECTION.model_dump(mode="json")


@pytest.mark.parametrize(
    "fault", ["both", "attestation", "preferences", "truthy", "invalid_id"]
)
def test_invalid_resume_rejected_before_service(monkeypatch, fault):
    payload = request_body()
    payload["resume_current"] = True
    if fault != "attestation":
        payload["attestation"] = None
    if fault == "both":
        payload["expected_session_id"] = str(uuid4())
    elif fault == "preferences":
        payload["configured_capture"] = SELECTION.model_dump()
    elif fault == "truthy":
        payload["resume_current"] = 1
    elif fault == "invalid_id":
        payload.pop("resume_current")
        payload["expected_session_id"] = "private_sentinel"
    monkeypatch.setattr(
        admission, "admit_capture", lambda **_: pytest.fail("service reached")
    )
    response = client().post(PATH, headers=request_headers(), json=payload)
    assert response.status_code == 422
    assert "private_sentinel" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_resume_reuses_saved_family_and_rejects_wrong_session_before_io(
    admission_case, configured
):
    f = admission_case
    if configured:
        install_configured(f)
    saved = await admission.admit_capture(
        **call_arguments(f), configured_capture=SELECTION if configured else None
    )
    before = journal(f)
    wrong = call_arguments(f, resume=True)
    calls_before = len(f.client.calls)
    configured_calls_before = len(getattr(f, "configured_calls", []))
    with pytest.raises(admission.AdmissionConflict):
        await admission.admit_capture(**wrong, expected_session_id=uuid4())
    assert len(f.client.calls) == calls_before
    assert len(getattr(f, "configured_calls", [])) == configured_calls_before
    for expected_session_id, resume_current in (
        (None, True),
        (saved.session_id, False),
    ):
        resumed = await admission.admit_capture(
            **call_arguments(f, resume=True),
            expected_session_id=expected_session_id,
            resume_current=resume_current,
        )
        assert resumed == saved
    after = journal(f)
    assert before.id == after.id
    assert before.configured_capture == after.configured_capture
    assert len(f.client.consents) == len(f.client.sessions) == 1
    assert saved.configured_capture == (SELECTION if configured else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["resume_current", "expected_session_id"])
async def test_resume_cannot_create_an_admission_without_consent(admission_case, mode):
    f = admission_case
    error = (
        admission.AdmissionConsentRequired
        if mode == "resume_current"
        else admission.AdmissionConflict
    )
    with pytest.raises(error):
        await admission.admit_capture(
            **call_arguments(f, resume=True),
            expected_session_id=uuid4() if mode == "expected_session_id" else None,
            resume_current=mode == "resume_current",
        )
    assert f.client.calls == []
    assert not f.client.consents and not f.client.sessions
