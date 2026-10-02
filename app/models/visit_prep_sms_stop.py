"""Terminal sender-wide SMS suppression, independent of preparation lifetime."""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from sqlmodel import Column, Field


class VisitPrepSMSStopEvent(BaseModel, TypeIDMixin("visit_prep_sms_stop"), table=True):
    __table_args__ = (
        sa.UniqueConstraint(
            "sender_digest", "source_event_digest", name="uq_visit_prep_sms_stop_source"
        ),
        sa.Index(
            "ix_visit_prep_sms_stop_recipient", "sender_digest", "recipient_digest"
        ),
        sa.CheckConstraint(
            "sender_digest ~ '^[0-9a-f]{64}$' AND recipient_digest ~ '^[0-9a-f]{64}$' "
            "AND source_event_digest ~ '^[0-9a-f]{64}$' AND source_evidence_hmac ~ '^[0-9a-f]{64}$' "
            "AND payload_hmac ~ '^[0-9a-f]{64}$'",
            name="digest_shape",
        ),
        sa.CheckConstraint("occurred_at <= recorded_at", name="time_shape"),
        sa.CheckConstraint(
            "envelope_format_version = 1 AND octet_length(evidence_ciphertext) BETWEEN 1 AND 4096",
            name="evidence_shape",
        ),
    )
    sender_digest: str = Field(max_length=64, repr=False)
    recipient_digest: str = Field(max_length=64, repr=False)
    source_event_digest: str = Field(max_length=64, repr=False)
    source_evidence_hmac: str = Field(max_length=64, repr=False)
    payload_hmac: str = Field(max_length=64, repr=False)
    evidence_ciphertext: str = Field(
        sa_column=Column(sa.Text(), nullable=False), repr=False
    )
    envelope_format_version: int = Field(default=1)
    occurred_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
    recorded_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )


def _deny_mutation(*_):
    raise RuntimeError("Visit Prep SMS STOP evidence is immutable.")


sa.event.listen(VisitPrepSMSStopEvent, "before_update", _deny_mutation)
sa.event.listen(VisitPrepSMSStopEvent, "before_delete", _deny_mutation)
