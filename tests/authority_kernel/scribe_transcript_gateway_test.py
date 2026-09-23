"""Source text stays behind current Core admission and bounded Scribe output."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.services import authority
from app.services import scribe_capture_gateway as gateway
from app.services.scribe_workflow import CaptureTranscript, CaptureTranscriptionStatus

from app.models.authority import AuthorityActorExternalIdentity
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_gateway_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_gateway_test import (
    arguments,
    pair,
    ready,
)
from tests.authority_kernel.scribe_capture_gateway_test import (
    capture_engine as _capture_engine,
)

admission_case = _admission_case
capture_engine = _capture_engine


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["status", "operation"])
async def test_transcript_revocation_withholds_text_and_stops_next_read(
    admission_case, phase
):
    f = admission_case
    scope = await ready(f)
    operation, f.client.result = pair("transcript", scope)

    def revoke(observed):
        if observed == phase:
            with Session(f.engine) as session:
                identity = session.get(
                    AuthorityActorExternalIdentity, f.case.identity.id
                )
                assert identity is not None
                identity.revoked_at = datetime.now(UTC)
                session.add(identity)
                session.commit()

    f.client.after_native = revoke
    with pytest.raises(authority.AuthorityKernelError):
        await gateway.execute_capture_operation(**arguments(f, operation))
    assert len(f.client.operation_calls) == (1 if phase == "operation" else 0)


@pytest.mark.asyncio
async def test_unfinished_transcription_never_reads_or_fabricates_text(admission_case):
    f = admission_case
    scope = await ready(f)
    operation, f.client.result = pair("transcript", scope)

    async def processing(tenant_id, session_id):
        return CaptureTranscriptionStatus(
            session_id=session_id,
            status="processing",
            transcript_available=False,
            updated_at=datetime.now(UTC),
        )

    f.client.get_capture_status = processing
    with pytest.raises(gateway.CaptureOperationUnavailable):
        await gateway.execute_capture_operation(**arguments(f, operation))
    assert not f.client.operation_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["foreign_session", "size"])
async def test_source_transcript_scope_and_total_content_limit(admission_case, fault):
    f = admission_case
    scope = await ready(f)
    operation, original = pair("transcript", scope)
    data = original.model_dump(mode="json")
    if fault == "foreign_session":
        data["session_id"] = str(uuid4())
    else:
        data["segments"] = [
            {**data["segments"][0], "content": "x" * 100_000} for _ in range(43)
        ]
    f.client.result = CaptureTranscript.model_validate(data)
    with pytest.raises(gateway.CaptureReceiptInvalid) as error:
        await gateway.execute_capture_operation(**arguments(f, operation))
    assert not str(error.value)


def test_transcript_representation_does_not_include_speech():
    from tests.authority_kernel.scribe_gateway_receiver_test import scope

    _, transcript = pair("transcript", scope())
    assert isinstance(transcript, CaptureTranscript)
    assert "Invented visit" not in repr(transcript)
    assert "Invented visit" not in repr(transcript.segments[0])
