"""add occurrence-bound Zoom capture authorization

Revision ID: e4b9a63f210d
Revises: a81c9d7e4f20
Create Date: 2026-08-21 18:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e4b9a63f210d"
down_revision: Union[str, None] = "a81c9d7e4f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "zoom_meeting",
        "appointment_id",
        existing_type=sa.UUID(),
        comment="Soft reference to Appointment retained for schedule-to-note navigation",
        existing_comment=(
            "Soft reference to Appointment — no FK constraint intentional: "
            "appointment is set async via webhook after meeting starts, and may "
            "not exist yet when the ZoomMeeting row is created"
        ),
    )
    op.add_column(
        "zoom_meeting",
        sa.Column(
            "capture_authorization_occurrence_digest",
            sa.String(length=64),
            nullable=True,
            comment=(
                "HMAC digest of the one Zoom occurrence authorized to capture "
                "this appointment"
            ),
        ),
    )
    op.add_column(
        "zoom_meeting",
        sa.Column(
            "capture_authorization_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Deadline after which this capture authorization fails closed",
        ),
    )
    op.add_column(
        "zoom_meeting",
        sa.Column(
            "capture_authorization_consumed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Set atomically before the authorized occurrence creates a Recall bot"
            ),
        ),
    )
    op.create_check_constraint(
        "ck_zoom_meeting_capture_authorization_occurrence_digest",
        "zoom_meeting",
        "capture_authorization_occurrence_digest IS NULL OR "
        "capture_authorization_occurrence_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_zoom_meeting_capture_authorization_complete",
        "zoom_meeting",
        "(capture_authorization_occurrence_digest IS NULL AND "
        "capture_authorization_expires_at IS NULL AND "
        "capture_authorization_consumed_at IS NULL) OR "
        "(appointment_id IS NOT NULL AND "
        "capture_authorization_occurrence_digest IS NOT NULL AND "
        "capture_authorization_expires_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_zoom_meeting_capture_authorization_complete",
        "zoom_meeting",
        type_="check",
    )
    op.drop_constraint(
        "ck_zoom_meeting_capture_authorization_occurrence_digest",
        "zoom_meeting",
        type_="check",
    )
    op.drop_column("zoom_meeting", "capture_authorization_consumed_at")
    op.drop_column("zoom_meeting", "capture_authorization_expires_at")
    op.drop_column("zoom_meeting", "capture_authorization_occurrence_digest")
    op.alter_column(
        "zoom_meeting",
        "appointment_id",
        existing_type=sa.UUID(),
        comment=(
            "Soft reference to Appointment — no FK constraint intentional: "
            "appointment is set async via webhook after meeting starts, and may "
            "not exist yet when the ZoomMeeting row is created"
        ),
        existing_comment=(
            "Soft reference to Appointment retained for schedule-to-note navigation"
        ),
    )
