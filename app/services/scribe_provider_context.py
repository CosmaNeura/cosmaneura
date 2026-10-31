"""Read existing Core provider authority without a second receiver registry."""

from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.services import authority, scribe_encounters
from app.services.companion_appointment_setup import _current_provider
from app.services.companion_encounter_contract import ConnectionSnapshot
from app.services.companion_record_contract import Closed
from app.services.companion_records import (
    RecordBudget,
    RecordUnavailable,
    check_deadline,
    transaction_budget,
)
from app.services.scribe_encounter_contract import CatalogRequest

from activemodel.session_manager import global_session
from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor import Doctor
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlmodel import Session, col, select


class ProviderContextRequest(Closed):
    # Neither action accepts renderer-supplied authority identifiers.
    action: Literal["connection", "catalog"]
    request_nonce: UUID


class ProviderContextResponse(Closed):
    contract_version: Literal["1"] = "1"
    action: Literal["connection", "catalog"]
    request_nonce: UUID
    checked_at: AwareDatetime
    connection: ConnectionSnapshot = Field(repr=False)
    catalog: dict[str, object] | None = Field(default=None, repr=False)


def _discovered_tenant(session: Session, *, issuer: str, subject: str) -> str:
    # Discovery is not authorization and takes no authority locks ahead of the
    # existing Practice -> Doctor lock order. The current resolver below must
    # validate the identity, reviewed link, membership and provider access.
    rows = session.exec(
        select(col(ScribeCaptureProviderLink.tenant_id))
        .join(
            AuthorityActorExternalIdentity,
            col(AuthorityActorExternalIdentity.id)
            == col(ScribeCaptureProviderLink.source_identity_id),
        )
        .join(Doctor, col(Doctor.id) == col(ScribeCaptureProviderLink.doctor_id))
        .where(
            AuthorityActorExternalIdentity.issuer == issuer,
            AuthorityActorExternalIdentity.subject == subject,
            col(AuthorityActorExternalIdentity.revoked_at).is_(None),
            Doctor.clerk_id == subject,
            col(ScribeCaptureProviderLink.revoked_at).is_(None),
        )
        .limit(2)
    ).all()
    if len(rows) != 1:
        raise authority.AuthorityRequestDeniedError
    return str(rows[0])


def _connection(
    *,
    issuer: str,
    subject: str,
    session_id: str,
    authority_settings: AuthorityKernelSettings,
    deadline: float | RecordBudget,
) -> ConnectionSnapshot:
    check_deadline(deadline)
    authority._ensure_enabled(authority_settings)
    if issuer != authority_settings.clerk_issuer:
        raise authority.AuthorityRequestDeniedError
    with global_session() as managed:
        session = cast(Session, managed)
        try:
            with session.no_autoflush, transaction_budget(session, deadline):
                tenant = _discovered_tenant(session, issuer=issuer, subject=subject)
                args = dict(
                    issuer=issuer,
                    subject=subject,
                    session_id=session_id,
                    tenant_ref=tenant,
                    settings=authority_settings,
                )
                connection, doctor_id, link = _current_provider(session, args)
                if session.new or session.dirty or session.deleted:
                    raise RecordUnavailable
                session.expire_all()
                current, current_doctor, current_link = _current_provider(session, args)
                if (
                    current != connection
                    or current_doctor != doctor_id
                    or current_link.id != link.id
                    or _discovered_tenant(session, issuer=issuer, subject=subject)
                    != tenant
                ):
                    raise authority.AuthorityRequestDeniedError
                if session.new or session.dirty or session.deleted:
                    raise RecordUnavailable
                check_deadline(deadline)
                return ConnectionSnapshot.model_validate(connection.model_dump())
        finally:
            # This path never creates, repairs or commits authority mappings.
            session.rollback()


def read_provider_context(
    *,
    issuer: str,
    subject: str,
    session_id: str,
    request: ProviderContextRequest,
    authority_settings: AuthorityKernelSettings,
    deadline: float | RecordBudget,
) -> ProviderContextResponse:
    def current_connection() -> ConnectionSnapshot:
        return _connection(
            issuer=issuer,
            subject=subject,
            session_id=session_id,
            authority_settings=authority_settings,
            deadline=deadline,
        )

    connection = current_connection()
    catalog = None
    if request.action == "catalog":
        # This private read has its own route gate. The closed action cannot
        # reach creation, matching, notes or other encounter mutation branches.
        catalog = scribe_encounters.encounter_workflow(
            issuer=issuer,
            subject=subject,
            session_id=session_id,
            authority_settings=authority_settings,
            deadline=deadline,
            request=CatalogRequest(
                action="list",
                expected_actor_ref=connection.actor_ref,
                expected_tenant_ref=connection.tenant_ref,
                request_nonce=request.request_nonce,
            ),
            settings_provider=lambda: ScribeEncounterSettings(enabled=True),
        )
        account = catalog.get("account")
        if (
            not isinstance(account, dict)
            or catalog.get("version") != "1"
            or catalog.get("action") != "list"
            or catalog.get("request_nonce") != str(request.request_nonce)
            or account.get("actor_ref") != connection.actor_ref
            or account.get("tenant_ref") != connection.tenant_ref
            or current_connection() != connection
        ):
            raise authority.AuthorityRequestDeniedError
    check_deadline(deadline)
    return ProviderContextResponse(
        action=request.action,
        request_nonce=request.request_nonce,
        checked_at=datetime.now(UTC),
        connection=connection,
        catalog=catalog,
    )
