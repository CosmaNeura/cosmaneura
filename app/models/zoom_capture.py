"""Provider-scoped Zoom room, occurrence, encounter, and segment state."""

from datetime import datetime

from app.utils.encryption import EncryptedType

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from sqlmodel import Column, Field


class ZoomClinicalRoom(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("zoom_room"),
    table=True,
):
    """The one Zoom room a provider explicitly designates for auto-capture."""

    __table_args__ = (
        sa.UniqueConstraint(
            "id",
            "doctor_id",
            "zoom_meeting_id_digest",
            name="uq_zoom_clinical_room_scope",
        ),
        sa.CheckConstraint(
            "zoom_meeting_id_digest ~ '^[0-9a-f]{64}$'",
            name="ck_zoom_clinical_room_meeting_digest",
        ),
        sa.CheckConstraint(
            "(state = 'active' AND disabled_at IS NULL) OR "
            "(state = 'disabled' AND disabled_at IS NOT NULL "
            "AND disabled_at >= designated_at)",
            name="ck_zoom_clinical_room_lifecycle",
        ),
        sa.Index(
            "uq_zoom_clinical_room_active_doctor",
            "doctor_id",
            unique=True,
            postgresql_where=sa.text("state = 'active'"),
        ),
        sa.Index(
            "uq_zoom_clinical_room_active_meeting",
            "zoom_meeting_id_digest",
            unique=True,
            postgresql_where=sa.text("state = 'active'"),
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    zoom_meeting_id_ciphertext: str = Field(
        sa_column=Column(EncryptedType(), nullable=False),
        exclude=True,
        repr=False,
    )
    zoom_meeting_id_digest: str = Field(min_length=64, max_length=64)
    state: str = Field(default="active", max_length=16)
    designated_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    disabled_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class ZoomOccurrence(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("zoom_occurrence"),
    table=True,
):
    """One Zoom occurrence, distinct from its reusable meeting-room number."""

    __table_args__ = (
        sa.UniqueConstraint(
            "id",
            "doctor_id",
            name="uq_zoom_occurrence_id_doctor_id",
        ),
        sa.UniqueConstraint(
            "occurrence_digest",
            name="uq_zoom_occurrence_digest",
        ),
        sa.ForeignKeyConstraint(
            ["clinical_room_id", "doctor_id", "zoom_meeting_id_digest"],
            [
                "zoom_clinical_room.id",
                "zoom_clinical_room.doctor_id",
                "zoom_clinical_room.zoom_meeting_id_digest",
            ],
            name="fk_zoom_occurrence_room_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["video_integration_id"],
            ["doctor_video_integration.id"],
            name="fk_zoom_occurrence_video_integration",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "bot_state IN "
            "('not_requested', 'starting', 'joining', 'ready', 'stopping', "
            "'stopped', 'uncertain', 'failed')",
            name="ck_zoom_occurrence_bot_state",
        ),
        sa.CheckConstraint(
            "zoom_host_id_digest ~ '^[0-9a-f]{64}$' "
            "AND occurrence_digest ~ '^[0-9a-f]{64}$' "
            "AND zoom_meeting_id_digest ~ '^[0-9a-f]{64}$'",
            name="ck_zoom_occurrence_digests",
        ),
        sa.CheckConstraint(
            "version > 0 AND bot_generation >= 0",
            name="ck_zoom_occurrence_versions",
        ),
        sa.CheckConstraint(
            "(lifecycle_state = 'active' AND ended_at IS NULL) OR "
            "(lifecycle_state = 'ended' AND ended_at IS NOT NULL "
            "AND ended_at >= started_at)",
            name="ck_zoom_occurrence_lifecycle",
        ),
        sa.CheckConstraint(
            "recall_bot_id_ciphertext IS NULL "
            "OR COALESCE(recall_bot_id_digest ~ '^[0-9a-f]{64}$', FALSE)",
            name="ck_zoom_occurrence_bot_ciphertext_digest",
        ),
        sa.CheckConstraint(
            "recall_bot_id_digest IS NULL OR recall_bot_id_digest ~ '^[0-9a-f]{64}$'",
            name="ck_zoom_occurrence_bot_digest",
        ),
        sa.CheckConstraint(
            "recall_bot_id_purged_at IS NULL OR "
            "(recall_bot_id_ciphertext IS NULL "
            "AND recall_bot_id_digest IS NOT NULL)",
            name="ck_zoom_occurrence_bot_purge",
        ),
        sa.Index(
            "uq_zoom_occurrence_active_meeting",
            "zoom_meeting_id_digest",
            unique=True,
            postgresql_where=sa.text("lifecycle_state = 'active'"),
        ),
        sa.Index(
            "uq_zoom_occurrence_recall_bot_digest",
            "recall_bot_id_digest",
            unique=True,
            postgresql_where=sa.text("recall_bot_id_digest IS NOT NULL"),
        ),
        sa.Index(
            "ix_zoom_occurrence_doctor_started_at",
            "doctor_id",
            "started_at",
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    video_integration_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("dvint"),  # type: ignore
    )
    clinical_room_id: TypeIDType = Field(sa_type=TypeIDType("zoom_room"))  # type: ignore
    zoom_meeting_id_digest: str = Field(min_length=64, max_length=64)
    zoom_host_id_digest: str = Field(min_length=64, max_length=64)
    occurrence_digest: str = Field(min_length=64, max_length=64)
    lifecycle_state: str = Field(default="active", max_length=16)
    bot_state: str = Field(default="not_requested", max_length=24)
    version: int = Field(default=1, ge=1)
    bot_generation: int = Field(default=0, ge=0)
    started_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    ended_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    recall_bot_id_ciphertext: str | None = Field(
        default=None,
        sa_column=Column(EncryptedType(), nullable=True),
        exclude=True,
        repr=False,
    )
    recall_bot_id_digest: str | None = Field(default=None, max_length=64)
    recall_bot_id_purged_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class ZoomCaptureSession(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("zoom_capture"),
    table=True,
):
    """One clinical encounter inside a Zoom occurrence."""

    __table_args__ = (
        sa.UniqueConstraint(
            "occurrence_id",
            "sequence",
            name="uq_zoom_capture_occurrence_sequence",
        ),
        sa.UniqueConstraint(
            "id",
            "doctor_id",
            name="uq_zoom_capture_id_doctor_id",
        ),
        sa.UniqueConstraint(
            "id",
            "doctor_id",
            "appointment_id",
            "patient_id",
            name="uq_zoom_capture_assignment_scope",
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id", "doctor_id"],
            ["zoom_occurrence.id", "zoom_occurrence.doctor_id"],
            name="fk_zoom_capture_occurrence_doctor",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "sequence > 0 AND version > 0",
            name="ck_zoom_capture_versions",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_zoom_capture_time_order",
        ),
        sa.CheckConstraint(
            "(lifecycle_state = 'open' AND ended_at IS NULL) OR "
            "(lifecycle_state IN ('closed', 'failed') AND ended_at IS NOT NULL)",
            name="ck_zoom_capture_lifecycle",
        ),
        sa.CheckConstraint(
            "(assignment_state = 'unassigned' AND appointment_id IS NULL "
            "AND patient_id IS NULL AND assignment_basis IS NULL "
            "AND assigned_at IS NULL) OR "
            "(assignment_state = 'assigned' AND appointment_id IS NOT NULL "
            "AND patient_id IS NOT NULL AND assignment_basis IN "
            "('scheduled_exact', 'provider_confirmed', 'upstream_exact') "
            "AND assigned_at IS NOT NULL)",
            name="ck_zoom_capture_assignment",
        ),
        sa.Index(
            "uq_zoom_capture_open_occurrence",
            "occurrence_id",
            unique=True,
            postgresql_where=sa.text("lifecycle_state = 'open'"),
        ),
        sa.Index(
            "ix_zoom_capture_doctor_assignment_started",
            "doctor_id",
            "assignment_state",
            "started_at",
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    occurrence_id: TypeIDType = Field(sa_type=TypeIDType("zoom_occurrence"))  # type: ignore
    sequence: int
    lifecycle_state: str = Field(default="open", max_length=16)
    assignment_state: str = Field(default="unassigned", max_length=16)
    version: int = Field(default=1, ge=1)
    started_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    ended_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    appointment_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("appointment"),  # type: ignore
        foreign_key="appointment.id",
    )
    patient_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("patient"),  # type: ignore
        foreign_key="patient.id",
    )
    assignment_basis: str | None = Field(default=None, max_length=32)
    assigned_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class ZoomCaptureSegment(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("zoom_segment"),
    table=True,
):
    """One uninterrupted Recall recording inside a capture session."""

    __table_args__ = (
        sa.UniqueConstraint(
            "capture_session_id",
            "sequence",
            name="uq_zoom_capture_segment_session_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["capture_session_id", "doctor_id"],
            ["zoom_capture_session.id", "zoom_capture_session.doctor_id"],
            name="fk_zoom_capture_segment_session_doctor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["capture_session_id", "doctor_id", "appointment_id", "patient_id"],
            [
                "zoom_capture_session.id",
                "zoom_capture_session.doctor_id",
                "zoom_capture_session.appointment_id",
                "zoom_capture_session.patient_id",
            ],
            name="fk_zoom_capture_segment_assignment_scope",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "sequence > 0 AND version > 0",
            name="ck_zoom_capture_segment_versions",
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_zoom_capture_segment_duration",
        ),
        sa.CheckConstraint(
            "stopped_at IS NULL OR stopped_at >= started_at",
            name="ck_zoom_capture_segment_time_order",
        ),
        sa.CheckConstraint(
            "transcript_content_expires_at IS NULL "
            "OR transcript_content_expires_at > started_at",
            name="ck_zoom_capture_segment_content_expiry_order",
        ),
        sa.CheckConstraint(
            "(transport_state IN ('starting', 'recording', 'stopping', 'uncertain') "
            "AND stopped_at IS NULL) OR "
            "(transport_state IN ('processing', 'complete', 'failed') "
            "AND stopped_at IS NOT NULL)",
            name="ck_zoom_capture_segment_transport_lifecycle",
        ),
        sa.CheckConstraint(
            "recall_recording_id_ciphertext IS NULL "
            "OR COALESCE(recall_recording_id_digest ~ '^[0-9a-f]{64}$', FALSE)",
            name="ck_zoom_capture_segment_recording_ciphertext_digest",
        ),
        sa.CheckConstraint(
            "recall_recording_id_digest IS NULL "
            "OR recall_recording_id_digest ~ '^[0-9a-f]{64}$'",
            name="ck_zoom_capture_segment_recording_digest",
        ),
        sa.CheckConstraint(
            "recall_recording_id_purged_at IS NULL OR "
            "(recall_recording_id_ciphertext IS NULL "
            "AND recall_recording_id_digest IS NOT NULL)",
            name="ck_zoom_capture_segment_recording_purge",
        ),
        sa.CheckConstraint(
            "recall_transcript_id_ciphertext IS NULL "
            "OR COALESCE(recall_transcript_id_digest ~ '^[0-9a-f]{64}$', FALSE)",
            name="ck_zoom_capture_segment_transcript_ciphertext_digest",
        ),
        sa.CheckConstraint(
            "recall_transcript_id_digest IS NULL "
            "OR recall_transcript_id_digest ~ '^[0-9a-f]{64}$'",
            name="ck_zoom_capture_segment_transcript_digest",
        ),
        sa.CheckConstraint(
            "recall_transcript_id_purged_at IS NULL OR "
            "(recall_transcript_id_ciphertext IS NULL "
            "AND recall_transcript_id_digest IS NOT NULL)",
            name="ck_zoom_capture_segment_transcript_purge",
        ),
        sa.CheckConstraint(
            "transcript_content_digest IS NULL "
            "OR transcript_content_digest ~ '^[0-9a-f]{64}$'",
            name="ck_zoom_capture_segment_pending_digest",
        ),
        sa.CheckConstraint(
            "(content_state = 'none' AND transcript_content_ciphertext IS NULL "
            "AND transcript_content_digest IS NULL "
            "AND transcript_content_expires_at IS NULL "
            "AND transcript_content_purged_at IS NULL "
            "AND result_transcript_id IS NULL "
            "AND appointment_id IS NULL AND patient_id IS NULL) OR "
            "(content_state = 'pending_assignment' "
            "AND transcript_content_ciphertext IS NOT NULL "
            "AND transcript_content_digest IS NOT NULL "
            "AND transcript_content_expires_at IS NOT NULL "
            "AND transcript_content_purged_at IS NULL "
            "AND result_transcript_id IS NULL "
            "AND appointment_id IS NULL AND patient_id IS NULL) OR "
            "(content_state = 'canonical' "
            "AND transcript_content_ciphertext IS NULL "
            "AND transcript_content_digest IS NOT NULL "
            "AND transcript_content_expires_at IS NOT NULL "
            "AND transcript_content_purged_at IS NOT NULL "
            "AND result_transcript_id IS NOT NULL "
            "AND appointment_id IS NOT NULL AND patient_id IS NOT NULL) OR "
            "(content_state = 'purged' "
            "AND transcript_content_ciphertext IS NULL "
            "AND transcript_content_digest IS NOT NULL "
            "AND transcript_content_expires_at IS NOT NULL "
            "AND transcript_content_purged_at IS NOT NULL "
            "AND result_transcript_id IS NULL "
            "AND appointment_id IS NULL AND patient_id IS NULL)",
            name="ck_zoom_capture_segment_content_lifecycle",
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN "
            "('bot_create_uncertain', 'recording_start_uncertain', "
            "'recording_stop_uncertain', 'transcript_request_uncertain', "
            "'transcript_fetch_failed', 'assignment_expired', 'provider_error')",
            name="ck_zoom_capture_segment_error_code",
        ),
        sa.Index(
            "uq_zoom_capture_segment_active_session",
            "capture_session_id",
            unique=True,
            postgresql_where=sa.text(
                "transport_state IN ('starting', 'recording', 'stopping') OR "
                "(transport_state = 'uncertain' AND stopped_at IS NULL)"
            ),
        ),
        sa.Index(
            "uq_zoom_capture_segment_recording_digest",
            "recall_recording_id_digest",
            unique=True,
            postgresql_where=sa.text("recall_recording_id_digest IS NOT NULL"),
        ),
        sa.Index(
            "uq_zoom_capture_segment_transcript_digest",
            "recall_transcript_id_digest",
            unique=True,
            postgresql_where=sa.text("recall_transcript_id_digest IS NOT NULL"),
        ),
        sa.Index(
            "ix_zoom_capture_segment_content_expiry",
            "content_state",
            "transcript_content_expires_at",
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    capture_session_id: TypeIDType = Field(sa_type=TypeIDType("zoom_capture"))  # type: ignore
    sequence: int
    transport_state: str = Field(default="starting", max_length=24)
    content_state: str = Field(default="none", max_length=24)
    version: int = Field(default=1, ge=1)
    started_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    stopped_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )

    recall_recording_id_ciphertext: str | None = Field(
        default=None,
        sa_column=Column(EncryptedType(), nullable=True),
        exclude=True,
        repr=False,
    )
    recall_recording_id_digest: str | None = Field(default=None, max_length=64)
    recall_recording_id_purged_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    recall_transcript_id_ciphertext: str | None = Field(
        default=None,
        sa_column=Column(EncryptedType(), nullable=True),
        exclude=True,
        repr=False,
    )
    recall_transcript_id_digest: str | None = Field(default=None, max_length=64)
    recall_transcript_id_purged_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )

    transcript_content_ciphertext: str | None = Field(
        default=None,
        sa_column=Column(EncryptedType(), nullable=True),
        exclude=True,
        repr=False,
    )
    transcript_content_digest: str | None = Field(default=None, max_length=64)
    transcript_content_expires_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    transcript_content_purged_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    duration_seconds: int | None = Field(default=None, ge=0)

    appointment_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("appointment"),  # type: ignore
        foreign_key="appointment.id",
    )
    patient_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("patient"),  # type: ignore
        foreign_key="patient.id",
    )
    result_transcript_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("ai_visit_transcript"),  # type: ignore
        foreign_key="ai_visit_transcript.id",
    )
    last_error_code: str | None = Field(default=None, max_length=64)
