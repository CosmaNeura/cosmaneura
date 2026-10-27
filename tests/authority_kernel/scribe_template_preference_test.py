from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.routes.scribe_capture import NoteRequest
from app.services import authority
from app.services import scribe_capture_notes as notes
from app.services.scribe_capture_gateway import CaptureOperationUnavailable
from app.services.scribe_workflow import ScribeWorkflowRejectedError

from app.models.note_template_preference import NoteTemplatePreference
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_notes_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_notes_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_capture_notes_test import (
    setup,
)

admission_case = _admission_case
capture_engine = _capture_engine


@pytest.mark.asyncio
async def test_new_preference_read_keeps_old_templates_wire_and_note_generation_unchanged(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    args = {
        k: v for k, v in kwargs.items() if k not in {"schema_ref", "prepare", "model"}
    }
    old = await notes.capture_note_templates(**args)
    assert (await notes.capture_template_preference(**args)).default_schema_ref is None
    with Session(f.engine) as session:
        session.add(
            NoteTemplatePreference(
                doctor_id=f.case.doctor.id, default_schema_id=f.schema_ref, revision=1
            )
        )
        session.commit()
    receipt = await notes.capture_template_preference(**args)
    assert receipt.model_dump(mode="json") == {
        "session_id": str(f.scope.session_id),
        "default_schema_ref": f.schema_ref,
    }
    assert await notes.capture_note_templates(**args) == old
    assert not f.model.calls


@pytest.mark.asyncio
async def test_preference_read_still_requires_exact_session_and_current_consent(
    admission_case, monkeypatch
):
    f = admission_case
    kwargs = await setup(f, monkeypatch)
    args = {
        k: v for k, v in kwargs.items() if k not in {"schema_ref", "prepare", "model"}
    }
    with pytest.raises((notes.CaptureNoteConflict, authority.AuthorityKernelError)):
        await notes.capture_template_preference(
            **{**args, "expected_session_id": uuid4()}
        )

    async def denied(*_):
        raise ScribeWorkflowRejectedError("synthetic denial")

    f.client.get_capture_status = denied
    with pytest.raises(CaptureOperationUnavailable):
        await notes.capture_template_preference(**args)


def test_preference_action_cannot_select_a_template_or_supply_note_edits():
    value = {
        "binding_ref": "scribe_capture_binding_00000000000000000000000000",
        "workspace_id": str(uuid4()),
        "request_nonce": str(uuid4()),
        "expected_session_id": str(uuid4()),
        "action": "template_preference",
    }
    assert NoteRequest.model_validate(value).schema_ref is None
    with pytest.raises(ValidationError):
        NoteRequest.model_validate(
            {**value, "schema_ref": "doctor_note_schema_00000000000000000000000000"}
        )
    with pytest.raises(ValidationError):
        NoteRequest.model_validate({**value, "edit": {"not": "a read"}})
