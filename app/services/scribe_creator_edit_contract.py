"""Closed consumer of Scribe creator-edit v1. Source remains immutable.

Wire fields mirror the Scribe generated OpenAPI; persistence belongs to Scribe.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.scribe_capture_working_contract import Role

MAX_TRANSCRIPT_CONTENT_CHARS = 100000


def _valid_utf8(value: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("creator_edit_text_invalid") from None
    if "\x00" in value:
        raise ValueError("creator_edit_text_invalid")


class _Protected(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<protected>)"

    def __str__(self) -> str:
        return self.__repr__()


class CreatorTextCorrectionV1(_Protected):
    source_segment_id: UUID
    expected_source_segment_version: int = Field(ge=1, strict=True)
    content: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_CONTENT_CHARS)

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        _valid_utf8(value)
        if not value.strip():
            raise ValueError("creator_edit_content_empty")
        return value


class CreatorSpeakerConfirmationV1(_Protected):
    speaker_id: UUID
    role: Role
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    detail: Literal["interpreter", "other"] | None = None

    @model_validator(mode="after")
    def consistent(self) -> CreatorSpeakerConfirmationV1:
        if self.display_name is not None:
            _valid_utf8(self.display_name)
        if self.detail is not None and self.role != "other":
            raise ValueError("creator_edit_speaker_detail_invalid")
        if self.role == "unknown" and self.display_name is not None:
            raise ValueError("creator_edit_unknown_speaker_named")
        if self.display_name is not None and (
            not self.display_name.strip()
            or any(ord(char) < 32 for char in self.display_name)
        ):
            raise ValueError("creator_edit_speaker_name_invalid")
        return self


class CreatorTranscriptEditBodyV1(_Protected):
    schema_version: Literal["1.0"] = "1.0"
    expected_source_transcript_version: int = Field(ge=1, strict=True)
    expected_working_transcript_id: UUID
    expected_working_transcript_version: int = Field(ge=1, strict=True)
    corrections: tuple[CreatorTextCorrectionV1, ...] = Field(default=(), max_length=128)
    speakers: tuple[CreatorSpeakerConfirmationV1, ...] = Field(
        default=(), max_length=128
    )

    @model_validator(mode="after")
    def bounded_unique_edits(self) -> CreatorTranscriptEditBodyV1:
        if not self.corrections and not self.speakers:
            raise ValueError("creator_edit_empty")
        ids = [item.source_segment_id for item in self.corrections]
        speakers = [item.speaker_id for item in self.speakers]
        if len(set(ids)) != len(ids) or len(set(speakers)) != len(speakers):
            raise ValueError("creator_edit_duplicate")
        if (
            sum(len(item.content) for item in self.corrections) > 500000
            or len(self.model_dump_json().encode("utf-8")) > 1_048_576
        ):
            raise ValueError("creator_edit_too_large")
        if len(self.model_dump_json().encode("utf-8")) > 1048576:
            raise ValueError("creator_edit_too_large")
        return self


class CreatorTranscriptEditV1(_Protected):
    edit_id: UUID
    working_transcript_version: int = Field(ge=2, strict=True)
    created_at: datetime
    request: CreatorTranscriptEditBodyV1

    @model_validator(mode="after")
    def valid_version(self) -> CreatorTranscriptEditV1:
        if (
            self.working_transcript_version
            != self.request.expected_working_transcript_version + 1
        ):
            raise ValueError("creator_edit_version_invalid")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("creator_edit_time_invalid")
        return self


class CreatorTranscriptEditReceiptV1(_Protected):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    edit_id: UUID
    working_transcript_id: UUID
    working_transcript_version: int = Field(ge=2, strict=True)
    saved: Literal[True] = True


def validate_edit_history(edits: tuple[CreatorTranscriptEditV1, ...]) -> None:
    if len(edits) > 1024 or len({item.edit_id for item in edits}) != len(edits):
        raise ValueError("creator_edit_history_invalid")
    versions = [item.working_transcript_version for item in edits]
    if (
        versions != sorted(set(versions))
        or sum(
            len(correction.content)
            for edit in edits
            for correction in edit.request.corrections
        )
        > 1000000
    ):
        raise ValueError("creator_edit_history_invalid")
