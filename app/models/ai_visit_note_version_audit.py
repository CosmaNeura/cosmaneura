"""Append-only, non-PHI evidence for an administrative note-version restore."""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from sqlmodel import Field


class AIVisitNoteVersionAudit(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("visit_note_version_audit"),
    table=True,
):
    """Pseudonymous evidence for one atomic current-version swap."""

    __table_args__ = (
        sa.CheckConstraint(
            "action = 'current_note_restored'",
            name="ck_ai_visit_note_version_audit_action",
        ),
        sa.CheckConstraint(
            "reason_code IN ("
            "'operator_selected_prior_version', "
            "'clinical_correction', "
            "'incident_recovery'"
            ")",
            name="ck_ai_visit_note_version_audit_reason_code",
        ),
        sa.CheckConstraint(
            "transition_sequence > 0",
            name="ck_ai_visit_note_version_audit_transition_sequence",
        ),
        sa.CheckConstraint(
            "appointment_ref_sha256 ~ '^[0-9a-f]{64}$' "
            "AND schema_ref_sha256 ~ '^[0-9a-f]{64}$' "
            "AND prior_current_note_ref_sha256 ~ '^[0-9a-f]{64}$' "
            "AND restored_note_ref_sha256 ~ '^[0-9a-f]{64}$' "
            "AND (restore_frontier_transcript_ref_sha256 IS NULL "
            "OR restore_frontier_transcript_ref_sha256 ~ '^[0-9a-f]{64}$') "
            "AND plan_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_ai_visit_note_version_audit_digests",
        ),
        sa.Index(
            "uq_ai_visit_note_version_audit_plan_sha256",
            "plan_sha256",
            unique=True,
        ),
        sa.Index(
            "uq_ai_visit_note_version_audit_family_sequence",
            "appointment_ref_sha256",
            "schema_ref_sha256",
            "transition_sequence",
            unique=True,
        ),
    )

    action: str = Field(default="current_note_restored", max_length=64)
    operator_ref: str = Field(max_length=255)
    reason_code: str = Field(max_length=64)
    transition_sequence: int
    effective_at: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    appointment_ref_sha256: str = Field(min_length=64, max_length=64)
    schema_ref_sha256: str = Field(min_length=64, max_length=64)
    prior_current_note_ref_sha256: str = Field(min_length=64, max_length=64)
    restored_note_ref_sha256: str = Field(min_length=64, max_length=64)
    restore_frontier_transcript_ref_sha256: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    plan_sha256: str = Field(min_length=64, max_length=64)
