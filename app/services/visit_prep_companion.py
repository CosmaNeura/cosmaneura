"""Core-owned pre-visit display transaction, never a conversational tool grant.

Reuse the dashboard's current package/artifact reader. All content stays staged
until current authority, action audit and successful commit acknowledgment. No
chart, account link or clinical record is provisioned and no model is invoked.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import Field
from typeid import TypeID

from app.configuration import authority as authority_configuration
from app.configuration import service_auth
from app.configuration.clerk import VisitPrepPhysicianPrincipal
from app.configuration.service_auth import VisitPrepWorkloadPrincipal
from app.services import companion_encounters
from app.services.companion_encounter_contract import digest
from app.services.companion_record_contract import (
    ActorRef,
    Closed,
    Digest,
    TenantRef,
    TypeSuffix,
)
from app.services.companion_records import (
    RecordBudget,
    RecordUnavailable,
    check_deadline,
    transaction_budget,
)
from app.services.doctor_access import doctor_access_in_force
from app.services.visit_prep_packages import (
    _database_now,
    _load_authorized_package,
    _owner,
)
from app.services.visit_prep_synthesis_publication import (
    VisitPrepSynthesisView,
    read_visit_prep_synthesis_in_session,
)

from activemodel.session_manager import global_session
from activemodel.types import TypeIDType
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor
from app.models.practice import PracticeMember
from app.models.provider_record_authority import ProviderPracticeAuthority
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlmodel import select

MAX_RESPONSE_BYTES = 262144
PATH = "/service/v1/companion/visit-prep/package"
PreparationRef = Annotated[str, Field(pattern=r"^visit_prep" + TypeSuffix, strict=True)]


class VisitPrepCompanionRequest(Closed):
    contract_version: Literal["1"]
    action: Literal["visit-prep.open"]
    purpose: Literal["provider_previsit_review"]
    preparation_ref: PreparationRef
    expected_actor_ref: ActorRef
    expected_tenant_ref: TenantRef
    connection_sha256: Digest
    workspace_id: UUID
    request_nonce: UUID


class VisitPrepCompanionDenied(Exception):
    def __init__(self):
        super().__init__("Visit Prep is unavailable.")


@dataclass(frozen=True)
class _OwnerScope:
    member_id: TypeIDType
    doctor_id: TypeIDType
    practice_id: TypeIDType


def _receiver_gate(workload, physician, at, auth_settings, workload_settings):
    current = service_auth.COMPANION_VISIT_PREP_AUTH_SETTINGS
    if (
        current != workload_settings
        or not current.enabled
        or not current.is_complete()
        or current.allowed_client_ids != frozenset({workload.client_id})
        or authority_configuration.AUTHORITY_KERNEL_SETTINGS != auth_settings
        or not auth_settings.enabled
        or not auth_settings.is_complete()
        or physician.issuer != auth_settings.clerk_issuer
    ):
        raise RecordUnavailable
    if not workload.window.valid_at(at) or not physician.window.valid_at(at):
        raise VisitPrepCompanionDenied


def _row(session, model, *predicates):
    # NOWAIT breaks any authority-writer lock-order conflict. Contention is
    # unavailable, never permission to use an earlier correspondence snapshot.
    return session.exec(
        select(model)
        .where(*predicates)
        .with_for_update(nowait=True)
        .execution_options(populate_existing=True)
    ).one_or_none()


def _locked_connection(session, physician, request, settings):
    doctor = session.exec(
        select(Doctor).where(Doctor.clerk_id == physician.subject)
    ).one_or_none()
    member = (
        session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
        ).one_or_none()
        if doctor is not None
        else None
    )
    if doctor is None or member is None:
        raise VisitPrepCompanionDenied
    scope = _OwnerScope(member.id, doctor.id, member.practice_id)
    # Use the package's actual Practice→Organization→Doctor→Member locks first,
    # avoiding a second connection/discovery transaction or chart requirement.
    _owner(
        session,
        owner_practice_member_id=scope.member_id,
        owner_doctor_id=scope.doctor_id,
        practice_id=scope.practice_id,
        expected_clerk_subject=physician.subject,
    )
    mapping = _row(
        session,
        ProviderPracticeAuthority,
        ProviderPracticeAuthority.practice_id == scope.practice_id,
    )
    link = _row(
        session,
        ScribeCaptureProviderLink,
        ScribeCaptureProviderLink.doctor_id == scope.doctor_id,
        ScribeCaptureProviderLink.tenant_id == request.expected_tenant_ref,
    )
    identity = _row(
        session,
        AuthorityActorExternalIdentity,
        AuthorityActorExternalIdentity.issuer == physician.issuer,
        AuthorityActorExternalIdentity.subject == physician.subject,
    )
    if (
        mapping is None
        or link is None
        or identity is None
        or mapping.tenant_id != link.tenant_id
    ):
        raise VisitPrepCompanionDenied
    actor = _row(session, AuthorityActor, AuthorityActor.id == identity.actor_id)
    membership = _row(
        session,
        AuthorityTenantMembership,
        AuthorityTenantMembership.actor_id == identity.actor_id,
        AuthorityTenantMembership.tenant_id == link.tenant_id,
    )
    tenant = _row(session, AuthorityTenant, AuthorityTenant.id == link.tenant_id)
    if actor is None or membership is None or tenant is None:
        raise VisitPrepCompanionDenied
    connection, doctor_id = companion_encounters._provider(
        session,
        issuer=physician.issuer,
        subject=physician.subject,
        session_id=physician.session_id,
        tenant_ref=request.expected_tenant_ref,
        settings=settings,
    )
    if (
        doctor_id != scope.doctor_id
        or connection.practice_ref != str(scope.practice_id)
        or connection.actor_ref != request.expected_actor_ref
        or connection.tenant_ref != request.expected_tenant_ref
        or connection.connection_sha256 != request.connection_sha256
    ):
        raise VisitPrepCompanionDenied
    return scope, connection


def _reauthorize(session, scope, physician, request, settings):
    _owner(
        session,
        owner_practice_member_id=scope.member_id,
        owner_doctor_id=scope.doctor_id,
        practice_id=scope.practice_id,
        expected_clerk_subject=physician.subject,
    )
    connection, doctor_id = companion_encounters._provider(
        session,
        issuer=physician.issuer,
        subject=physician.subject,
        session_id=physician.session_id,
        tenant_ref=request.expected_tenant_ref,
        settings=settings,
    )
    if (
        doctor_id != scope.doctor_id
        or connection.connection_sha256 != request.connection_sha256
    ):
        raise VisitPrepCompanionDenied
    at = _database_now(session)
    doctor = session.get(Doctor, scope.doctor_id)
    if doctor is None or not doctor_access_in_force(doctor, at=at):
        raise VisitPrepCompanionDenied
    return at


def _type_id(value: str) -> TypeIDType:
    return cast(TypeIDType, TypeID.from_string(value))


def read_visit_prep_companion(
    *,
    workload: VisitPrepWorkloadPrincipal,
    physician: VisitPrepPhysicianPrincipal,
    request: VisitPrepCompanionRequest,
    deadline: RecordBudget,
    serialize: Callable[[VisitPrepSynthesisView, datetime], bytes],
) -> bytes:
    """Return validated bytes only after a successful owned commit.

    The receiver supplies a pure closed-schema serializer, not authority or
    source content. Ambient sessions are refused without touching their state.
    Failed/uncertain commits and late results never disclose the private buffer.
    """
    auth_settings = authority_configuration.AUTHORITY_KERNEL_SETTINGS
    workload_settings = service_auth.COMPANION_VISIT_PREP_AUTH_SETTINGS
    if not isinstance(workload, VisitPrepWorkloadPrincipal) or not isinstance(
        physician, VisitPrepPhysicianPrincipal
    ):
        raise VisitPrepCompanionDenied
    request = VisitPrepCompanionRequest.model_validate(request.model_dump())
    check_deadline(deadline)
    with global_session() as session:
        try:
            with session.no_autoflush, transaction_budget(session, deadline):
                _receiver_gate(
                    workload,
                    physician,
                    _database_now(session),
                    auth_settings,
                    workload_settings,
                )
                scope, connection = _locked_connection(
                    session, physician, request, auth_settings
                )
                _receiver_gate(
                    workload,
                    physician,
                    _database_now(session),
                    auth_settings,
                    workload_settings,
                )
                result = read_visit_prep_synthesis_in_session(
                    session,
                    owner_practice_member_id=scope.member_id,
                    owner_doctor_id=scope.doctor_id,
                    practice_id=scope.practice_id,
                    expected_clerk_subject=physician.subject,
                    visit_prep_id=_type_id(request.preparation_ref),
                )
                session.add(
                    AuthorityAuditEvent(
                        event_type="visit_prep.companion_open",
                        outcome="allowed",
                        occurred_at=_database_now(session),
                        workload_client_id=workload.client_id,
                        client_event_id=request.request_nonce,
                        actor_id=_type_id(connection.actor_ref),
                        tenant_id=_type_id(connection.tenant_ref),
                        resource_type="visit_prep",
                        resource_ref=request.preparation_ref,
                        route="companion_visit_prep",
                        intent=request.purpose,
                        request_digest=digest(request.model_dump(mode="json")),
                        content_sha256=result.package.source_version,
                        platform_refs=[
                            {"kind": "workspace", "ref": str(request.workspace_id)},
                            {
                                "kind": "provider_link",
                                "ref": connection.provider_link_ref,
                            },
                            {
                                "kind": "clerk_session_sha256",
                                "ref": digest(physician.session_id),
                            },
                        ],
                    )
                )
                session.flush()
                # Audit/lock waits are past. Re-resolve time-dependent Core
                # authority before serialization and immediately before commit.
                at = _reauthorize(session, scope, physician, request, auth_settings)
                _receiver_gate(
                    workload, physician, at, auth_settings, workload_settings
                )
                body = serialize(result, at)
                if (
                    not isinstance(body, bytes)
                    or not body
                    or len(body) > MAX_RESPONSE_BYTES
                ):
                    raise RecordUnavailable
                at = _reauthorize(session, scope, physician, request, auth_settings)
                _receiver_gate(
                    workload, physician, at, auth_settings, workload_settings
                )
                final = _load_authorized_package(
                    session,
                    owner_practice_member_id=scope.member_id,
                    owner_doctor_id=scope.doctor_id,
                    practice_id=scope.practice_id,
                    expected_clerk_subject=physician.subject,
                    visit_prep_id=_type_id(request.preparation_ref),
                )
                if final.package.source_version != result.package.source_version:
                    raise VisitPrepCompanionDenied
                # A retained owner read need not have an active invitation, but
                # its time-dependent projection must not claim an earlier state.
                if final.package.preparation != result.package.preparation:
                    raise RecordUnavailable
                _receiver_gate(
                    workload,
                    physician,
                    _database_now(session),
                    auth_settings,
                    workload_settings,
                )
                check_deadline(deadline)
                session.commit()
                check_deadline(deadline)
                return body
        except Exception:
            session.rollback()
            raise
