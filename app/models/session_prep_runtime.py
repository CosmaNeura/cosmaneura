"""Core-owned durable Session Prep artifacts, build leases, and read receipts."""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from app.models.authority import InteractionLineage
from app.models.provider_record_authority import ProviderRecordAuthority
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field


class SessionPrepArtifact(BaseModel, TypeIDMixin("prep_artifact"), table=True):
    __table_args__ = (
        sa.UniqueConstraint(
            "record_scope_id", "version", name="uq_prep_artifact_version"
        ),
        sa.UniqueConstraint(
            "record_scope_id", "source_digest", name="uq_prep_artifact_source"
        ),
        sa.CheckConstraint("version > 0", name="ck_prep_artifact_version"),
    )
    record_scope_id: TypeIDType = ProviderRecordAuthority.foreign_key(index=True)
    version: int = Field(ge=1)
    source_digest: str = Field(min_length=64, max_length=64)
    source_manifest: list[dict[str, Any]] = Field(
        sa_type=JSONB, exclude=True, repr=False
    )
    result_payload: dict[str, Any] = Field(sa_type=JSONB, exclude=True, repr=False)
    generated_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore


class SessionPrepBuild(BaseModel, TypeIDMixin("prep_build"), table=True):
    __table_args__ = (
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'ready', 'failed')",
            name="ck_prep_build_state",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= 3", name="ck_prep_build_attempts"
        ),
    )
    record_scope_id: TypeIDType = ProviderRecordAuthority.foreign_key(unique=True)
    desired_digest: str = Field(min_length=64, max_length=64)
    state: str = Field(default="pending", max_length=16, index=True)
    attempts: int = Field(default=0)
    requested_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    retry_at: datetime = Field(index=True, sa_type=sa.DateTime(timezone=True))  # type: ignore
    lease_until: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )
    failure_code: str | None = Field(default=None, max_length=32)


class SessionPrepReadReceipt(BaseModel, TypeIDMixin("prep_read"), table=True):
    __table_args__ = (
        sa.UniqueConstraint(
            "workload_client_id",
            "interaction_id",
            "idempotency_key",
            name="uq_prep_read_retry",
        ),
    )
    workload_client_id: str = Field(max_length=64)
    interaction_id: TypeIDType = InteractionLineage.foreign_key(index=True)
    idempotency_key: str = Field(min_length=16, max_length=128)
    request_digest: str = Field(min_length=64, max_length=64)
    record_scope_id: TypeIDType = ProviderRecordAuthority.foreign_key(index=True)
    artifact_id: TypeIDType | None = SessionPrepArtifact.foreign_key(
        default=None, nullable=True
    )
    response_payload: dict[str, Any] = Field(sa_type=JSONB, exclude=True, repr=False)
    recorded_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    retain_until: datetime = Field(index=True, sa_type=sa.DateTime(timezone=True))  # type: ignore
