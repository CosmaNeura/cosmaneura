"""Current human/clinical authority for every first-party capture phase.

The public entrypoint uses the request's shared ActiveModel session, including
the existing processing-access resolver. No identity upsert or network lookup
occurs here. Subsequent capture phases must call this boundary again.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.configuration.scribe_capture import (
    SCRIBE_CAPTURE_SETTINGS,
    ScribeCaptureSettings,
)
from app.services import authority
from app.services.doctor_access import doctor_access_in_force
from app.services.doctor_access_row_ownership import capture_doctor_access_rows

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityTenant,
    AuthorityTenantMembership,
    MountContextSession,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember
from app.models.scribe_capture_binding import (
    ScribeCaptureBinding,
    ScribeCaptureProviderLink,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select


class CaptureScopeDenied(authority.AuthorityRequestDeniedError):
    """Value-free denial: do not reveal which scope component was unavailable."""


@dataclass(frozen=True, repr=False)
class CaptureBindingSnapshot:
    binding_ref: str
    workspace_id: UUID
    appointment_ref: str
    actor_ref: str
    tenant_ref: str
    request_nonce: UUID
    checked_at: datetime


def _locked(session: Session, model: Any, identifier: Any) -> Any:
    row = session.exec(
        select(model)
        .where(model.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise CaptureScopeDenied
    return row


@dataclass(frozen=True, repr=False)
class _CaptureOwner:
    mount_id: TypeIDType
    actor_id: TypeIDType
    identity_id: TypeIDType
    subject: str
    doctor_id: TypeIDType
    practice_id: TypeIDType


def _lock_capture_owner(
    session: Session,
    mount: MountContextSession,
    settings: AuthorityKernelSettings,
    ownership: ExitStack,
) -> _CaptureOwner:
    # Called only after the opaque token and its stored session are validated.
    # Appointment/link input cannot choose the account whose rows are locked.
    context = authority._context_for_session(
        session, mount, clerk_issuer=settings.clerk_issuer
    )
    # Identical provider-only requirements to authorize_interaction; this early
    # read selects locks, while that boundary still performs the full decision.
    if (
        context.surface != "provider_assistant"
        or context.auth_tier != "provider"
        or context.person_id is None
        or mount.tenant_id is None
        or mount.actor_id is None
    ):
        raise authority.AuthorityRequestDeniedError
    identity = session.get(
        AuthorityActorExternalIdentity, mount.source_identity_id, populate_existing=True
    )
    if (
        identity is None
        or identity.actor_id != mount.actor_id
        or identity.issuer != settings.clerk_issuer
        or identity.revoked_at is not None
    ):
        raise CaptureScopeDenied
    doctor = session.exec(
        select(Doctor)
        .where(Doctor.clerk_id == identity.subject)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if doctor is None:
        raise CaptureScopeDenied
    member = session.exec(
        select(PracticeMember)
        .where(PracticeMember.doctor_id == doctor.id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if member is None:
        raise CaptureScopeDenied
    owner = _CaptureOwner(
        mount.id,
        identity.actor_id,
        identity.id,
        identity.subject,
        doctor.id,
        member.practice_id,
    )
    if isinstance(session, Session):
        locked = ownership.enter_context(
            capture_doctor_access_rows(
                session,
                practice_id=owner.practice_id,
                doctor_id=owner.doctor_id,
                subject=owner.subject,
            )
        )
        if locked is None:
            raise CaptureScopeDenied
        _practice, doctor, member, _proof = locked
    else:
        # Nontransactional test adapters cannot issue ownership evidence.
        _locked(session, Practice, owner.practice_id)
        doctor = _locked(session, Doctor, owner.doctor_id)
        member = session.exec(
            select(PracticeMember)
            .where(PracticeMember.doctor_id == doctor.id)
            .execution_options(populate_existing=True)
        ).one_or_none()
    # A practice move or identity rebind can win while the first lock waits.
    # Deny instead of taking a different owner's locks in the reverse order.
    identity = session.get(
        AuthorityActorExternalIdentity, owner.identity_id, populate_existing=True
    )
    current_mount = session.get(
        MountContextSession, owner.mount_id, populate_existing=True
    )
    if (
        doctor.clerk_id != owner.subject
        or doctor.deleted_at is not None
        or member is None
        or member.practice_id != owner.practice_id
        or identity is None
        or identity.actor_id != owner.actor_id
        or identity.issuer != settings.clerk_issuer
        or identity.subject != owner.subject
        or identity.revoked_at is not None
        or current_mount is None
        or (current_mount.actor_id, current_mount.source_identity_id)
        != (owner.actor_id, owner.identity_id)
    ):
        raise CaptureScopeDenied
    return owner


def _authorized_scope(
    session: Session,
    *,
    workload_client_id: str,
    mount_context_token: str,
    interaction_ref: str,
    client_turn_id: UUID,
    appointment_ref: str,
    settings: AuthorityKernelSettings,
) -> dict[str, Any]:
    with ExitStack() as ownership:
        return _authorized_scope_with_rows(
            session,
            workload_client_id=workload_client_id,
            mount_context_token=mount_context_token,
            interaction_ref=interaction_ref,
            client_turn_id=client_turn_id,
            appointment_ref=appointment_ref,
            settings=settings,
            ownership=ownership,
        )


def _authorized_scope_with_rows(
    session: Session,
    *,
    workload_client_id: str,
    mount_context_token: str,
    interaction_ref: str,
    client_turn_id: UUID,
    appointment_ref: str,
    settings: AuthorityKernelSettings,
    ownership: ExitStack,
) -> dict[str, Any]:
    arguments = {
        "session": session,
        "workload_client_id": workload_client_id,
        "mount_context_token": mount_context_token,
        "interaction_ref": interaction_ref,
        "client_turn_id": client_turn_id,
        "settings": settings,
    }
    owner: _CaptureOwner | None = None

    def lock_owner(mount: MountContextSession) -> None:
        nonlocal owner
        owner = _lock_capture_owner(session, mount, settings, ownership)

    # Match exchange/offboarding's Practice -> Doctor order before the complete
    # existing view-as/token/lineage authorization; this hook grants no access.
    authorized = authority.authorize_interaction(
        **arguments, _before_view_as=lock_owner
    )
    if owner is None:
        raise CaptureScopeDenied
    mount = _locked(session, MountContextSession, authorized.mount_session.id)
    if (mount.id, mount.actor_id, mount.source_identity_id) != (
        owner.mount_id,
        owner.actor_id,
        owner.identity_id,
    ):
        raise CaptureScopeDenied
    actor = _locked(session, AuthorityActor, mount.actor_id)
    identity = _locked(
        session, AuthorityActorExternalIdentity, mount.source_identity_id
    )
    if (
        identity.actor_id != owner.actor_id
        or identity.issuer != settings.clerk_issuer
        or identity.subject != owner.subject
        or identity.revoked_at is not None
    ):
        raise CaptureScopeDenied
    tenant = _locked(session, AuthorityTenant, mount.tenant_id)
    person_binding = _locked(session, AuthorityPersonBinding, mount.person_binding_id)
    _locked(session, AuthorityPerson, person_binding.person_id)
    membership = session.exec(
        select(AuthorityTenantMembership)
        .where(
            AuthorityTenantMembership.actor_id == actor.id,
            AuthorityTenantMembership.tenant_id == tenant.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if membership is None:
        raise CaptureScopeDenied
    # Re-derive lineage after refreshing/locking every authority input. This
    # rejects concurrent revocation and stale ORM identity-map values alike.
    authority.authorize_interaction(**arguments)
    if (
        person_binding.binding_kind != "provider_record"
        or person_binding.binding_ref != appointment_ref
        or person_binding.actor_id not in (None, actor.id)
    ):
        raise CaptureScopeDenied
    link = session.exec(
        select(ScribeCaptureProviderLink)
        .where(
            ScribeCaptureProviderLink.actor_id == actor.id,
            ScribeCaptureProviderLink.tenant_id == tenant.id,
            col(ScribeCaptureProviderLink.revoked_at).is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        link is None
        or link.source_identity_id != identity.id
        or link.doctor_id != owner.doctor_id
        or link.practice_id != owner.practice_id
        or link.reviewed_at is None
        or len(link.review_ref) < 16
    ):
        raise CaptureScopeDenied
    doctor = _locked(session, Doctor, link.doctor_id)
    practice_member = session.exec(
        select(PracticeMember)
        .where(PracticeMember.doctor_id == doctor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if practice_member is None or practice_member.practice_id != link.practice_id:
        raise CaptureScopeDenied
    _locked(session, Practice, link.practice_id)
    appointment = session.exec(
        select(Appointment)
        .where(Appointment.id == appointment_ref)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if appointment is None:
        raise CaptureScopeDenied
    patient = _locked(session, Patient, appointment.patient_id)
    if (
        doctor.deleted_at is not None
        or doctor.clerk_id != identity.subject
        or appointment.doctor_id != doctor.id
        or patient.doctor_id != doctor.id
        or not doctor_access_in_force(doctor)
    ):
        raise CaptureScopeDenied
    return {
        "provider_link_id": link.id,
        "actor_id": actor.id,
        "tenant_id": tenant.id,
        "source_identity_id": identity.id,
        "person_binding_id": person_binding.id,
        "doctor_id": doctor.id,
        "patient_id": patient.id,
        "appointment_id": appointment.id,
        "practice_id": link.practice_id,
        "scribe_tenant_id": link.scribe_tenant_id,
    }


def _scope_digest(scope: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {key: str(value) for key, value in scope.items()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def authorize_capture_binding(
    session: Session,
    *,
    binding_ref: str,
    workspace_id: UUID,
    workload_client_id: str,
    mount_context_token: str,
    interaction_ref: str,
    client_turn_id: UUID,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
    capture_settings: ScribeCaptureSettings = SCRIBE_CAPTURE_SETTINGS,
) -> ScribeCaptureBinding:
    """Authorize an existing binding inside the caller's phase transaction.

    The initial identifier lookup only locates canonical scope; no data is
    released and no downstream work occurs until current authority is checked.
    """
    if not capture_settings.enabled:
        raise authority.AuthorityKernelDisabledError
    candidate = session.exec(
        select(ScribeCaptureBinding)
        .where(
            ScribeCaptureBinding.id == binding_ref,
        )
        .execution_options(populate_existing=True)
    ).one_or_none()
    if candidate is None:
        raise CaptureScopeDenied
    scope = _authorized_scope(
        session,
        workload_client_id=workload_client_id,
        mount_context_token=mount_context_token,
        interaction_ref=interaction_ref,
        client_turn_id=client_turn_id,
        appointment_ref=str(candidate.appointment_id),
        settings=settings,
    )
    binding = _locked(session, ScribeCaptureBinding, candidate.id)
    if (
        binding.revoked_at is not None
        or binding.workspace_id != workspace_id
        or binding.scope_sha256 != _scope_digest(scope)
        or any(getattr(binding, key) != value for key, value in scope.items())
    ):
        raise CaptureScopeDenied
    return binding


def resolve_capture_binding(
    *,
    workload_client_id: str,
    mount_context_token: str,
    interaction_ref: str,
    client_turn_id: UUID,
    workspace_id: UUID,
    appointment_ref: str,
    request_nonce: UUID,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
    capture_settings: ScribeCaptureSettings = SCRIBE_CAPTURE_SETTINGS,
) -> CaptureBindingSnapshot:
    if not capture_settings.enabled:
        raise authority.AuthorityKernelDisabledError
    with get_session() as managed:
        session = cast(Session, managed)
        try:
            scope = _authorized_scope(
                session,
                workload_client_id=workload_client_id,
                mount_context_token=mount_context_token,
                interaction_ref=interaction_ref,
                client_turn_id=client_turn_id,
                appointment_ref=appointment_ref,
                settings=settings,
            )
            digest = _scope_digest(scope)
            bindings = session.exec(
                select(ScribeCaptureBinding)
                .where(
                    (ScribeCaptureBinding.workspace_id == workspace_id)
                    | (
                        (ScribeCaptureBinding.actor_id == scope["actor_id"])
                        & (ScribeCaptureBinding.tenant_id == scope["tenant_id"])
                        & (
                            ScribeCaptureBinding.appointment_id
                            == scope["appointment_id"]
                        )
                    )
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            if len(bindings) > 1:
                raise CaptureScopeDenied
            binding = bindings[0] if bindings else None
            if binding is None:
                binding = ScribeCaptureBinding(
                    workspace_id=workspace_id,
                    scope_sha256=digest,
                    **scope,
                )
                session.add(binding)
                session.flush()
            elif (
                binding.revoked_at is not None
                or binding.workspace_id != workspace_id
                or binding.scope_sha256 != digest
                or any(getattr(binding, key) != value for key, value in scope.items())
            ):
                raise CaptureScopeDenied
            result = CaptureBindingSnapshot(
                binding_ref=str(binding.id),
                workspace_id=workspace_id,
                appointment_ref=str(binding.appointment_id),
                actor_ref=str(binding.actor_id),
                tenant_ref=str(binding.tenant_id),
                request_nonce=request_nonce,
                checked_at=datetime.now(UTC),
            )
            session.commit()
            return result
        except IntegrityError:
            session.rollback()
            # A different creator racing the same workspace cannot observe or
            # adopt the winning binding. Same-creator creation is doctor-locked.
            raise CaptureScopeDenied from None
        except Exception:
            session.rollback()
            raise
