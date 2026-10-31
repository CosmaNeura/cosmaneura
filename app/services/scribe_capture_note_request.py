"""The unchanged note request contract shared by browser and native facades."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services import scribe_capture_note_edits as note_edits
from app.services.scribe_capture_notebook import ScratchNotes


class NoteScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    binding_ref: str = Field(
        pattern=r"^scribe_capture_binding_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    workspace_id: UUID
    request_nonce: UUID
    expected_session_id: UUID


class NoteRequest(NoteScopeRequest):
    schema_ref: str | None = Field(
        default=None, pattern=r"^doctor_note_schema_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    action: Literal[
        "read",
        "prepare",
        "templates",
        "template_preference",
        "save",
        "resolve",
        "save_status",
    ]
    edit: note_edits.NoteEdit | None = None
    scratch_notes: ScratchNotes | None = None

    @model_validator(mode="after")
    def template_selection(self) -> NoteRequest:
        if (self.action in {"templates", "template_preference"}) != (
            self.schema_ref is None
        ):
            raise ValueError("invalid_template_selection")
        if (self.action in {"save", "resolve", "save_status"}) != (
            self.edit is not None
        ):
            raise ValueError("invalid_note_edit_selection")
        if self.scratch_notes is not None and (
            self.action not in {"read", "prepare"}
            or self.scratch_notes.workspace_id != self.workspace_id
        ):
            raise ValueError("invalid_notebook_source")
        return self
