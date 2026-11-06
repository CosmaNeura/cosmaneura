"""add visit prep journey aggregate and responses
Revision ID: 69bad2e072e2
Revises: e78845cecca6
Create Date: 2026-11-06
"""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op
from activemodel.types.typeid import TypeIDType
revision = "69bad2e072e2"
down_revision = "e78845cecca6"
branch_labels = None
depends_on = None
_UUID_RE = "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
def upgrade() -> None:
    op.create_table(
        "visit_prep",
        sa.Column("id", TypeIDType("visit_prep"), nullable=False),
        sa.Column("practice_id", TypeIDType("practice"), nullable=False),
        sa.Column("owner_practice_member_id", TypeIDType("practice_member"), nullable=False),
        sa.Column("owner_doctor_id", TypeIDType("doctor"), nullable=False),
        sa.Column("question_set_version_id", TypeIDType("visit_prep_question_set_version"), nullable=False),
        sa.Column("creation_idempotency_key", sa.String(length=36), nullable=False),
        sa.Column("creation_payload_hmac_sha256", sa.String(length=64), nullable=False),
        sa.Column("recipient_snapshot_ciphertext", sa.Text(), nullable=False),
        sa.Column("has_email_contact", sa.Boolean(), nullable=False),
        sa.Column("has_sms_contact", sa.Boolean(), nullable=False),
        sa.Column("physician_display_name", sa.String(length=160), nullable=False),
        sa.Column("practice_display_name", sa.String(length=200), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=32), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone_name", sa.String(length=64), nullable=False),
        sa.Column("schedule_generation", sa.Integer(), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credential_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_role", sa.String(length=16), nullable=True),
        sa.Column("confirmation_name_digest", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "lifecycle_state IN ('invited', 'confirmed', 'in_progress', 'completed')",
            name="vp_lifecycle_state_allowed",
        ),
        sa.CheckConstraint(f"creation_idempotency_key ~ {_UUID_RE}", name="vp_creation_idempotency_key_shape"),
        sa.CheckConstraint("creation_payload_hmac_sha256 ~ '^[0-9a-f]{64}$'", name="vp_creation_payload_hmac_shape"),
        sa.CheckConstraint(
            "octet_length(recipient_snapshot_ciphertext) BETWEEN 1 AND 16384", name="vp_recipient_ciphertext_size"
        ),
        sa.CheckConstraint("has_email_contact IS TRUE OR has_sms_contact IS TRUE", name="vp_recipient_has_contact"),
        sa.CheckConstraint(
            "char_length(btrim(physician_display_name)) BETWEEN 1 AND 160", name="vp_physician_name_normalized"
        ),
        sa.CheckConstraint(
            "char_length(btrim(practice_display_name)) BETWEEN 1 AND 200", name="vp_practice_name_normalized"
        ),
        sa.CheckConstraint("credential_generation > 0", name="vp_credential_generation_positive"),
        sa.CheckConstraint("schedule_generation > 0", name="vp_schedule_generation_positive"),
        sa.ForeignKeyConstraint(["owner_practice_member_id"], ["practice_member.id"], name="fk_vp_owner_member", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["practice_id"], ["practice.id"], name="fk_vp_practice", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_doctor_id"], ["doctor.id"], name="fk_vp_owner_doctor", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["question_set_version_id", "owner_practice_member_id"],
            ["visit_prep_question_set_version.id", "visit_prep_question_set_version.owner_practice_member_id"],
            name="fk_vp_question_version_owner", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_practice_member_id", "creation_idempotency_key", name="uq_vp_owner_idempotency"),
        sa.UniqueConstraint("token_hash", name="uq_vp_token_hash"),
    )
    op.create_index("visit_prep_owner_idx", "visit_prep", ["owner_practice_member_id", "id"])
    op.create_table(
        "visit_prep_question_response",
        sa.Column("id", TypeIDType("visit_prep_response"), nullable=False),
        sa.Column("visit_prep_id", TypeIDType("visit_prep"), nullable=False),
        sa.Column("question_set_version_id", TypeIDType("visit_prep_question_set_version"), nullable=False),
        sa.Column("question_id", TypeIDType("visit_prep_question"), nullable=False),
        sa.Column("submission_sequence", sa.Integer(), nullable=False),
        sa.Column("response_kind", sa.String(length=16), nullable=False),
        sa.Column("answer_ciphertext", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=36), nullable=False),
        sa.Column("payload_hmac_sha256", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("submission_sequence BETWEEN 1 AND 5", name="vp_response_sequence_range"),
        sa.CheckConstraint("response_kind IN ('answered', 'skipped')", name="vp_response_kind_allowed"),
        sa.CheckConstraint(
            "(response_kind = 'answered' AND answer_ciphertext IS NOT NULL) OR "
            "(response_kind = 'skipped' AND answer_ciphertext IS NULL)",
            name="vp_response_answer_shape",
        ),
        sa.CheckConstraint(f"idempotency_key ~ {_UUID_RE}", name="vp_response_idempotency_shape"),
        sa.ForeignKeyConstraint(["visit_prep_id"], ["visit_prep.id"], name="fk_vp_response_prep", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["question_set_version_id", "question_id"],
            ["visit_prep_question.question_set_version_id", "visit_prep_question.id"],
            name="fk_vp_response_question", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("visit_prep_id", "question_id", name="uq_vp_response_question"),
        sa.UniqueConstraint("visit_prep_id", "submission_sequence", name="uq_vp_response_sequence"),
        sa.UniqueConstraint("visit_prep_id", "idempotency_key", name="uq_vp_response_idempotency"),
    )
def downgrade() -> None:
    op.drop_table("visit_prep_question_response")
    op.drop_index("visit_prep_owner_idx", table_name="visit_prep")
    op.drop_table("visit_prep")
