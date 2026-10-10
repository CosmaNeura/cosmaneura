"""Explicit, compare-and-save edits of a currently admitted Core draft.

The prior note body is retained. A journal and replacement current version
commit together; retrying cannot overwrite an intervening edit or restore.
"""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

import orjson
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typeid import TypeID

from app.services import scribe_capture_notes as notes
from app.services.scribe_capture_admission import (
    CaptureCaller,
)
from app.services.scribe_workflow import ScribeWorkflowClient

from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.scribe_capture_note_edit import (
    ScribeCaptureNoteEdit,
    ScribeCaptureNoteSaveFence,
)
from sqlalchemy import update
from sqlmodel import select

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class NoteEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    client_key: UUID
    expected_note_ref: str = Field(pattern=r"^visit_note_[0-7][0-9a-hjkmnp-tv-z]{25}$")
    expected_note_sha256: Digest
    expected_source_sha256: Digest
    fields: dict[str, str] = Field(min_length=1, max_length=100, repr=False)

    @field_validator("fields")
    @classmethod
    def bounded_fields(cls, fields):
        try:
            if any(
                not key or len(key) > 200 or "\0" in key or "\0" in value
                for key, value in fields.items()
            ):
                raise ValueError
            if len(orjson.dumps(fields)) > notes.MAX_MODEL_BYTES:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("invalid_note_edit") from None
        return fields


class SaveResolution(BaseModel):
    """Past request outcome, never a current draft or review authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    session_id: UUID
    schema_ref: str = Field(pattern=r"^doctor_note_schema_[0-7][0-9a-hjkmnp-tv-z]{25}$")
    client_key: UUID
    request_sha256: Digest
    status: Literal["saved", "closed", "pending"]
    result_note_ref: str | None = Field(
        default=None, pattern=r"^visit_note_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    result_sha256: Digest | None = None

    @model_validator(mode="after")
    def consistent_result(self):
        if (self.result_note_ref is not None) != (self.status == "saved") or (
            self.result_sha256 is not None
        ) != (self.status == "saved"):
            raise ValueError("invalid_note_save_resolution")
        return self


def request_hash(
    edit: NoteEdit, schema_ref: str, session_id: UUID, workspace_id: UUID
) -> str:
    return hashlib.sha256(
        orjson.dumps(
            {
                "edit": edit.model_dump(mode="json"),
                "schema": schema_ref,
                "session": str(session_id),
                "workspace": str(workspace_id),
            },
            option=orjson.OPT_SORT_KEYS,
        )
    ).hexdigest()


async def resolve_capture_note_save(
    *,
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    expected_session_id: UUID,
    schema_ref: str,
    edit: NoteEdit,
    client: ScribeWorkflowClient,
    _close_pending: bool = True,
) -> SaveResolution:
    """Confirm a retained save or prevent an unpublished request from committing."""
    notes._enabled(caller)
    edit = NoteEdit.model_validate(edit.model_dump())
    caller = replace(caller, selected_session_id=expected_session_id)
    scope = notes._authorize_note_scope(caller, binding_ref, workspace_id)
    if scope.session_id != expected_session_id:
        raise notes.CaptureNoteConflict
    digest = request_hash(edit, schema_ref, scope.session_id, workspace_id)
    # A changed transcript is allowed; revoked consent/authority is not.
    await notes._current_consent(caller, binding_ref, workspace_id, scope, client)
    with notes._database() as session:
        notes._locked_scope(session, caller, binding_ref, workspace_id, scope)
        session.exec(
            select(Appointment)
            .where(Appointment.id == scope.session_request.appointment_id)
            .with_for_update()
        ).one()
        saved = session.exec(
            select(ScribeCaptureNoteEdit).where(
                ScribeCaptureNoteEdit.binding_id == binding_ref,
                ScribeCaptureNoteEdit.client_key == edit.client_key,
            )
        ).one_or_none()
        closed = session.exec(
            select(ScribeCaptureNoteSaveFence).where(
                ScribeCaptureNoteSaveFence.binding_id == binding_ref,
                ScribeCaptureNoteSaveFence.client_key == edit.client_key,
            )
        ).one_or_none()
        if saved is not None and closed is not None:
            raise notes.CaptureNoteConflict
        recorded = saved or closed
        if recorded is not None and (
            recorded.request_sha256 != digest
            or recorded.session_id != scope.session_id
            or str(recorded.base_note_id) != edit.expected_note_ref
        ):
            raise notes.CaptureNoteConflict
        result = None
        if saved is not None:
            result = session.exec(
                select(AIVisitNote)
                .where(AIVisitNote.id == saved.result_note_id)
                .with_for_update(of=AIVisitNote)
                .execution_options(populate_existing=True)
            ).one()
            if (
                str(result.doctor_id) != scope.session_request.practitioner_id
                or str(result.appointment_id) != scope.session_request.appointment_id
                or str(result.doctor_note_schema_id) != schema_ref
                or saved.source_sha256 != edit.expected_source_sha256
                or result.note != edit.fields
                or hashlib.sha256(orjson.dumps(list(result.note.items()))).hexdigest()
                != saved.result_sha256
            ):
                raise notes.CaptureNoteConflict
        elif closed is None:
            base = session.exec(
                select(AIVisitNote)
                .where(AIVisitNote.id == edit.expected_note_ref)
                .with_for_update(of=AIVisitNote)
                .execution_options(populate_existing=True)
            ).one_or_none()
            if (
                base is None
                or str(base.doctor_id) != scope.session_request.practitioner_id
                or str(base.appointment_id) != scope.session_request.appointment_id
                or str(base.doctor_note_schema_id) != schema_ref
                or hashlib.sha256(orjson.dumps(list(base.note.items()))).hexdigest()
                != edit.expected_note_sha256
            ):
                raise notes.CaptureNoteConflict
            if _close_pending:
                session.add(
                    ScribeCaptureNoteSaveFence(
                        binding_id=cast(TypeIDType, TypeID.from_string(binding_ref)),
                        client_key=edit.client_key,
                        session_id=scope.session_id,
                        base_note_id=base.id,
                        request_sha256=digest,
                    )
                )
                session.flush()
        receipt = SaveResolution(
            session_id=scope.session_id,
            schema_ref=schema_ref,
            client_key=edit.client_key,
            request_sha256=digest,
            status="saved"
            if saved is not None
            else ("closed" if closed is not None or _close_pending else "pending"),
            result_note_ref=str(result.id) if result is not None else None,
            result_sha256=saved.result_sha256 if saved is not None else None,
        )
        session.commit()
        return receipt


async def check_capture_note_save(
    *,
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    expected_session_id: UUID,
    schema_ref: str,
    edit: NoteEdit,
    client: ScribeWorkflowClient,
) -> SaveResolution:
    """Read the exact save receipt without fencing or publishing the request."""
    return await resolve_capture_note_save(
        caller=caller,
        binding_ref=binding_ref,
        workspace_id=workspace_id,
        expected_session_id=expected_session_id,
        schema_ref=schema_ref,
        edit=edit,
        client=client,
        _close_pending=False,
    )


async def save_capture_note(
    *,
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    expected_session_id: UUID,
    schema_ref: str,
    edit: NoteEdit,
    client: ScribeWorkflowClient,
) -> notes.DraftReceipt:
    notes._enabled(caller)
    edit = NoteEdit.model_validate(edit.model_dump())
    caller = replace(caller, selected_session_id=expected_session_id)
    scope = notes._authorize_note_scope(caller, binding_ref, workspace_id)
    if scope.session_id != expected_session_id:
        raise notes.CaptureNoteConflict
    source = await notes._read_source(caller, binding_ref, workspace_id, scope, client)
    if source.digest != edit.expected_source_sha256:
        raise notes.CaptureNoteConflict
    digest = request_hash(edit, schema_ref, scope.session_id, workspace_id)
    # The source receiver checks consent. Check once more immediately before
    # the local publication transaction; no network work occurs under a lock.
    await notes._current_source(
        caller, binding_ref, workspace_id, scope, client, source
    )
    with notes._database() as session:
        notes._locked_scope(session, caller, binding_ref, workspace_id, scope)
        session.exec(
            select(Appointment)
            .where(Appointment.id == scope.session_request.appointment_id)
            .with_for_update()
        ).one()
        if (
            session.exec(
                select(ScribeCaptureNoteSaveFence).where(
                    ScribeCaptureNoteSaveFence.binding_id == binding_ref,
                    ScribeCaptureNoteSaveFence.client_key == edit.client_key,
                )
            ).first()
            is not None
        ):
            raise notes.CaptureNoteConflict
        inputs = notes._inputs(session, scope, schema_ref, source)
        current = inputs.note
        if current.status != "draft" or current.fields is None:
            raise notes.CaptureNoteConflict
        replay = session.exec(
            select(ScribeCaptureNoteEdit).where(
                ScribeCaptureNoteEdit.binding_id == binding_ref,
                ScribeCaptureNoteEdit.client_key == edit.client_key,
            )
        ).one_or_none()
        if replay is not None:
            if (
                replay.request_sha256 != digest
                or replay.session_id != scope.session_id
                or str(replay.result_note_id) != current.note_ref
                or replay.result_sha256 != current.note_sha256
            ):
                raise notes.CaptureNoteConflict
            session.commit()
            return current
        if (
            current.note_ref != edit.expected_note_ref
            or current.note_sha256 != edit.expected_note_sha256
        ):
            raise notes.CaptureNoteConflict
        try:
            fields = notes._validated_note(
                orjson.dumps(edit.fields).decode(), current.fields
            )
        except notes.CaptureNoteUnconfirmed:
            raise notes.CaptureNoteConflict from None
        assert current.note_ref is not None
        prior = session.exec(
            select(AIVisitNote)
            .where(AIVisitNote.id == current.note_ref)
            .with_for_update(of=AIVisitNote)
            .execution_options(populate_existing=True)
        ).one()
        # Lock-and-compare again: legacy/manual writers may edit a row in place.
        if (
            prior.superseded_at is not None
            or hashlib.sha256(orjson.dumps(list(prior.note.items()))).hexdigest()
            != current.note_sha256
        ):
            raise notes.CaptureNoteConflict
        notebook = notes._notebook_context(session, scope, prior)
        session.execute(
            update(cast(Any, AIVisitNote))
            .where(cast(Any, AIVisitNote.id) == prior.id)
            .values(
                superseded_at=datetime.now(UTC),
                supersession_reason="clinician_capture_edit",
                updated_at=prior.updated_at,
            )
        )
        session.flush()
        result = AIVisitNote(
            doctor_id=prior.doctor_id,
            appointment_id=prior.appointment_id,
            ai_transcript_id=prior.ai_transcript_id,
            doctor_note_schema_id=prior.doctor_note_schema_id,
            note=fields,
            restore_frontier_transcript_id=prior.restore_frontier_transcript_id,
        )
        session.add(result)
        session.flush()
        notes._save_notebook_context(session, scope, result, notebook)
        session.add(
            ScribeCaptureNoteEdit(
                binding_id=cast(TypeIDType, TypeID.from_string(binding_ref)),
                client_key=edit.client_key,
                session_id=scope.session_id,
                base_note_id=prior.id,
                result_note_id=result.id,
                request_sha256=digest,
                source_sha256=source.digest,
                result_sha256=hashlib.sha256(
                    orjson.dumps(list(fields.items()))
                ).hexdigest(),
            )
        )
        # Validate the final receipt before committing either row.
        confirmed = notes._inputs(session, scope, schema_ref, source).note
        session.commit()
        return confirmed
