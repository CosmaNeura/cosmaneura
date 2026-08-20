"""Platform-owned identity and authorization records for governed agent mounts.

These tables are intentionally additive.  Existing ``Doctor`` and ``Patient`` rows
are not authority inputs until an explicit, reviewed migration binds them here.
"""

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Field, UniqueConstraint


class AuthorityTenant(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("authority_tenant"),
    table=True,
):
    """A practice or other platform tenancy boundary."""

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'revoked')",
            name="ck_authority_tenant_status",
        ),
    )

    status: str = Field(default="active", max_length=16, index=True)


class AuthorityActor(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("authority_actor"),
    table=True,
):
    """A platform principal; never inferred from request-body identity hints."""

    __table_args__ = (
        sa.CheckConstraint(
            "actor_kind IN ('human', 'workload')",
            name="ck_authority_actor_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'revoked')",
            name="ck_authority_actor_status",
        ),
    )

    actor_kind: str = Field(default="human", max_length=16)
    status: str = Field(default="active", max_length=16, index=True)


class AuthorityActorExternalIdentity(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("actor_identity"),
    table=True,
):
    """A verified IdP subject bound to exactly one platform actor."""

    __table_args__ = (
        UniqueConstraint(
            "issuer",
            "subject",
            name="uq_authority_actor_external_identity_issuer_subject",
        ),
    )

    actor_id: TypeIDType = AuthorityActor.foreign_key()
    issuer: str = Field(max_length=255)
    subject: str = Field(max_length=255)
    verified_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    revoked_at: datetime | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class AuthorityTenantMembership(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("tenant_membership"),
    table=True,
):
    """Current actor membership used by server policy, not a client role claim."""

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "actor_id",
            name="uq_authority_tenant_membership_tenant_actor",
        ),
        sa.CheckConstraint(
            "membership_kind IN ('provider', 'staff')",
            name="ck_authority_tenant_membership_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'revoked')",
            name="ck_authority_tenant_membership_status",
        ),
    )

    tenant_id: TypeIDType = AuthorityTenant.foreign_key()
    actor_id: TypeIDType = AuthorityActor.foreign_key()
    membership_kind: str = Field(max_length=32)
    status: str = Field(default="active", max_length=16, index=True)


class AuthorityPerson(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("authority_person"),
    table=True,
):
    """Canonical person lifecycle without demographics or contact data."""

    __table_args__ = (
        sa.CheckConstraint(
            "lifecycle_state IN ('prospect', 'patient', 'enrolled')",
            name="ck_authority_person_lifecycle_state",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'merged', 'revoked')",
            name="ck_authority_person_status",
        ),
        sa.CheckConstraint(
            "(status = 'merged' AND merged_into_person_id IS NOT NULL) OR "
            "(status <> 'merged' AND merged_into_person_id IS NULL)",
            name="ck_authority_person_merge_target",
        ),
    )

    lifecycle_state: str = Field(max_length=16, index=True)
    status: str = Field(default="active", max_length=16, index=True)
    merged_into_person_id: TypeIDType | None = Field(  # type: ignore
        default=None,
        nullable=True,
        sa_type=TypeIDType("authority_person"),  # type: ignore
        foreign_key="authority_person.id",
    )


class AuthorityPersonOrigin(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("person_origin"),
    table=True,
):
    """Append-only provenance retained when canonical person records are merged."""

    __table_args__ = (
        sa.CheckConstraint(
            "origin IN ('sync', 'provider_entry', 'self_service')",
            name="ck_authority_person_origin",
        ),
    )

    person_id: TypeIDType = AuthorityPerson.foreign_key()
    origin: str = Field(max_length=32, index=True)
    source_ref: str | None = Field(default=None, nullable=True, max_length=160)


class AuthorityPersonBinding(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("person_binding"),
    table=True,
):
    """Opaque, verified link between a person and an identity or tenant record."""

    __table_args__ = (
        sa.CheckConstraint(
            "binding_kind IN "
            "('claim_link', 'prospect_session', 'identified_session', "
            "'provider_record')",
            name="ck_authority_person_binding_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_authority_person_binding_status",
        ),
    )

    person_id: TypeIDType = AuthorityPerson.foreign_key()
    tenant_id: TypeIDType | None = AuthorityTenant.foreign_key(
        default=None,
        nullable=True,
    )
    actor_id: TypeIDType | None = AuthorityActor.foreign_key(
        default=None,
        nullable=True,
    )
    binding_ref: str = Field(unique=True, index=True, max_length=160)
    binding_kind: str = Field(max_length=32)
    status: str = Field(default="active", max_length=16, index=True)
    verified_at: datetime | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    revoked_at: datetime | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class MountContextSession(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("mount_session"),
    table=True,
):
    """Server-side binding behind one family of opaque mount-context tokens."""

    __table_args__ = (
        sa.CheckConstraint(
            "session_kind IN ('anonymous', 'authenticated')",
            name="ck_mount_context_session_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_mount_context_session_status",
        ),
        sa.CheckConstraint(
            "surface_ceiling IN ('public', 'provider_assistant')",
            name="ck_mount_context_session_surface_ceiling",
        ),
        sa.CheckConstraint(
            "auth_tier_ceiling IN "
            "('anonymous', 'prospect', 'identified', 'enrolled', 'provider')",
            name="ck_mount_context_session_auth_tier_ceiling",
        ),
        sa.CheckConstraint(
            "(session_kind = 'anonymous' AND actor_id IS NULL "
            "AND tenant_id IS NULL AND person_binding_id IS NULL "
            "AND source_identity_id IS NULL "
            "AND surface_ceiling = 'public' "
            "AND auth_tier_ceiling = 'anonymous') OR "
            "(session_kind = 'authenticated' AND actor_id IS NOT NULL "
            "AND person_binding_id IS NOT NULL "
            "AND source_identity_id IS NOT NULL)",
            name="ck_mount_context_session_binding_shape",
        ),
    )

    actor_id: TypeIDType | None = AuthorityActor.foreign_key(
        default=None,
        nullable=True,
    )
    tenant_id: TypeIDType | None = AuthorityTenant.foreign_key(
        default=None,
        nullable=True,
    )
    person_binding_id: TypeIDType | None = AuthorityPersonBinding.foreign_key(
        default=None,
        nullable=True,
    )
    source_identity_id: TypeIDType | None = AuthorityActorExternalIdentity.foreign_key(
        default=None,
        nullable=True,
    )
    session_kind: str = Field(max_length=32)
    surface_ceiling: str = Field(max_length=32)
    auth_tier_ceiling: str = Field(max_length=16)
    status: str = Field(default="active", max_length=16, index=True)
    absolute_expires_at: datetime = Field(
        index=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    revoked_at: datetime | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class MountContextToken(
    BaseModel,
    TypeIDMixin("mount_token"),
    table=True,
):
    """Hashed bearer-token receipt. Token plaintext is never persisted."""

    session_id: TypeIDType = MountContextSession.foreign_key()
    token_hash: str = Field(unique=True, index=True, min_length=64, max_length=64)
    issued_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    refresh_after: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    expires_at: datetime = Field(index=True, sa_type=sa.DateTime(timezone=True))  # type: ignore
    revoked_at: datetime | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class InteractionLineage(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("interaction"),
    table=True,
):
    """Non-authorizing correlation for exactly one logical client turn."""

    __table_args__ = (
        sa.CheckConstraint(
            "audit_status IN ('pending', 'complete', 'failed')",
            name="ck_interaction_lineage_audit_status",
        ),
        sa.CheckConstraint(
            "usage_status IN ('pending', 'complete', 'failed')",
            name="ck_interaction_lineage_usage_status",
        ),
    )

    workload_client_id: str = Field(index=True, max_length=64)
    client_turn_id: UUID = Field(unique=True, index=True)
    session_id: TypeIDType = MountContextSession.foreign_key()
    interaction_ref: str = Field(unique=True, index=True, max_length=160)
    binding_fingerprint: str = Field(min_length=64, max_length=64)
    audit_status: str = Field(default="pending", max_length=16, index=True)
    usage_status: str = Field(default="pending", max_length=16, index=True)
    completed_at: datetime | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class AuthorityIdempotencyRecord(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("authority_idem"),
    table=True,
):
    """Request-digest binding for one Agent API logical operation slot.

    ``operation`` includes any contract phase discriminator, so a changed key
    cannot create a second effect for the same interaction and phase.
    """

    __table_args__ = (
        UniqueConstraint(
            "workload_client_id",
            "operation",
            "interaction_id",
            "idempotency_key",
            name="uq_authority_idempotency_scope_key",
        ),
        UniqueConstraint(
            "workload_client_id",
            "operation",
            "interaction_id",
            "logical_ref",
            name="uq_authority_idempotency_logical_operation",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'complete', 'failed')",
            name="ck_authority_idempotency_status",
        ),
    )

    workload_client_id: str = Field(max_length=64)
    operation: str = Field(max_length=96)
    interaction_id: TypeIDType = InteractionLineage.foreign_key()
    logical_ref: str = Field(min_length=8, max_length=160)
    idempotency_key: str = Field(min_length=16, max_length=128)
    request_digest: str = Field(min_length=64, max_length=64)
    status: str = Field(default="pending", max_length=16, index=True)
    response_ref: str | None = Field(default=None, nullable=True, max_length=160)
    retain_until: datetime = Field(
        index=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class AuthorityAuditEvent(
    BaseModel,
    TypeIDMixin("authority_audit"),
    table=True,
):
    """Append-only, privacy-minimized platform audit fact.

    Observational events retain only the contract's closed set of hashes, safe
    routing labels, and validated opaque references. Authoritative identity and
    scope are copied from the locked interaction lineage, never accepted from
    the caller payload.
    """

    __table_args__ = (
        UniqueConstraint(
            "workload_client_id",
            "interaction_id",
            "client_event_id",
            name="uq_authority_audit_observation_client_event",
        ),
        sa.CheckConstraint(
            "event_source IN ('platform_authoritative', 'sovereign_observational')",
            name="ck_authority_audit_event_source",
        ),
        sa.CheckConstraint(
            "event_source <> 'sovereign_observational' OR "
            "(workload_client_id IS NOT NULL AND client_event_id IS NOT NULL "
            "AND interaction_id IS NOT NULL)",
            name="ck_authority_audit_observation_shape",
        ),
    )

    event_source: str = Field(default="platform_authoritative", max_length=32)
    event_type: str = Field(index=True, max_length=128)
    outcome: str = Field(max_length=32)
    occurred_at: datetime = Field(index=True, sa_type=sa.DateTime(timezone=True))  # type: ignore
    recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        index=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    workload_client_id: str | None = Field(default=None, nullable=True, max_length=64)
    client_event_id: UUID | None = Field(default=None, nullable=True)
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
    interaction_id: TypeIDType | None = InteractionLineage.foreign_key(
        default=None,
        nullable=True,
    )
    resource_type: str = Field(max_length=64)
    resource_ref: str = Field(max_length=160)
    request_digest: str | None = Field(default=None, nullable=True, max_length=64)
    route: str | None = Field(default=None, nullable=True, max_length=128)
    intent: str | None = Field(default=None, nullable=True, max_length=128)
    content_sha256: str | None = Field(default=None, nullable=True, max_length=64)
    platform_refs: list[dict[str, str]] = Field(default_factory=list, sa_type=sa.JSON)


@sa.event.listens_for(AuthorityPersonOrigin, "before_update")
def _prevent_authority_person_origin_update(*_: object) -> None:
    raise RuntimeError("Authority person origins are append-only.")


@sa.event.listens_for(AuthorityPersonOrigin, "before_delete")
def _prevent_authority_person_origin_delete(*_: object) -> None:
    raise RuntimeError("Authority person origins are append-only.")


@sa.event.listens_for(AuthorityAuditEvent, "before_update")
def _prevent_authority_audit_update(*_: object) -> None:
    raise RuntimeError("Authority audit events are append-only.")


@sa.event.listens_for(AuthorityAuditEvent, "before_delete")
def _prevent_authority_audit_delete(*_: object) -> None:
    raise RuntimeError("Authority audit events are append-only.")
