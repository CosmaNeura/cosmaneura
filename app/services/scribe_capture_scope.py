"""Current human/clinical authority for every first-party capture phase.

The public entrypoint uses the request's shared ActiveModel session, including
the existing processing-access resolver. No identity upsert or network lookup
occurs here. Subsequent capture phases must call this boundary again.
"""

from __future__ import annotations

import hashlib
import json
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

from activemodel.session_manager import get_session
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
    arguments = {
        "session": session,
        "workload_client_id": workload_client_id,
        "mount_context_token": mount_context_token,
        "interaction_ref": interaction_ref,
        "client_turn_id": client_turn_id,
        "settings": settings,
    }
    # Existing boundary locks Doctor -> ViewAsSession -> token. Preserve that
    # order before taking additional scope locks, and never trust a cached grant.
    authorized = authority.authorize_interaction(**arguments)
    mount = _locked(session, MountContextSession, authorized.mount_session.id)
    actor = _locked(session, AuthorityActor, mount.actor_id)
    identity = _locked(
        session, AuthorityActorExternalIdentity, mount.source_identity_id
    )
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
