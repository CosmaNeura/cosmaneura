"""Persist the self-only processing fence; shared login and PHI are retained."""

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from app.services.scribe_cancellation_contract import (
    AcceptRequest,
    CancellationConflict,
    CancellationDenied,
    CancellationNotFound,
    CancellationPolicy,
    CancellationPreview,
    CancellationReceipt,
    CancellationRequest,
    CancellationSettings,
    CancellationUnavailable,
    PreviewRequest,
    StatusRequest,
)

from app.models.scribe_cancellation import scribe_cancellation_operation as operations
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

MAX_SCOPE_ROWS = 10_000


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, separators=(",", ":"), sort_keys=True, allow_nan=False
        ).encode()
    ).hexdigest()


@dataclass(frozen=True, repr=False)
class SelfIdentity:
    caller_sha256: str
    doctor_ref: str
    actor_ref: str | None


def resolve_self(
    connection: Connection, *, issuer: str, subject: str, trusted_issuer: str, tables
) -> SelfIdentity:
    """Verified issuer/subject only; never email adoption, upsert or view-as."""
    if not trusted_issuer or issuer != trusted_issuer:
        raise CancellationDenied
    doctor, identity, actor = tables.doctor, tables.identity, tables.actor
    rows = connection.execute(
        select(doctor.c.id, doctor.c.deleted_at)
        .where(
            doctor.c.clerk_id == subject,
        )
        .limit(2)
        .with_for_update()
    ).all()
    if len(rows) != 1 or rows[0][1] is not None:
        raise CancellationDenied
    actors = connection.execute(
        select(
            identity.c.actor_id,
            identity.c.revoked_at,
            identity.c.verified_at,
            actor.c.actor_kind,
            actor.c.status,
        )
        .outerjoin(actor, actor.c.id == identity.c.actor_id)
        .where(
            identity.c.issuer == issuer,
            identity.c.subject == subject,
        )
        .limit(2)
    ).all()
    if len(actors) > 1 or any(
        revoked is not None or verified is None or kind != "human" or state != "active"
        for _, revoked, verified, kind, state in actors
    ):
        raise CancellationDenied
    return SelfIdentity(
        digest([issuer, subject]),
        str(rows[0][0]),
        str(actors[0][0]) if actors else None,
    )


def never_activated_subject(
    connection: Connection, *, issuer: str, subject: str, tables
) -> bool:
    """Count all Doctor rows, including deleted rows; only true first use is 404."""
    rows = connection.execute(
        select(tables.doctor.c.id).where(tables.doctor.c.clerk_id == subject).limit(2)
    ).all()
    if rows:
        return False
    recorded = connection.execute(
        select(operations.c.id).where(
            operations.c.caller_sha256 == digest([issuer, subject])
        )
    ).first()
    historical_link = connection.execute(
        select(tables.link.c.id)
        .join(tables.identity, tables.identity.c.id == tables.link.c.source_identity_id)
        .where(tables.identity.c.issuer == issuer, tables.identity.c.subject == subject)
        .limit(1)
    ).first()
    if recorded is not None or historical_link is not None:
        raise CancellationDenied
    grant = tables.grant
    revoked = connection.execute(
        select(grant.c.id)
        .where(grant.c.clerk_id == subject, grant.c.revoked_at.is_not(None))
        .limit(1)
    ).first()
    if revoked is not None:
        raise CancellationDenied
    identity = tables.identity
    revoked_identity = connection.execute(
        select(identity.c.id)
        .where(
            identity.c.issuer == issuer,
            identity.c.subject == subject,
            identity.c.revoked_at.is_not(None),
        )
        .limit(1)
    ).first()
    if revoked_identity is not None:
        raise CancellationDenied
    return True


def deny_account_tombstones(
    connection: Connection, *, identity: SelfIdentity, subject: str, tables
) -> None:
    grant, member, link = tables.grant, tables.member, tables.link
    revoked_grant = connection.execute(
        select(grant.c.id)
        .where(grant.c.clerk_id == subject, grant.c.revoked_at.is_not(None))
        .limit(1)
    ).first()
    members = connection.execute(
        select(member.c.seat_state, member.c.access_revoked_at)
        .where(member.c.doctor_id == identity.doctor_ref)
        .limit(2)
    ).all()
    if (
        revoked_grant is not None
        or len(members) > 1
        or any(seat != "active" or revoked is not None for seat, revoked in members)
    ):
        raise CancellationDenied
    cancelled = connection.execute(
        select(operations.c.id).where(operations.c.doctor_ref == identity.doctor_ref)
    ).first()
    revoked_link = connection.execute(
        select(link.c.id)
        .where(link.c.doctor_id == identity.doctor_ref, link.c.revoked_at.is_not(None))
        .limit(1)
    ).first()
    # Own cancellation status deliberately survives its Scribe-only link fence.
    if revoked_link is not None and cancelled is None:
        raise CancellationDenied


def owned_inventory(
    connection: Connection, *, identity: SelfIdentity, tables
) -> list[list[Any]]:
    """Exact historical creator links/bindings/admissions, never a tenant purge.

    Called after the existing Doctor row is locked. This is an observational
    inventory, not permission to erase or take over practice-owned records.
    Incomplete admissions are retained as incomplete inventory entries.
    """
    link, binding, admission, source = (
        tables.link,
        tables.binding,
        tables.admission,
        tables.identity,
    )
    if identity.actor_ref is None:
        return []  # A pre-Scribe account can cancel without creating authority.
    links = (
        connection.execute(
            select(link)
            .join(source, source.c.id == link.c.source_identity_id)
            .where(
                link.c.doctor_id == identity.doctor_ref,
                link.c.actor_id == identity.actor_ref,
                source.c.actor_id == identity.actor_ref,
            )
            .order_by(link.c.id)
            .limit(MAX_SCOPE_ROWS + 1)
            .with_for_update()
        )
        .mappings()
        .all()
    )
    if len(links) > MAX_SCOPE_ROWS:
        raise CancellationUnavailable
    inventory = []
    for original in links:
        bindings = (
            connection.execute(
                select(binding)
                .where(binding.c.provider_link_id == original["id"])
                .order_by(binding.c.id)
                .limit(MAX_SCOPE_ROWS + 1)
                .with_for_update()
            )
            .mappings()
            .all()
        )
        for item in bindings:
            if any(
                item[key] != original[key]
                for key in (
                    "actor_id",
                    "doctor_id",
                    "tenant_id",
                    "practice_id",
                    "source_identity_id",
                    "scribe_tenant_id",
                )
            ):
                raise CancellationDenied
            admissions = (
                connection.execute(
                    select(admission)
                    .where(admission.c.binding_id == item["id"])
                    .order_by(admission.c.id)
                    .limit(MAX_SCOPE_ROWS + 1)
                    .with_for_update()
                )
                .mappings()
                .all()
            )
            if len(admissions) > MAX_SCOPE_ROWS:
                raise CancellationUnavailable
            for accepted in admissions:
                if accepted["scribe_tenant_id"] != original["scribe_tenant_id"]:
                    raise CancellationDenied
                inventory.append(
                    [
                        str(original["id"]),
                        str(item["id"]),
                        str(accepted["id"]),
                        str(accepted["scribe_tenant_id"]),
                        str(accepted["session_id"]) if accepted["session_id"] else None,
                    ]
                )
            if not admissions:
                inventory.append(
                    [
                        str(original["id"]),
                        str(item["id"]),
                        None,
                        str(item["scribe_tenant_id"]),
                        None,
                    ]
                )
            if len(inventory) > MAX_SCOPE_ROWS:
                raise CancellationUnavailable
        if len(bindings) > MAX_SCOPE_ROWS:
            raise CancellationUnavailable
    return sorted(inventory, key=lambda row: json.dumps(row))


def preview_digest(
    identity: SelfIdentity, inventory, policy: CancellationPolicy
) -> str:
    return digest(
        [
            "scribe-cancellation-request-v1",
            "scribe-only",
            identity.caller_sha256,
            identity.doctor_ref,
            inventory,
            policy.model_dump(mode="json"),
        ]
    )


def _receipt(
    row, request: AcceptRequest | StatusRequest, *, now: datetime | None = None
) -> CancellationReceipt:
    policy = CancellationPolicy.model_validate(row["policy"])

    def utc(value):
        return (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )

    return CancellationReceipt(
        action=request.action,
        request_nonce=request.request_nonce,
        operation_id=row["id"],
        received_at=utc(row["accepted_at"]),
        export_until=utc(row["export_until"]),
        phase="read-only-export"
        if (now or datetime.now(UTC)) < utc(row["export_until"])
        else "read-only-expired",
        grace_days=policy.grace_days,
        phi_scope=policy.phi_scope,
    )


def existing_receipt(
    connection: Connection,
    identity: SelfIdentity,
    request,
    request_key: UUID | None = None,
):
    row = (
        connection.execute(
            select(operations).where(
                operations.c.caller_sha256 == identity.caller_sha256,
                operations.c.doctor_ref == identity.doctor_ref,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        if isinstance(request, StatusRequest):
            raise CancellationNotFound
        return None
    if isinstance(request, StatusRequest):
        if request.operation_id is not None and request.operation_id != row["id"]:
            raise CancellationDenied
    elif (
        not isinstance(request, AcceptRequest)
        or request_key is None
        or not all(
            (
                hmac.compare_digest(
                    row["request_key_sha256"], digest(str(request_key))
                ),
                hmac.compare_digest(row["preview_sha256"], request.preview_sha256),
            )
        )
    ):
        raise CancellationConflict
    return _receipt(row, request)


def persist_request(
    connection: Connection,
    *,
    identity: SelfIdentity,
    inventory,
    policy: CancellationPolicy,
    request: AcceptRequest,
    request_key: UUID,
    now: datetime,
) -> CancellationReceipt:
    existing = existing_receipt(connection, identity, request, request_key)
    if existing:
        return existing
    if request.preview_sha256 != preview_digest(identity, inventory, policy):
        raise CancellationConflict
    if now.tzinfo is None or now.utcoffset() is None:
        raise CancellationUnavailable
    accepted_at = now.astimezone(UTC)
    values = {
        "id": uuid4(),
        "caller_sha256": identity.caller_sha256,
        "doctor_ref": identity.doctor_ref,
        "request_key_sha256": digest(str(request_key)),
        "preview_sha256": request.preview_sha256,
        "inventory_sha256": digest(inventory),
        "inventory": inventory,
        "policy": policy.model_dump(mode="json"),
        "accepted_at": accepted_at,
        "export_until": accepted_at + timedelta(days=policy.grace_days),
        "phase": "read-only",
    }
    try:
        with connection.begin_nested():
            connection.execute(operations.insert().values(**values))
    except IntegrityError:
        # Concurrent acceptance has exactly the same immutable retry contract.
        existing = existing_receipt(connection, identity, request, request_key)
        if existing:
            return existing
        raise CancellationConflict from None
    return _receipt(values, request)


def execute_cancellation(
    *,
    issuer: str,
    subject: str,
    session_id: str,
    request: CancellationRequest,
    request_key: UUID | None,
    settings: CancellationSettings,
    authority_settings,
    deadline,
):
    # Lazy Core imports keep standalone contract/ledger tests bootstrap-free.
    from types import SimpleNamespace

    from app.services.companion_records import check_deadline, transaction_budget

    from activemodel.session_manager import global_session
    from app.models.authority import AuthorityActor, AuthorityActorExternalIdentity
    from app.models.doctor import Doctor
    from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
    from app.models.practice import PracticeMember
    from app.models.scribe_capture_admission import ScribeCaptureAdmission
    from app.models.scribe_capture_binding import (
        ScribeCaptureBinding,
        ScribeCaptureProviderLink,
    )

    if not settings.ledger_ready:
        raise CancellationUnavailable
    if issuer != authority_settings.clerk_issuer or not session_id:
        raise CancellationDenied
    tables = SimpleNamespace(
        doctor=cast(Any, Doctor).__table__,
        identity=cast(Any, AuthorityActorExternalIdentity).__table__,
        actor=cast(Any, AuthorityActor).__table__,
        grant=cast(Any, PilotAccessGrandfatherGrant).__table__,
        member=cast(Any, PracticeMember).__table__,
        link=cast(Any, ScribeCaptureProviderLink).__table__,
        binding=cast(Any, ScribeCaptureBinding).__table__,
        admission=cast(Any, ScribeCaptureAdmission).__table__,
    )
    with global_session() as session:
        try:
            with transaction_budget(session, deadline):
                db = session.connection()
                if isinstance(request, StatusRequest) and never_activated_subject(
                    db, issuer=issuer, subject=subject, tables=tables
                ):
                    raise CancellationNotFound
                identity = resolve_self(
                    db,
                    issuer=issuer,
                    subject=subject,
                    trusted_issuer=authority_settings.clerk_issuer,
                    tables=tables,
                )
                if not isinstance(request, PreviewRequest):
                    try:
                        old = existing_receipt(db, identity, request, request_key)
                    except CancellationNotFound:
                        deny_account_tombstones(
                            db, identity=identity, subject=subject, tables=tables
                        )
                        raise
                    if old:
                        return old  # Own receipt grants no renewed processing access.
                deny_account_tombstones(
                    db, identity=identity, subject=subject, tables=tables
                )
                settings.require_policy()
                current = resolve_self(
                    db,
                    issuer=issuer,
                    subject=subject,
                    trusted_issuer=authority_settings.clerk_issuer,
                    tables=tables,
                )
                if current != identity:
                    raise CancellationDenied
                inventory = owned_inventory(db, identity=identity, tables=tables)
                policy = settings.require_policy()
                if isinstance(request, PreviewRequest):
                    return CancellationPreview(
                        request_nonce=request.request_nonce,
                        preview_sha256=preview_digest(identity, inventory, policy),
                        grace_days=policy.grace_days,
                        phi_scope=policy.phi_scope,
                    )
                if not isinstance(request, AcceptRequest) or request_key is None:
                    raise CancellationDenied
                result = persist_request(
                    db,
                    identity=identity,
                    inventory=inventory,
                    policy=policy,
                    request=request,
                    request_key=request_key,
                    now=datetime.now(UTC),
                )
                check_deadline(deadline)
                session.commit()  # Persist before the HTTP receipt is constructed/sent.
                return result
        finally:
            session.rollback()
