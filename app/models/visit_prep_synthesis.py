"""Dormant encrypted, immutable Visit Prep briefs; no model or runtime writes.

Each artifact binds an exact patient-source and generation profile. The payload
contains the brief and selected-source snapshot, encrypted before persistence.
Services must authorize and revalidate current sources before publication/read;
database lineage checks alone do not grant disclosure or prove clinical facts.
Retention and legal holds belong to the parent Visit Prep aggregate.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class VisitPrepSynthesisArtifact(
    BaseModel, TimestampsMixin, TypeIDMixin("visit_prep_brief"), table=True
):
    __table_args__ = (
        sa.CheckConstraint("version > 0", name="version_positive"),
        sa.CheckConstraint(
            "source_version ~ '^[0-9a-f]{64}$'", name="source_version_shape"
        ),
        sa.CheckConstraint(
            "generator_version ~ '^[a-z][a-z0-9_]{0,63}$' AND "
            "prompt_version ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="generation_versions_shape",
        ),
        sa.CheckConstraint(
            "model_profile ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name="model_profile_shape",
        ),
        sa.CheckConstraint("credential_generation > 0", name="credential_positive"),
        sa.CheckConstraint(
            "basis_response_count BETWEEN 0 AND 5 AND basis_event_sequence >= 0 "
            "AND (basis_response_count > 0 OR basis_event_sequence = 0)",
            name="basis_shape",
        ),
        sa.CheckConstraint(
            "actor_role IN ('patient', 'caregiver')", name="actor_role_allowed"
        ),
        sa.CheckConstraint(
            "current_answers_reviewed IS FALSE OR current_answers_complete IS TRUE",
            name="review_requires_complete",
        ),
        sa.CheckConstraint(
            "payload_format_version = 1 AND "
            "octet_length(payload_ciphertext) BETWEEN 1 AND 65536",
            name="payload_shape",
        ),
        sa.CheckConstraint("recorded_at >= created_at", name="recorded_at_order"),
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "owner_practice_member_id", "question_set_version_id"],
            [
                "visit_prep.id",
                "visit_prep.owner_practice_member_id",
                "visit_prep.question_set_version_id",
            ],
            name="fk_visit_prep_brief_root",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_generation_id", "visit_prep_id", "owner_practice_member_id"],
            [
                "visit_prep_schedule_generation.id",
                "visit_prep_schedule_generation.visit_prep_id",
                "visit_prep_schedule_generation.owner_practice_member_id",
            ],
            name="fk_visit_prep_brief_schedule",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["visit_prep_id", "credential_generation"],
            [
                "visit_prep_credential_generation.visit_prep_id",
                "visit_prep_credential_generation.generation",
            ],
            name="fk_visit_prep_brief_credential",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "visit_prep_id", "version", name="uq_visit_prep_brief_version"
        ),
        sa.UniqueConstraint(
            "visit_prep_id",
            "source_version",
            "generator_version",
            "prompt_version",
            "model_profile",
            name="uq_visit_prep_brief_source_profile",
        ),
    )

    visit_prep_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep"), nullable=False)
    )
    owner_practice_member_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("practice_member"), nullable=False)
    )
    question_set_version_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_question_set_version"), nullable=False)
    )
    schedule_generation_id: TypeIDType = Field(
        sa_column=Column(TypeIDType("visit_prep_schedule"), nullable=False)
    )
    credential_generation: int
    version: int
    source_version: str = Field(max_length=64)
    basis_response_count: int
    basis_event_sequence: int
    actor_role: str = Field(max_length=16)
    original_interview_completed: bool
    current_answers_complete: bool
    current_answers_reviewed: bool
    generator_version: str = Field(max_length=64)
    prompt_version: str = Field(max_length=64)
    model_profile: str = Field(max_length=128)
    payload_ciphertext: str = Field(
        repr=False, exclude=True, sa_column=Column(sa.Text(), nullable=False)
    )
    payload_format_version: int = Field(default=1)
    recorded_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
        sa_column_kwargs={"server_default": sa.func.now()},
        nullable=False,
    )


def _prevent_artifact_update(*_: object) -> None:
    raise RuntimeError("Visit Prep synthesis artifacts are immutable.")


def _prevent_artifact_delete(*_: object) -> None:
    raise RuntimeError(
        "Visit Prep synthesis artifacts delete only with aggregate cleanup."
    )


sa.event.listen(VisitPrepSynthesisArtifact, "before_update", _prevent_artifact_update)
sa.event.listen(VisitPrepSynthesisArtifact, "before_delete", _prevent_artifact_delete)
