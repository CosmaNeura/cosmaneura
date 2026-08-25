from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, TypeVar, cast
from uuid import uuid4

import pytest
from clerk_backend_api.models import ClerkError, ClerkErrors, ClerkErrorsData
from typer.testing import CliRunner

import app.cli as cli_module
import app.services.authority_identity_rebind as identity_rebind
from app.cli import app
from app.configuration.authority import AuthorityKernelSettings
from app.services.authority import (
    AuthorityRequestDeniedError,
    exchange_context_token,
    provider_record_actor_ref,
    resolve_mount_context,
)
from app.services.authority_identity_rebind import (
    AUTHORITY_IDENTITY_REBIND_EVENT_TYPE,
    AuthorityIdentityRebindError,
    AuthorityIdentityRebindPlan,
    AuthorityIdentityRebindResult,
    plan_staging_provider_authority_identity_rebind,
    rebind_staging_provider_authority_identity,
)
from app.services.clerk_cohort import (
    ClerkCohortIdentity,
    canonical_clerk_email_pair_sha256,
)
from app.services.staging_synthetic_appointment import (
    SYNTHETIC_APPOINTMENT_DATE,
    SYNTHETIC_APPOINTMENT_NAME,
    SYNTHETIC_APPOINTMENT_NOTES,
    SYNTHETIC_CREATED_BY_SYSTEM,
    SYNTHETIC_PATIENT_DATE_OF_BIRTH,
    SYNTHETIC_PATIENT_NAME,
)

from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
    AuthorityTenant,
    AuthorityTenantMembership,
    MountContextSession,
    MountContextToken,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.synthetic_preview_appointment_fixture import (
    SYNTHETIC_PREVIEW_FIXTURE_VERSION,
    SyntheticPreviewAppointmentFixture,
)
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, select

TEST_AUTHORITY_SETTINGS = AuthorityKernelSettings(
    enabled=True,
    token_ttl_seconds=900,
    token_refresh_after_seconds=300,
    clerk_issuer="https://test.clerk.invalid",
)
CURRENT_SUBJECT = "user_current_singleton"
STALE_SUBJECT = "user_stale_absent"
PRIOR_REVOKED_SUBJECT = "user_prior_revoked_absent"
CURRENT_EMAIL = "current-provider@example.test"
OPERATOR_REF = "operator_authority_identity_rebind"
REASON = "Restore the reviewed staging provider authority identity."

T = TypeVar("T")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required(value: T | None) -> T:
    assert value is not None
    return value


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _pair_digest() -> str:
    return canonical_clerk_email_pair_sha256(
        (ClerkCohortIdentity(CURRENT_SUBJECT, CURRENT_EMAIL),)
    )


def _model_snapshot(model: Any) -> dict[str, Any]:
    return {
        column.name: getattr(model, column.name) for column in model.__table__.columns
    }


def _patch_staging_and_clerk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity_rebind, "is_staging", lambda: True)

    def clerk_resource_not_found() -> ClerkErrors:
        return ClerkErrors(
            data=ClerkErrorsData(
                errors=[
                    ClerkError(
                        message="not found",
                        long_message="not found",
                        code="resource_not_found",
                    )
                ]
            )
        )

    class ExactUsers:
        def get(self, *, user_id: str) -> Any:
            if user_id == CURRENT_SUBJECT:
                return SimpleNamespace(
                    id=CURRENT_SUBJECT,
                    banned=False,
                    locked=False,
                    primary_email_address_id="idn_current_primary",
                    email_addresses=[
                        SimpleNamespace(
                            id="idn_current_primary",
                            email_address=CURRENT_EMAIL,
                            verification=SimpleNamespace(status="verified"),
                        )
                    ],
                )
            if user_id == STALE_SUBJECT:
                raise clerk_resource_not_found()
            if user_id == PRIOR_REVOKED_SUBJECT:
                raise AssertionError("historical revoked identity must not be queried")
            raise AssertionError("unexpected exact Clerk subject lookup")

    monkeypatch.setattr(identity_rebind, "clerk", SimpleNamespace(users=ExactUsers()))


def _prepared_scope(
    authority_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    _patch_staging_and_clerk(monkeypatch)
    SQLModel.metadata.create_all(
        authority_session.get_bind(),
        tables=[
            cast(Any, Doctor).__table__,
            cast(Any, Patient).__table__,
            cast(Any, Appointment).__table__,
            cast(Any, SyntheticPreviewAppointmentFixture).__table__,
        ],
    )
    now = datetime.now(UTC)
    doctor = Doctor(
        clerk_id=CURRENT_SUBJECT,
        email=CURRENT_EMAIL,
        biography=None,
        last_active_at=now,
    )
    actor = AuthorityActor(actor_kind="human", status="active")
    tenant = AuthorityTenant(status="active")
    person = AuthorityPerson(lifecycle_state="patient", status="active")
    patient = Patient(
        doctor_id=doctor.id,
        name=SYNTHETIC_PATIENT_NAME,
        date_of_birth=SYNTHETIC_PATIENT_DATE_OF_BIRTH,
    )
    authority_session.add_all([doctor, actor, tenant, person])
    authority_session.flush()
    patient.doctor_id = doctor.id
    authority_session.add(patient)
    authority_session.flush()

    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=SYNTHETIC_APPOINTMENT_DATE,
        appointment_name=SYNTHETIC_APPOINTMENT_NAME,
        appointment_notes_preview=SYNTHETIC_APPOINTMENT_NOTES,
        created_by_system=SYNTHETIC_CREATED_BY_SYSTEM,
        external_id=None,
    )
    authority_session.add(appointment)
    authority_session.flush()
    fixture = SyntheticPreviewAppointmentFixture(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        fixture_version=SYNTHETIC_PREVIEW_FIXTURE_VERSION,
        synthetic_only=True,
        provisioned_by="operator_identity_rebind_fixture",
    )
    membership = AuthorityTenantMembership(
        actor_id=actor.id,
        tenant_id=tenant.id,
        membership_kind="provider",
        status="active",
    )
    origin = AuthorityPersonOrigin(
        person_id=person.id,
        origin="provider_entry",
        source_ref=str(patient.id),
    )
    binding = AuthorityPersonBinding(
        person_id=person.id,
        tenant_id=tenant.id,
        binding_ref=str(appointment.id),
        binding_kind="provider_record",
        status="active",
        verified_at=now,
    )
    stale_identity = AuthorityActorExternalIdentity(
        actor_id=actor.id,
        issuer=TEST_AUTHORITY_SETTINGS.clerk_issuer,
        subject=STALE_SUBJECT,
        verified_at=now - timedelta(days=5),
    )
    prior_revoked_at = now - timedelta(days=3)
    prior_identity = AuthorityActorExternalIdentity(
        actor_id=actor.id,
        issuer=TEST_AUTHORITY_SETTINGS.clerk_issuer,
        subject=PRIOR_REVOKED_SUBJECT,
        verified_at=now - timedelta(days=30),
        revoked_at=prior_revoked_at,
    )
    authority_session.add_all(
        [fixture, membership, origin, binding, stale_identity, prior_identity]
    )
    authority_session.flush()
    mount_session = MountContextSession(
        actor_id=actor.id,
        tenant_id=tenant.id,
        person_binding_id=binding.id,
        source_identity_id=stale_identity.id,
        session_kind="authenticated",
        surface_ceiling="provider_assistant",
        auth_tier_ceiling="provider",
        status="active",
        absolute_expires_at=now + timedelta(hours=2),
    )
    authority_session.add(mount_session)
    authority_session.flush()
    mount_token = MountContextToken(
        session_id=mount_session.id,
        token_hash="a" * 64,
        issued_at=now,
        refresh_after=now + timedelta(minutes=5),
        expires_at=now + timedelta(minutes=15),
    )
    authority_session.add(mount_token)
    authority_session.commit()
    return {
        "doctor": doctor,
        "fixture": fixture,
        "appointment": appointment,
        "patient": patient,
        "actor": actor,
        "tenant": tenant,
        "membership": membership,
        "person": person,
        "origin": origin,
        "binding": binding,
        "stale_identity": stale_identity,
        "prior_identity": prior_identity,
        "mount_session": mount_session,
        "mount_token": mount_token,
    }


def _plan_args(scope: dict[str, Any], session: Session) -> dict[str, Any]:
    return {
        "doctor_id": scope["doctor"].id,
        "appointment_id": scope["appointment"].id,
        "actor_id": scope["actor"].id,
        "operator_ref": OPERATOR_REF,
        "reason": REASON,
        "expected_current_clerk_pair_sha256": _pair_digest(),
        "expected_stale_subject_sha256": _digest(STALE_SUBJECT),
        "session": session,
        "settings": TEST_AUTHORITY_SETTINGS,
    }


def _all(session: Session, model: Any) -> list[Any]:
    return list(session.exec(select(model)).all())


def test_rebind_is_dry_run_first_atomic_minimized_and_idempotent(
    authority_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _prepared_scope(authority_session, monkeypatch)
    preserved_models = {
        key: _model_snapshot(scope[key])
        for key in (
            "doctor",
            "fixture",
            "appointment",
            "patient",
            "actor",
            "tenant",
            "membership",
            "person",
            "origin",
            "binding",
            "prior_identity",
            "mount_session",
            "mount_token",
        )
    }
    stale_verified_at = scope["stale_identity"].verified_at

    plan = plan_staging_provider_authority_identity_rebind(
        **_plan_args(scope, authority_session)
    )

    assert plan.identity_count == 2
    assert plan.active_identity_count == 1
    assert plan.revoked_identity_count == 1
    assert plan.already_applied is False
    assert len(_all(authority_session, AuthorityActorExternalIdentity)) == 2
    assert not [
        event
        for event in _all(authority_session, AuthorityAuditEvent)
        if event.event_type == AUTHORITY_IDENTITY_REBIND_EVENT_TYPE
    ]
    assert (
        _required(
            authority_session.get(
                AuthorityActorExternalIdentity, scope["stale_identity"].id
            )
        ).revoked_at
        is None
    )

    first = rebind_staging_provider_authority_identity(
        **_plan_args(scope, authority_session),
        confirmed_plan_sha256=plan.plan_sha256,
    )

    assert first.changed is True
    identities = list(
        authority_session.exec(
            select(AuthorityActorExternalIdentity).where(
                AuthorityActorExternalIdentity.actor_id == scope["actor"].id
            )
        ).all()
    )
    assert len(identities) == 3
    assert [
        identity.subject for identity in identities if identity.revoked_at is None
    ] == [CURRENT_SUBJECT]
    stale_after = next(
        identity for identity in identities if identity.subject == STALE_SUBJECT
    )
    assert stale_after.revoked_at is not None
    assert stale_after.verified_at == stale_verified_at
    assert (
        next(
            identity
            for identity in identities
            if identity.subject == PRIOR_REVOKED_SUBJECT
        ).revoked_at
        == scope["prior_identity"].revoked_at
    )

    model_types = {
        "doctor": Doctor,
        "fixture": SyntheticPreviewAppointmentFixture,
        "appointment": Appointment,
        "patient": Patient,
        "actor": AuthorityActor,
        "tenant": AuthorityTenant,
        "membership": AuthorityTenantMembership,
        "person": AuthorityPerson,
        "origin": AuthorityPersonOrigin,
        "binding": AuthorityPersonBinding,
        "prior_identity": AuthorityActorExternalIdentity,
        "mount_session": MountContextSession,
        "mount_token": MountContextToken,
    }
    for key, model_type in model_types.items():
        current = authority_session.get(model_type, scope[key].id)
        assert current is not None
        assert _model_snapshot(current) == preserved_models[key]

    receipts = [
        event
        for event in _all(authority_session, AuthorityAuditEvent)
        if event.event_type == AUTHORITY_IDENTITY_REBIND_EVENT_TYPE
    ]
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.event_source == "platform_authoritative"
    assert receipt.outcome == "accepted"
    assert receipt.actor_id == scope["actor"].id
    assert receipt.tenant_id == scope["tenant"].id
    assert receipt.person_id == scope["person"].id
    assert receipt.workload_client_id is None
    assert receipt.client_event_id is None
    assert receipt.interaction_id is None
    assert receipt.request_digest == plan.plan_sha256
    assert receipt.platform_refs == []
    receipt_payload = json.dumps(
        {
            column.name: str(getattr(receipt, column.name))
            for column in receipt.__table__.columns
        },
        sort_keys=True,
    )
    for protected_value in (
        CURRENT_SUBJECT,
        STALE_SUBJECT,
        PRIOR_REVOKED_SUBJECT,
        CURRENT_EMAIL,
    ):
        assert protected_value not in receipt_payload

    stale_revoked_at = stale_after.revoked_at
    current_identity = next(
        identity for identity in identities if identity.subject == CURRENT_SUBJECT
    )
    assert (
        len(
            {
                _naive_utc(stale_after.revoked_at),
                _naive_utc(current_identity.verified_at),
                _naive_utc(receipt.occurred_at),
                _naive_utc(receipt.recorded_at),
            }
        )
        == 1
    )
    current_verified_at = current_identity.verified_at
    second = rebind_staging_provider_authority_identity(
        **_plan_args(scope, authority_session),
        confirmed_plan_sha256=plan.plan_sha256,
    )

    assert second.changed is False
    assert second.plan_sha256 == first.plan_sha256
    assert len(_all(authority_session, AuthorityActorExternalIdentity)) == 3
    assert (
        len(
            [
                event
                for event in _all(authority_session, AuthorityAuditEvent)
                if event.event_type == AUTHORITY_IDENTITY_REBIND_EVENT_TYPE
            ]
        )
        == 1
    )
    assert (
        _required(
            authority_session.get(AuthorityActorExternalIdentity, stale_after.id)
        ).revoked_at
        == stale_revoked_at
    )
    assert (
        _required(
            authority_session.get(AuthorityActorExternalIdentity, current_identity.id)
        ).verified_at
        == current_verified_at
    )

    receipt.outcome = "changed"
    authority_session.add(receipt)
    with pytest.raises(RuntimeError, match="append-only"):
        authority_session.commit()
    authority_session.rollback()


def test_real_authority_exchange_moves_only_to_current_identity(
    authority_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _prepared_scope(authority_session, monkeypatch)
    record_ref = str(scope["appointment"].id)
    workload_client_id = "11111111-1111-4111-8111-111111111111"

    with pytest.raises(AuthorityRequestDeniedError):
        exchange_context_token(
            clerk_issuer=TEST_AUTHORITY_SETTINGS.clerk_issuer,
            clerk_subject=CURRENT_SUBJECT,
            requested_surface="provider_assistant",
            record_ref=record_ref,
            session=authority_session,
            settings=TEST_AUTHORITY_SETTINGS,
        )

    stale_issue = exchange_context_token(
        clerk_issuer=TEST_AUTHORITY_SETTINGS.clerk_issuer,
        clerk_subject=STALE_SUBJECT,
        requested_surface="provider_assistant",
        record_ref=record_ref,
        session=authority_session,
        settings=TEST_AUTHORITY_SETTINGS,
    )
    stale_before = resolve_mount_context(
        workload_client_id=workload_client_id,
        mount_context_token=stale_issue.context_token,
        client_turn_id=uuid4(),
        session=authority_session,
        settings=TEST_AUTHORITY_SETTINGS,
    )
    stale_token = authority_session.exec(
        select(MountContextToken).where(
            MountContextToken.token_hash
            == hashlib.sha256(stale_issue.context_token.encode()).hexdigest()
        )
    ).one()
    stale_mount = authority_session.get(MountContextSession, stale_token.session_id)
    assert stale_mount is not None
    assert stale_before.context.actor_id == str(scope["actor"].id)
    assert stale_before.context.person_id == scope["person"].id
    assert stale_mount.person_binding_id == scope["binding"].id
    assert stale_mount.source_identity_id == scope["stale_identity"].id
    stale_mount_snapshot = _model_snapshot(stale_mount)
    stale_token_snapshot = _model_snapshot(stale_token)

    plan = plan_staging_provider_authority_identity_rebind(
        **_plan_args(scope, authority_session)
    )
    result = rebind_staging_provider_authority_identity(
        **_plan_args(scope, authority_session),
        confirmed_plan_sha256=plan.plan_sha256,
    )
    assert result.changed is True

    assert provider_record_actor_ref(
        clerk_issuer=TEST_AUTHORITY_SETTINGS.clerk_issuer,
        clerk_subject=CURRENT_SUBJECT,
        record_ref=record_ref,
        session=authority_session,
        settings=TEST_AUTHORITY_SETTINGS,
    ) == str(scope["actor"].id)
    current_issue = exchange_context_token(
        clerk_issuer=TEST_AUTHORITY_SETTINGS.clerk_issuer,
        clerk_subject=CURRENT_SUBJECT,
        requested_surface="provider_assistant",
        record_ref=record_ref,
        session=authority_session,
        settings=TEST_AUTHORITY_SETTINGS,
    )
    current = resolve_mount_context(
        workload_client_id=workload_client_id,
        mount_context_token=current_issue.context_token,
        client_turn_id=uuid4(),
        session=authority_session,
        settings=TEST_AUTHORITY_SETTINGS,
    )
    current_token = authority_session.exec(
        select(MountContextToken).where(
            MountContextToken.token_hash
            == hashlib.sha256(current_issue.context_token.encode()).hexdigest()
        )
    ).one()
    current_mount = authority_session.get(MountContextSession, current_token.session_id)
    assert current_mount is not None
    assert current.context.surface == "provider_assistant"
    assert current.context.auth_tier == "provider"
    assert current.context.actor_id == str(scope["actor"].id)
    assert current.context.tenant_id == str(scope["tenant"].id)
    assert current.context.person_id == scope["person"].id
    assert current_mount.person_binding_id == scope["binding"].id

    with pytest.raises(AuthorityRequestDeniedError):
        exchange_context_token(
            clerk_issuer=TEST_AUTHORITY_SETTINGS.clerk_issuer,
            clerk_subject=STALE_SUBJECT,
            requested_surface="provider_assistant",
            record_ref=record_ref,
            session=authority_session,
            settings=TEST_AUTHORITY_SETTINGS,
        )
    with pytest.raises(AuthorityRequestDeniedError):
        resolve_mount_context(
            workload_client_id=workload_client_id,
            mount_context_token=stale_issue.context_token,
            client_turn_id=uuid4(),
            session=authority_session,
            settings=TEST_AUTHORITY_SETTINGS,
        )

    stale_mount_after = authority_session.get(MountContextSession, stale_mount.id)
    stale_token_after = authority_session.get(MountContextToken, stale_token.id)
    assert stale_mount_after is not None
    assert stale_token_after is not None
    assert _model_snapshot(stale_mount_after) == stale_mount_snapshot
    assert _model_snapshot(stale_token_after) == stale_token_snapshot


def test_rebind_refuses_wrong_plan_without_mutation(
    authority_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _prepared_scope(authority_session, monkeypatch)

    with pytest.raises(
        AuthorityIdentityRebindError,
        match="does not match the current reviewed plan",
    ):
        rebind_staging_provider_authority_identity(
            **_plan_args(scope, authority_session),
            confirmed_plan_sha256="f" * 64,
        )

    identities = _all(authority_session, AuthorityActorExternalIdentity)
    assert len(identities) == 2
    assert (
        _required(
            authority_session.get(
                AuthorityActorExternalIdentity, scope["stale_identity"].id
            )
        ).revoked_at
        is None
    )
    assert not [
        event
        for event in _all(authority_session, AuthorityAuditEvent)
        if event.event_type == AUTHORITY_IDENTITY_REBIND_EVENT_TYPE
    ]


def test_rebind_refuses_identity_shape_drift_without_selective_cleanup(
    authority_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _prepared_scope(authority_session, monkeypatch)
    authority_session.add(
        AuthorityActorExternalIdentity(
            actor_id=scope["actor"].id,
            issuer=TEST_AUTHORITY_SETTINGS.clerk_issuer,
            subject="user_unreviewed_extra",
            verified_at=datetime.now(UTC),
            revoked_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    authority_session.commit()

    with pytest.raises(
        AuthorityIdentityRebindError,
        match="did not match the reviewed rebind",
    ):
        plan_staging_provider_authority_identity_rebind(
            **_plan_args(scope, authority_session)
        )

    identities = _all(authority_session, AuthorityActorExternalIdentity)
    assert len(identities) == 3
    assert [
        identity.subject for identity in identities if identity.revoked_at is None
    ] == [STALE_SUBJECT]
    assert not [
        event
        for event in _all(authority_session, AuthorityAuditEvent)
        if event.event_type == AUTHORITY_IDENTITY_REBIND_EVENT_TYPE
    ]


def test_rebind_refuses_non_staging_and_unproved_live_clerk(
    authority_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _prepared_scope(authority_session, monkeypatch)
    monkeypatch.setattr(identity_rebind, "is_staging", lambda: False)
    with pytest.raises(AuthorityIdentityRebindError, match="restricted to staging"):
        plan_staging_provider_authority_identity_rebind(
            **_plan_args(scope, authority_session)
        )

    monkeypatch.setattr(identity_rebind, "is_staging", lambda: True)

    class FailedUsers:
        def get(self, *, user_id: str) -> Any:
            raise RuntimeError(f"synthetic raw provider failure for {user_id}")

    monkeypatch.setattr(
        identity_rebind,
        "clerk",
        SimpleNamespace(users=FailedUsers()),
    )
    with pytest.raises(
        AuthorityIdentityRebindError,
        match="exact current Clerk identity could not be verified",
    ) as exc_info:
        plan_staging_provider_authority_identity_rebind(
            **_plan_args(scope, authority_session)
        )
    assert "synthetic raw provider failure" not in str(exc_info.value)
    assert len(_all(authority_session, AuthorityActorExternalIdentity)) == 2


def test_rebind_requires_exact_resource_not_found_for_active_predecessor(
    authority_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _prepared_scope(authority_session, monkeypatch)

    class AmbiguousPredecessorUsers:
        def get(self, *, user_id: str) -> Any:
            if user_id == CURRENT_SUBJECT:
                return SimpleNamespace(
                    id=CURRENT_SUBJECT,
                    banned=False,
                    locked=False,
                    primary_email_address_id="idn_current_primary",
                    email_addresses=[
                        SimpleNamespace(
                            id="idn_current_primary",
                            email_address=CURRENT_EMAIL,
                            verification=SimpleNamespace(status="verified"),
                        )
                    ],
                )
            error = RuntimeError("lookalike resource-not-found result")
            error.data = SimpleNamespace(  # type: ignore[attr-defined]
                errors=[SimpleNamespace(code="resource_not_found")]
            )
            raise error

    monkeypatch.setattr(
        identity_rebind,
        "clerk",
        SimpleNamespace(users=AmbiguousPredecessorUsers()),
    )
    with pytest.raises(
        AuthorityIdentityRebindError,
        match="predecessor Clerk identity absence could not be proven",
    ):
        plan_staging_provider_authority_identity_rebind(
            **_plan_args(scope, authority_session)
        )

    assert len(_all(authority_session, AuthorityActorExternalIdentity)) == 2
    assert (
        _required(
            authority_session.get(
                AuthorityActorExternalIdentity, scope["stale_identity"].id
            )
        ).revoked_at
        is None
    )


def test_rebind_rolls_back_database_errors_without_leaking_values(
    authority_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _prepared_scope(authority_session, monkeypatch)
    plan = plan_staging_provider_authority_identity_rebind(
        **_plan_args(scope, authority_session)
    )
    original_commit = authority_session.commit

    def fail_commit() -> None:
        raise OperationalError(
            f"UPDATE secret={STALE_SUBJECT}",
            {},
            RuntimeError(CURRENT_EMAIL),
        )

    monkeypatch.setattr(authority_session, "commit", fail_commit)
    with pytest.raises(
        AuthorityIdentityRebindError,
        match="^authority identity database operation failed$",
    ) as exc_info:
        rebind_staging_provider_authority_identity(
            **_plan_args(scope, authority_session),
            confirmed_plan_sha256=plan.plan_sha256,
        )
    monkeypatch.setattr(authority_session, "commit", original_commit)

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    assert STALE_SUBJECT not in str(exc_info.value)
    assert CURRENT_EMAIL not in str(exc_info.value)
    identities = _all(authority_session, AuthorityActorExternalIdentity)
    assert len(identities) == 2
    assert (
        _required(
            authority_session.get(
                AuthorityActorExternalIdentity, scope["stale_identity"].id
            )
        ).revoked_at
        is None
    )
    assert not [
        event
        for event in _all(authority_session, AuthorityAuditEvent)
        if event.event_type == AUTHORITY_IDENTITY_REBIND_EVENT_TYPE
    ]


def test_rebind_rejects_clerk_error_with_more_than_one_error(
    authority_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _prepared_scope(authority_session, monkeypatch)

    class MultipleErrorUsers:
        def get(self, *, user_id: str) -> Any:
            if user_id == CURRENT_SUBJECT:
                return SimpleNamespace(
                    id=CURRENT_SUBJECT,
                    banned=False,
                    locked=False,
                    primary_email_address_id="idn_current_primary",
                    email_addresses=[
                        SimpleNamespace(
                            id="idn_current_primary",
                            email_address=CURRENT_EMAIL,
                            verification=SimpleNamespace(status="verified"),
                        )
                    ],
                )
            raise ClerkErrors(
                data=ClerkErrorsData(
                    errors=[
                        ClerkError(
                            message="not found",
                            long_message="not found",
                            code="resource_not_found",
                        ),
                        ClerkError(
                            message="ambiguous",
                            long_message="ambiguous",
                            code="resource_not_found",
                        ),
                    ]
                )
            )

    monkeypatch.setattr(
        identity_rebind,
        "clerk",
        SimpleNamespace(users=MultipleErrorUsers()),
    )
    with pytest.raises(
        AuthorityIdentityRebindError,
        match="predecessor Clerk identity absence could not be proven",
    ):
        plan_staging_provider_authority_identity_rebind(
            **_plan_args(scope, authority_session)
        )

    assert (
        _required(
            authority_session.get(
                AuthorityActorExternalIdentity, scope["stale_identity"].id
            )
        ).revoked_at
        is None
    )


def test_rebind_requires_patient_lifecycle_without_repairing_person(
    authority_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _prepared_scope(authority_session, monkeypatch)
    scope["person"].lifecycle_state = "prospect"
    authority_session.add(scope["person"])
    authority_session.commit()

    with pytest.raises(
        AuthorityIdentityRebindError,
        match="authority person is not an active patient",
    ):
        plan_staging_provider_authority_identity_rebind(
            **_plan_args(scope, authority_session)
        )

    assert (
        _required(
            authority_session.get(AuthorityPerson, scope["person"].id)
        ).lifecycle_state
        == "prospect"
    )
    assert (
        _required(
            authority_session.get(
                AuthorityActorExternalIdentity, scope["stale_identity"].id
            )
        ).revoked_at
        is None
    )


def test_rebind_requires_exact_current_subject_and_verified_primary_email(
    authority_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _prepared_scope(authority_session, monkeypatch)

    class WrongCurrentEmailUsers:
        def get(self, *, user_id: str) -> Any:
            assert user_id == CURRENT_SUBJECT
            return SimpleNamespace(
                id=CURRENT_SUBJECT,
                banned=False,
                locked=False,
                primary_email_address_id="idn_current_primary",
                email_addresses=[
                    SimpleNamespace(
                        id="idn_current_primary",
                        email_address="different-provider@example.test",
                        verification=SimpleNamespace(status="verified"),
                    )
                ],
            )

    monkeypatch.setattr(
        identity_rebind,
        "clerk",
        SimpleNamespace(users=WrongCurrentEmailUsers()),
    )
    with pytest.raises(
        AuthorityIdentityRebindError,
        match="did not match the confirmed digest",
    ):
        plan_staging_provider_authority_identity_rebind(
            **_plan_args(scope, authority_session)
        )

    assert len(_all(authority_session, AuthorityActorExternalIdentity)) == 2


def test_cli_uses_fixed_value_free_output_and_apply_confirmations(
    authority_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = _prepared_scope(authority_session, monkeypatch)
    plan_sha256 = "b" * 64
    monkeypatch.setattr(cli_module, "global_session", lambda: nullcontext())
    monkeypatch.setattr(
        identity_rebind,
        "plan_staging_provider_authority_identity_rebind",
        lambda **_: AuthorityIdentityRebindPlan(
            plan_sha256=plan_sha256,
            identity_count=2,
            active_identity_count=1,
            revoked_identity_count=1,
            already_applied=False,
        ),
    )
    monkeypatch.setattr(
        identity_rebind,
        "rebind_staging_provider_authority_identity",
        lambda **_: AuthorityIdentityRebindResult(
            plan_sha256=plan_sha256,
            changed=True,
        ),
    )
    command = [
        "rebind-staging-provider-authority-identity",
        "--doctor-id",
        str(scope["doctor"].id),
        "--appointment-id",
        str(scope["appointment"].id),
        "--actor-id",
        str(scope["actor"].id),
        "--operator-ref",
        OPERATOR_REF,
        "--reason",
        REASON,
        "--current-clerk-pair-sha256",
        _pair_digest(),
        "--stale-subject-sha256",
        _digest(STALE_SUBJECT),
    ]
    runner = CliRunner()

    dry_run = runner.invoke(app, command)
    refused = runner.invoke(app, [*command, "--apply"])
    applied = runner.invoke(
        app,
        [
            *command,
            "--apply",
            "--confirm-staging-only",
            "--confirm-revoke-only-stale-identity",
            "--confirm-plan-sha256",
            plan_sha256,
        ],
    )

    assert dry_run.exit_code == 0
    assert dry_run.output == (
        "DRY RUN: staging authority identity rebind state=ready identities=2 "
        "active=1 revoked=1 "
        f"plan_sha256={plan_sha256}; no changes made.\n"
    )
    assert refused.exit_code == 1
    assert refused.output == (
        "Authority identity rebind refused; no identity values were printed.\n"
    )
    assert applied.exit_code == 0
    assert applied.output == (
        f"Authority identity rebind applied; plan_sha256={plan_sha256}.\n"
    )
    for output in (dry_run.output, refused.output, applied.output):
        assert CURRENT_SUBJECT not in output
        assert STALE_SUBJECT not in output
        assert PRIOR_REVOKED_SUBJECT not in output
        assert CURRENT_EMAIL not in output
