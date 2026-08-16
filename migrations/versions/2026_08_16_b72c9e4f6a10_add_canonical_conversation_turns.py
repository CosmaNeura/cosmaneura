"""add_canonical_conversation_turns

Revision ID: b72c9e4f6a10
Revises: a61c2f1e9b44
Create Date: 2026-08-16 02:00:00.000000
"""

from collections.abc import Sequence

import activemodel
import sqlalchemy as sa
from alembic import op


revision: str = "b72c9e4f6a10"
down_revision: str | None = "a61c2f1e9b44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id(prefix: str) -> sa.Column:
    return sa.Column(
        "id",
        activemodel.types.typeid.TypeIDType(prefix=prefix),
        nullable=False,
        comment=f"TypeID with prefix: {prefix}",
    )


def upgrade() -> None:
    # Match the ratified scope: the same random key may occur in another
    # interaction, while the separate logical-operation constraint still
    # guarantees one phase effect per interaction.
    op.drop_constraint(
        "uq_authority_idempotency_scope_key",
        "authority_idempotency_record",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_authority_idempotency_scope_key",
        "authority_idempotency_record",
        ["workload_client_id", "operation", "interaction_id", "idempotency_key"],
    )

    op.create_table(
        "canonical_conversation_turn",
        _id("conversation_turn"),
        sa.Column(
            "interaction_id",
            activemodel.types.typeid.TypeIDType(prefix="interaction"),
            nullable=False,
        ),
        sa.Column("workload_client_id", sa.String(length=64), nullable=False),
        sa.Column("client_turn_id", sa.Uuid(), nullable=False),
        sa.Column(
            "tenant_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_tenant"),
            nullable=True,
        ),
        sa.Column(
            "actor_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_actor"),
            nullable=True,
        ),
        sa.Column(
            "person_id",
            activemodel.types.typeid.TypeIDType(prefix="authority_person"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["authority_actor.id"],
            name=op.f("canonical_conversation_turn_actor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["interaction_id"],
            ["interaction_lineage.id"],
            name=op.f("canonical_conversation_turn_interaction_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["authority_person.id"],
            name=op.f("canonical_conversation_turn_person_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["authority_tenant.id"],
            name=op.f("canonical_conversation_turn_tenant_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("canonical_conversation_turn_pkey")),
        comment="Immutable scope anchor shared by both canonical turn phases.",
    )
    op.create_index(
        op.f("canonical_conversation_turn_client_turn_id_idx"),
        "canonical_conversation_turn",
        ["client_turn_id"],
        unique=True,
    )
    op.create_index(
        op.f("canonical_conversation_turn_created_at_idx"),
        "canonical_conversation_turn",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("canonical_conversation_turn_interaction_id_idx"),
        "canonical_conversation_turn",
        ["interaction_id"],
        unique=True,
    )

    op.create_table(
        "canonical_conversation_user_phase",
        _id("conversation_user_phase"),
        sa.Column(
            "turn_id",
            activemodel.types.typeid.TypeIDType(prefix="conversation_turn"),
            nullable=False,
        ),
        sa.Column("ingress_kind", sa.String(length=32), nullable=False),
        sa.Column("action_code", sa.String(length=128), nullable=True),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.String(length=20000), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["canonical_conversation_turn.id"],
            name=op.f("canonical_conversation_user_phase_turn_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("canonical_conversation_user_phase_pkey"),
        ),
        sa.UniqueConstraint(
            "turn_id",
            name="uq_canonical_conversation_user_phase_turn",
        ),
        comment="Append-only verbatim ingress accepted for one canonical turn.",
    )
    op.create_index(
        op.f("canonical_conversation_user_phase_recorded_at_idx"),
        "canonical_conversation_user_phase",
        ["recorded_at"],
        unique=False,
    )
    op.create_index(
        op.f("canonical_conversation_user_phase_turn_id_idx"),
        "canonical_conversation_user_phase",
        ["turn_id"],
        unique=False,
    )

    op.create_table(
        "canonical_conversation_final_phase",
        _id("conversation_final_phase"),
        sa.Column(
            "turn_id",
            activemodel.types.typeid.TypeIDType(prefix="conversation_turn"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.String(length=20000), nullable=False),
        sa.Column("presentation_mode", sa.String(length=32), nullable=False),
        sa.Column("suppress_disclaimer", sa.Boolean(), nullable=False),
        sa.Column("suppress_intent_badge", sa.Boolean(), nullable=False),
        sa.Column("suppress_doctrinal_badge", sa.Boolean(), nullable=False),
        sa.Column("suppress_citations", sa.Boolean(), nullable=False),
        sa.Column("suppress_structured_cards", sa.Boolean(), nullable=False),
        sa.Column("suppress_feature_actions", sa.Boolean(), nullable=False),
        sa.Column("suppress_guided_paths", sa.Boolean(), nullable=False),
        sa.Column("human_contact_label", sa.String(length=120), nullable=True),
        sa.Column("human_contact_action_id", sa.String(length=128), nullable=True),
        sa.Column(
            "human_contact_fallback_text",
            sa.String(length=240),
            nullable=True,
        ),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
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
            "presentation_mode IN "
            "('standard', 'emergency', 'sensitive_disclosure')",
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
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["canonical_conversation_user_phase.turn_id"],
            name=op.f("canonical_conversation_final_phase_turn_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("canonical_conversation_final_phase_pkey"),
        ),
        sa.UniqueConstraint(
            "turn_id",
            name="uq_canonical_conversation_final_phase_turn",
        ),
        comment="Append-only exact response and presentation for one accepted turn.",
    )
    op.create_index(
        op.f("canonical_conversation_final_phase_recorded_at_idx"),
        "canonical_conversation_final_phase",
        ["recorded_at"],
        unique=False,
    )
    op.create_index(
        op.f("canonical_conversation_final_phase_turn_id_idx"),
        "canonical_conversation_final_phase",
        ["turn_id"],
        unique=False,
    )

    for table_name in (
        "canonical_conversation_turn",
        "canonical_conversation_user_phase",
        "canonical_conversation_final_phase",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION authority_reject_append_only_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "canonical_conversation_final_phase",
        "canonical_conversation_user_phase",
        "canonical_conversation_turn",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS {table_name}_append_only ON {table_name}"
        )

    op.drop_table("canonical_conversation_final_phase")
    op.drop_table("canonical_conversation_user_phase")
    op.drop_table("canonical_conversation_turn")

    op.drop_constraint(
        "uq_authority_idempotency_scope_key",
        "authority_idempotency_record",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_authority_idempotency_scope_key",
        "authority_idempotency_record",
        ["workload_client_id", "operation", "idempotency_key"],
    )
