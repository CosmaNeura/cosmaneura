"""Canonical platform-owned conversation turns for the Agent API contract."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from app.models.authority import (
    AuthorityActor,
    AuthorityPerson,
    AuthorityTenant,
    InteractionLineage,
)
from sqlmodel import Field, UniqueConstraint


class CanonicalConversationTurn(
    BaseModel,
    TypeIDMixin("conversation_turn"),
    table=True,
):
    """Immutable scope anchor shared by both canonical turn phases."""

    interaction_id: TypeIDType = InteractionLineage.foreign_key(
        unique=True,
        index=True,
    )
    workload_client_id: str = Field(max_length=64)
    client_turn_id: UUID = Field(unique=True, index=True)
    tenant_id: TypeIDType | None = AuthorityTenant.foreign_key(
        default=None,
        nullable=True,
    )
    actor_id: TypeIDType | None = AuthorityActor.foreign_key(
        default=None,
        nullable=True,
    )
    person_id: TypeIDType | None = AuthorityPerson.foreign_key(
        default=None,
        nullable=True,
    )
    created_at: datetime = Field(
        index=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class CanonicalConversationUserPhase(
    BaseModel,
    TypeIDMixin("conversation_user_phase"),
    table=True,
):
    """Append-only verbatim ingress accepted for one canonical turn."""

    __table_args__ = (
        UniqueConstraint(
            "turn_id",
            name="uq_canonical_conversation_user_phase_turn",
        ),
        sa.CheckConstraint(
            "ingress_kind IN ('chat_text', 'provider_ui_action')",
            name="ck_canonical_conversation_user_phase_ingress_kind",
        ),
        sa.CheckConstraint(
            "(ingress_kind = 'chat_text' AND action_code IS NULL) OR "
            "(ingress_kind = 'provider_ui_action' AND action_code IS NOT NULL)",
            name="ck_canonical_conversation_user_phase_action_shape",
        ),
        sa.CheckConstraint(
            "content_type = 'text/plain'",
            name="ck_canonical_conversation_user_phase_content_type",
        ),
        sa.CheckConstraint(
            "length(content) BETWEEN 1 AND 20000",
            name="ck_canonical_conversation_user_phase_content_length",
        ),
    )

    turn_id: TypeIDType = CanonicalConversationTurn.foreign_key(index=True)
    ingress_kind: str = Field(max_length=32)
    action_code: str | None = Field(default=None, nullable=True, max_length=128)
    content_type: str = Field(max_length=32)
    content: str = Field(min_length=1, max_length=20_000)
    request_digest: str = Field(min_length=64, max_length=64)
    recorded_at: datetime = Field(
        index=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class CanonicalConversationFinalPhase(
    BaseModel,
    TypeIDMixin("conversation_final_phase"),
    table=True,
):
    """Append-only exact response and presentation for one accepted turn."""

    __table_args__ = (
        UniqueConstraint(
            "turn_id",
            name="uq_canonical_conversation_final_phase_turn",
        ),
        sa.CheckConstraint(
            "outcome IN ('completed', 'refused', 'unavailable')",
            name="ck_canonical_conversation_final_phase_outcome",
        ),
        sa.CheckConstraint(
            "content_type = 'text/plain'",
            name="ck_canonical_conversation_final_phase_content_type",
        ),
        sa.CheckConstraint(
            "length(content) BETWEEN 1 AND 20000",
            name="ck_canonical_conversation_final_phase_content_length",
        ),
        sa.CheckConstraint(
            "presentation_mode IN ('standard', 'emergency', 'sensitive_disclosure')",
            name="ck_canonical_conversation_final_phase_presentation_mode",
        ),
        sa.CheckConstraint(
            "(human_contact_label IS NULL AND human_contact_action_id IS NULL "
            "AND human_contact_fallback_text IS NULL) OR "
            "(human_contact_label IS NOT NULL "
            "AND human_contact_action_id IS NOT NULL "
            "AND human_contact_fallback_text IS NOT NULL)",
            name="ck_canonical_conversation_final_phase_contact_shape",
        ),
        sa.CheckConstraint(
            "human_contact_label IS NULL OR "
            "(length(human_contact_label) BETWEEN 1 AND 120 "
            "AND length(human_contact_action_id) BETWEEN 8 AND 128 "
            "AND length(human_contact_fallback_text) BETWEEN 1 AND 240)",
            name="ck_canonical_conversation_final_phase_contact_length",
        ),
        sa.CheckConstraint(
            "presentation_mode <> 'sensitive_disclosure' OR "
            "(suppress_disclaimer AND suppress_intent_badge "
            "AND suppress_doctrinal_badge AND suppress_citations "
            "AND suppress_structured_cards AND suppress_feature_actions "
            "AND suppress_guided_paths "
            "AND human_contact_label IS NOT NULL "
            "AND human_contact_action_id IS NOT NULL "
            "AND human_contact_fallback_text IS NOT NULL)",
            name="ck_canonical_conversation_final_phase_sensitive_shape",
        ),
    )

    # This foreign key targets the unique accepted-phase turn key, rather than
    # the turn table directly, so the database cannot manufacture a final phase
    # without a durably accepted user phase.
    turn_id: TypeIDType = Field(  # type: ignore
        index=True,
        sa_type=TypeIDType("conversation_turn"),  # type: ignore
        foreign_key="canonical_conversation_user_phase.turn_id",
    )
    outcome: str = Field(max_length=16)
    content_type: str = Field(max_length=32)
    content: str = Field(min_length=1, max_length=20_000)
    presentation_mode: str = Field(max_length=32)
    suppress_disclaimer: bool
    suppress_intent_badge: bool
    suppress_doctrinal_badge: bool
    suppress_citations: bool
    suppress_structured_cards: bool
    suppress_feature_actions: bool
    suppress_guided_paths: bool
    human_contact_label: str | None = Field(
        default=None,
        nullable=True,
        max_length=120,
    )
    human_contact_action_id: str | None = Field(
        default=None,
        nullable=True,
        max_length=128,
    )
    human_contact_fallback_text: str | None = Field(
        default=None,
        nullable=True,
        max_length=240,
    )
    request_digest: str = Field(min_length=64, max_length=64)
    recorded_at: datetime = Field(
        index=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


def _prevent_canonical_conversation_update(*_: object) -> None:
    raise RuntimeError("Canonical conversation records are append-only.")


def _prevent_canonical_conversation_delete(*_: object) -> None:
    raise RuntimeError("Canonical conversation records are append-only.")


for append_only_model in (
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
    CanonicalConversationFinalPhase,
):
    sa.event.listen(
        append_only_model,
        "before_update",
        _prevent_canonical_conversation_update,
    )
    sa.event.listen(
        append_only_model,
        "before_delete",
        _prevent_canonical_conversation_delete,
    )
