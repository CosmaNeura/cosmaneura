"""Versioned, physician-owned question definitions for Visit Prep.

This module contains configuration only. Patient-facing preps, answers,
delivery state, and generated briefs live in their own later schema layers.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel as PydanticBaseModel

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import PydanticJSONMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field


class VisitPrepQuestionChoice(PydanticBaseModel):
    """One ordered choice rendered for a choice-style question."""

    value: str
    label: str
    hint: str | None = None


class VisitPrepQuestionSet(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("visit_prep_question_set"),
    table=True,
):
    """Stable lineage for one physician-owned Visit Prep question set."""

    __table_args__ = (
        sa.CheckConstraint(
            "seed_key IS NULL OR seed_key ~ '^[a-z][a-z0-9_.-]{0,63}$'",
            name="seed_key_shape",
        ),
        sa.CheckConstraint(
            "creation_payload_sha256 IS NULL OR "
            "creation_payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="creation_payload_sha256_shape",
        ),
        sa.UniqueConstraint(
            "id",
            "owner_practice_member_id",
            name="uq_visit_prep_qset_id_owner",
        ),
        sa.UniqueConstraint(
            "owner_practice_member_id",
            "seed_key",
            name="uq_visit_prep_qset_owner_seed",
        ),
    )

    owner_practice_member_id: TypeIDType = Field(
        sa_column=Column(
            TypeIDType("practice_member"),
            sa.ForeignKey("practice_member.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    """Authoritative owner; email and domain are never authorization keys."""

    seed_key: str | None = Field(default=None, max_length=64)
    """Stable idempotency key for a system-supplied starting set."""

    creation_payload_sha256: str | None = Field(default=None, max_length=64)
    """Immutable fingerprint binding an idempotency key to its create payload."""


class VisitPrepQuestionSetVersion(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("visit_prep_question_set_version"),
    table=True,
):
    """One versioned snapshot of a Visit Prep question set."""

    __table_args__ = (
        sa.CheckConstraint(
            "version_number > 0",
            name="version_positive",
        ),
        sa.CheckConstraint(
            "name = btrim(name) AND char_length(name) BETWEEN 1 AND 120",
            name="name_normalized",
        ),
        sa.CheckConstraint(
            "supersedes_version_id IS NULL OR supersedes_version_id <> id",
            name="no_self_supersession",
        ),
        sa.CheckConstraint(
            "NOT is_default OR published_at IS NOT NULL",
            name="default_is_published",
        ),
        sa.CheckConstraint(
            "published_at IS NULL OR published_at >= created_at",
            name="publication_not_before_creation",
        ),
        sa.ForeignKeyConstraint(
            ["question_set_id", "owner_practice_member_id"],
            [
                "visit_prep_question_set.id",
                "visit_prep_question_set.owner_practice_member_id",
            ],
            name="fk_visit_prep_qset_version_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_version_id", "question_set_id"],
            [
                "visit_prep_question_set_version.id",
                "visit_prep_question_set_version.question_set_id",
            ],
            name="fk_visit_prep_qset_version_supersedes",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "question_set_id",
            "version_number",
            name="uq_visit_prep_qset_version_number",
        ),
        sa.UniqueConstraint(
            "id",
            "question_set_id",
            name="uq_visit_prep_qset_version_id_set",
        ),
        sa.UniqueConstraint(
            "id",
            "owner_practice_member_id",
            name="uq_visit_prep_qset_version_id_owner",
        ),
        sa.Index(
            "uq_visit_prep_qset_default_owner",
            "owner_practice_member_id",
            unique=True,
            postgresql_where=sa.text("is_default IS TRUE"),
        ),
        sa.Index(
            "visit_prep_qset_version_supersedes_idx",
            "supersedes_version_id",
            postgresql_where=sa.text("supersedes_version_id IS NOT NULL"),
        ),
    )

    question_set_id: TypeIDType = Field(
        sa_column=Column(
            TypeIDType("visit_prep_question_set"),
            nullable=False,
        ),
    )
    owner_practice_member_id: TypeIDType = Field(
        sa_column=Column(
            TypeIDType("practice_member"),
            nullable=False,
        ),
    )
    version_number: int
    name: str = Field(max_length=120)
    supersedes_version_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(
            TypeIDType("visit_prep_question_set_version"),
            nullable=True,
        ),
    )
    published_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    is_default: bool = Field(
        default=False,
        sa_column=Column(
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


class VisitPrepQuestion(
    BaseModel,
    PydanticJSONMixin,
    TimestampsMixin,
    TypeIDMixin("visit_prep_question"),
    table=True,
):
    """One immutable-after-publication question in an exact set version."""

    __table_args__ = (
        sa.CheckConstraint(
            "position BETWEEN 1 AND 5",
            name="position_range",
        ),
        sa.CheckConstraint(
            "question_key ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="question_key_shape",
        ),
        sa.CheckConstraint(
            "prompt_text = btrim(prompt_text) "
            "AND char_length(prompt_text) BETWEEN 1 AND 1000",
            name="prompt_normalized",
        ),
        sa.CheckConstraint(
            "helper_text IS NULL OR "
            "(helper_text = btrim(helper_text) "
            "AND char_length(helper_text) BETWEEN 1 AND 500)",
            name="helper_normalized",
        ),
        sa.CheckConstraint(
            "kind IN ('story', 'list', 'choice')",
            name="kind_allowed",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(choice_options) = 'array' AND "
            "((kind = 'choice' AND jsonb_array_length(choice_options) >= 2) "
            "OR (kind IN ('story', 'list') "
            "AND jsonb_array_length(choice_options) = 0))",
            name="choices_shape",
        ),
        sa.CheckConstraint(
            "(show_if_question_id IS NULL AND show_if_values IS NULL) OR "
            "(show_if_question_id IS NOT NULL "
            "AND show_if_values IS NOT NULL "
            "AND jsonb_typeof(show_if_values) = 'array' "
            "AND jsonb_array_length(show_if_values) > 0)",
            name="condition_complete",
        ),
        sa.CheckConstraint(
            "show_if_question_id IS NULL OR show_if_question_id <> id",
            name="condition_not_self",
        ),
        sa.ForeignKeyConstraint(
            ["question_set_version_id"],
            ["visit_prep_question_set_version.id"],
            name="fk_visit_prep_question_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_set_version_id", "show_if_question_id"],
            [
                "visit_prep_question.question_set_version_id",
                "visit_prep_question.id",
            ],
            name="fk_visit_prep_question_condition_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "question_set_version_id",
            "question_key",
            name="uq_visit_prep_question_version_key",
        ),
        sa.UniqueConstraint(
            "question_set_version_id",
            "position",
            name="uq_visit_prep_question_version_position",
        ),
        sa.UniqueConstraint(
            "question_set_version_id",
            "id",
            name="uq_visit_prep_question_version_id",
        ),
    )

    question_set_version_id: TypeIDType = Field(
        sa_column=Column(
            TypeIDType("visit_prep_question_set_version"),
            nullable=False,
        ),
    )
    question_key: str = Field(max_length=64)
    position: int
    prompt_text: str = Field(max_length=1000)
    helper_text: str | None = Field(default=None, max_length=500)
    kind: str = Field(max_length=16)
    choice_options: list[VisitPrepQuestionChoice] = Field(
        default_factory=list,
        sa_column=Column(
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    show_if_question_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(
            TypeIDType("visit_prep_question"),
            nullable=True,
        ),
    )
    show_if_values: list[str] | None = Field(
        default=None,
        sa_column=Column(
            JSONB(none_as_null=True),
            nullable=True,
        ),
    )
