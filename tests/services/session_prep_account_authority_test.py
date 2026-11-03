"""Active-account Session Prep authority and Scribe namespace continuity."""

from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

import pytest

import app.configuration.session_prep as configuration
import app.services.authority as authority
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.configuration.session_prep import SessionPrepProviderSettings
from app.services import scribe_encounters
from app.services.companion_provider_provisioning import (
    CompanionProvisioningError,
    provision_companion_provider,
)
from app.services.companion_records import RecordBudget
from app.services.provider_record_authority import (
    ProviderRecordScopeDenied,
    ensure_provider_record_scope,
    validate_provider_capture_record_scope,
)
from app.services.scribe_encounter_contract import REQUEST_ADAPTER
from app.services.session_prep_note_corrections import correct_source_note
from app.services.session_prep_note_sources import NoteSourceDenied, note_content_sha256

from activemodel.session_manager import get_session, global_session
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityPersonBinding,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.practice import Practice, PracticeMember
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from app.models.scribe_cancellation import scribe_cancellation_operation
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from app.models.visit_audio_file import VisitAudioFile
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel import col

from tests.factories import (
    AppointmentFactory,
    DoctorFactory,
    PatientFactory,
    save_practice_member,
)
from tests.routes.utils import provision_practice_in_force
from tests.services.session_prep_authority_test import AUTHORITY_SETTINGS, _token


@pytest.fixture
def personal_account(monkeypatch):
    doctor = DoctorFactory.build(
        onboarding_completed_at=None,
        last_active_at=datetime.now(UTC),
        email="synthetic_account_" + uuid4().hex + "@synthetic.invalid",
    ).save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id
    ).save()
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(True),
    )
    return doctor, patient, appointment


def _context(token):
    return authority.bootstrap_mount_context(
        mount_context_token=token, settings=AUTHORITY_SETTINGS
    )


def _mapping(appointment):
    record = ProviderRecordAuthority.one(appointment_id=appointment.id)
    return ProviderPracticeAuthority.one(id=record.practice_authority_id)


@pytest.mark.parametrize("actor_state", ["own", "foreign", "cancelled"])
def test_no_practice_upload_source_correction_preserves_exact_owner_and_cancellation(
    personal_account,
    monkeypatch,
    actor_state,
):
    doctor, patient, appointment = personal_account
    notes_before = AIVisitNote.count()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type="Synthetic source note",
        fields={"Assessment": "Recorded assessment"},
    ).save()
    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        duration=23,
        transcript_data=[],
    ).save()
    VisitAudioFile(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_id=appointment.id,
        azure_file_name="synthetic-account-correction.wav",
    ).save()
    original_fields = {"Assessment": "Synthetic fatigue, source draft."}
    note = AIVisitNote(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=schema.id,
        note=original_fields,
    ).save()
    actor = (
        doctor
        if actor_state != "foreign"
        else DoctorFactory.build(
            onboarding_completed_at=None,
            last_active_at=datetime.now(UTC),
            email="synthetic_foreign_" + uuid4().hex + "@synthetic.invalid",
        ).save()
    )
    with get_session() as session:
        if actor_state == "cancelled":
            import app.configuration.scribe_cancellation as cancellation_configuration
            from app.services.scribe_cancellation_contract import CancellationSettings

            monkeypatch.setattr(
                cancellation_configuration,
                "SCRIBE_CANCELLATION_SETTINGS",
                CancellationSettings(ledger_ready=True),
            )
            accepted = datetime.now(UTC) - timedelta(minutes=1)
            session.execute(
                scribe_cancellation_operation.insert().values(
                    id=uuid4(),
                    caller_sha256="a" * 64,
                    doctor_ref=str(doctor.id),
                    request_key_sha256="b" * 64,
                    preview_sha256="c" * 64,
                    inventory_sha256="d" * 64,
                    inventory=[],
                    policy={
                        "grace_days": 30,
                        "phi_scope": "core-and-scribe-clinical-records",
                        "approval_sha256": "e" * 64,
                    },
                    accepted_at=accepted,
                    export_until=accepted + timedelta(days=30),
                    phase="read-only",
                )
            )
        args = dict(
            session=session,
            doctor=actor,
            note_id=note.id,
            expected_content_sha256=note_content_sha256(original_fields),
            fields={"Assessment": "Synthetic fatigue, corrected from encounter."},
        )
        if actor_state == "own":
            corrected = correct_source_note(**args)
            assert corrected.id != note.id
            assert corrected.doctor_id == doctor.id
            assert corrected.note == args["fields"]
            session.commit()
            retained = AIVisitNote.one(id=note.id)
            assert retained.note == original_fields
            assert retained.superseded_at is not None
            assert AIVisitNote.count() == notes_before + 2
        else:
            with pytest.raises(NoteSourceDenied):
                correct_source_note(**args)
            assert AIVisitNote.count() == notes_before + 1
    assert PracticeMember.get(doctor_id=doctor.id) is None


def _link_args(doctor, practice, mapping, scribe_tenant_id):
    return dict(
        doctor_id=doctor.id,
        practice_id=practice.id,
        tenant_id=mapping.tenant_id,
        scribe_tenant_id=scribe_tenant_id,
        verified_primary_email=doctor.email,
        verified_clerk_subject=doctor.clerk_id,
        email_confirmation=doctor.email,
        review_ref="synthetic_review_" + uuid4().hex,
        operator_ref="synthetic_operator_" + uuid4().hex,
        settings=AUTHORITY_SETTINGS,
    )


def _shared_practice_case(personal_account, other_namespace, own_namespace="account"):
    doctor, _patient, visit = personal_account
    if own_namespace == "account":
        original = _context(_token(doctor, str(visit.id)))
        personal_mapping = _mapping(visit)
    other = DoctorFactory.build(
        onboarding_completed_at=None,
        last_active_at=datetime.now(UTC),
        email="synthetic_other_" + uuid4().hex + "@synthetic.invalid",
    ).save()
    patient = PatientFactory.build(doctor_id=other.id).save()
    other_visit = AppointmentFactory.build(
        doctor_id=other.id,
        patient_id=patient.id,
    ).save()
    if other_namespace == "account":
        _token(other, str(other_visit.id))
    other.onboarding_completed_at = datetime.now(UTC)
    other.save()
    practice = provision_practice_in_force(other)
    if other_namespace == "practice":
        _token(other, str(other_visit.id))
    other_mapping = _mapping(other_visit)
    doctor.onboarding_completed_at = datetime.now(UTC)
    doctor.save()
    save_practice_member(
        PracticeMember(
            doctor_id=doctor.id,
            practice_id=practice.id,
            role="workforce",
            attested_at=datetime.now(UTC),
        )
    )
    if own_namespace == "practice":
        original = _context(_token(doctor, str(visit.id)))
        personal_mapping = _mapping(visit)
    return doctor, personal_mapping, original, other, other_mapping, practice


@pytest.mark.parametrize(
    "own_namespace,other_namespace",
    [
        ("account", "account"),
        ("account", "practice"),
        ("practice", "account"),
    ],
)
def test_scribe_same_real_practice_keeps_separate_verified_core_namespaces(
    personal_account,
    own_namespace,
    other_namespace,
):
    doctor, own_mapping, original, other, other_mapping, practice = (
        _shared_practice_case(personal_account, other_namespace, own_namespace)
    )
    scribe_tenant_id = uuid4()
    with global_session() as session:
        other_args = _link_args(other, practice, other_mapping, scribe_tenant_id)
        other_plan = provision_companion_provider(session, **other_args)
        other_link = provision_companion_provider(
            session,
            **other_args,
            apply=True,
            confirm_plan_sha256=other_plan.plan_sha256,
        )
        own_args = _link_args(doctor, practice, own_mapping, scribe_tenant_id)
        own_plan = provision_companion_provider(session, **own_args)
        own_link = provision_companion_provider(
            session,
            **own_args,
            apply=True,
            confirm_plan_sha256=own_plan.plan_sha256,
        )
        first = session.get(ScribeCaptureProviderLink, other_link.provider_link_ref)
        second = session.get(ScribeCaptureProviderLink, own_link.provider_link_ref)
        assert first is not None and second is not None
        assert first.practice_id == second.practice_id == practice.id
        assert first.scribe_tenant_id == second.scribe_tenant_id == scribe_tenant_id
        assert first.tenant_id == other_mapping.tenant_id
        assert second.tenant_id == own_mapping.tenant_id
        assert first.tenant_id != second.tenant_id
    assert ProviderPracticeAuthority.count() == 2
    assert (
        _context(_token(doctor, str(personal_account[2].id))).tenant_id
        == original.tenant_id
    )


@pytest.mark.parametrize(
    "defect",
    [
        "foreign_practice",
        "missing_mapping",
        "wrong_doctor",
        "wrong_scribe_tenant",
        "foreign_doctor_in_own_tenant",
    ],
)
def test_scribe_shared_practice_denies_unverified_related_namespaces(
    personal_account,
    defect,
):
    doctor, own_mapping, _original, other, other_mapping, practice = (
        _shared_practice_case(personal_account, "account")
    )
    identity = AuthorityActorExternalIdentity.one(subject=other.clerk_id)
    tenant_id = other_mapping.tenant_id
    practice_id = practice.id
    row_doctor_id = other.id
    scribe_tenant_id = uuid4()
    if defect == "foreign_practice":
        third = DoctorFactory.build().save()
        practice_id = provision_practice_in_force(third).id
    elif defect == "missing_mapping":
        tenant_id = AuthorityTenant(status="active").save().id
    elif defect == "wrong_doctor":
        row_doctor_id = DoctorFactory.build().save().id
    elif defect == "foreign_doctor_in_own_tenant":
        tenant_id = own_mapping.tenant_id
    ScribeCaptureProviderLink(
        actor_id=identity.actor_id,
        tenant_id=tenant_id,
        source_identity_id=identity.id,
        doctor_id=row_doctor_id,
        practice_id=practice_id,
        scribe_tenant_id=uuid4()
        if defect == "wrong_scribe_tenant"
        else scribe_tenant_id,
        reviewed_at=datetime.now(UTC),
        review_ref="synthetic_review_" + uuid4().hex,
    ).save()
    with get_session() as session:
        with pytest.raises(
            CompanionProvisioningError, match="practice_correspondence_review_required"
        ):
            provision_companion_provider(
                session,
                **_link_args(doctor, practice, own_mapping, scribe_tenant_id),
            )


def test_new_active_account_gets_private_prep_without_practice_or_legal_writes(
    personal_account,
):
    doctor, _patient, appointment = personal_account
    practices_before = Practice.count()
    members_before = PracticeMember.count()
    context = _context(_token(doctor, str(appointment.id)))

    assert "session-prep:read" in context.capabilities
    assert context.session_prep_context_ref is not None
    mapping = _mapping(appointment)
    assert mapping.doctor_id == doctor.id
    assert mapping.practice_id is None
    assert str(mapping.tenant_id) == context.tenant_id
    assert Practice.count() == practices_before
    assert PracticeMember.count() == members_before
    assert doctor.onboarding_completed_at is None
    assert ProviderRecordAuthority.count() == 1


@pytest.mark.parametrize("owners", ["neither", "both"])
def test_native_namespace_requires_exactly_one_owner(personal_account, owners):
    doctor, _patient, _appointment = personal_account
    practice = provision_practice_in_force(doctor)
    tenant = AuthorityTenant(status="active").save()
    with get_session() as session:
        with pytest.raises(IntegrityError, match="exactly_one_owner"):
            with session.begin_nested():
                session.add(
                    ProviderPracticeAuthority(
                        tenant_id=tenant.id,
                        practice_id=practice.id if owners == "both" else None,
                        doctor_id=doctor.id if owners == "both" else None,
                    )
                )
                session.flush()


def test_native_account_namespace_retains_immutable_owner(personal_account):
    doctor, _patient, appointment = personal_account
    _token(doctor, str(appointment.id))
    mapping = _mapping(appointment)
    practice = provision_practice_in_force(doctor)
    with get_session() as session:
        with pytest.raises(DBAPIError, match="immutable"):
            with session.begin_nested():
                session.execute(
                    update(ProviderPracticeAuthority)
                    .where(col(ProviderPracticeAuthority.id) == mapping.id)
                    .values(doctor_id=None, practice_id=practice.id)
                )
    retained = _mapping(appointment)
    assert retained.doctor_id == doctor.id
    assert retained.practice_id is None


def test_personal_scope_reuses_tenant_and_person_for_own_followup(personal_account):
    doctor, patient, appointment = personal_account
    first = _context(_token(doctor, str(appointment.id)))
    followup = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id
    ).save()

    second = _context(_token(doctor, str(followup.id)))

    assert first.tenant_id == second.tenant_id
    assert first.person_id == second.person_id
    assert first.session_prep_context_ref != second.session_prep_context_ref
    assert ProviderPracticeAuthority.count() == 1


def test_scribe_onboarding_preserves_existing_account_tenant_and_prep(
    personal_account,
):
    doctor, patient, appointment = personal_account
    token = _token(doctor, str(appointment.id))
    first = _context(token)
    mapping = _mapping(appointment)
    doctor.onboarding_completed_at = datetime.now(UTC)
    doctor.save()
    provision_practice_in_force(doctor)
    followup = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id
    ).save()

    with get_session() as session:
        scope = ensure_provider_record_scope(
            session=session,
            clerk_issuer=AUTHORITY_SETTINGS.clerk_issuer,
            clerk_subject=doctor.clerk_id,
            record_ref=str(followup.id),
        )
        assert scope.practice_authority_id == mapping.id
        binding = session.get(AuthorityPersonBinding, scope.binding_id)
        assert binding is not None
        assert (
            validate_provider_capture_record_scope(
                session=session,
                binding=binding,
                actor_id=binding.actor_id,
                clerk_subject=doctor.clerk_id,
            )
            is not None
        )
        session.commit()

    assert _context(token).session_prep_context_ref == first.session_prep_context_ref
    assert _context(_token(doctor, str(followup.id))).tenant_id == first.tenant_id
    assert ProviderPracticeAuthority.count() == 1
    assert (
        AuthorityTenantMembership.where(
            col(AuthorityTenantMembership.actor_id) == first.actor_id,
            col(AuthorityTenantMembership.status) == "active",
        ).count()
        == 1
    )
    assert (
        AuthorityAuditEvent.where(
            col(AuthorityAuditEvent.event_type)
            == "authority.provider_membership.replaced"
        ).count()
        == 0
    )


def test_personal_namespace_does_not_bypass_scribes_practice_policy(personal_account):
    doctor, patient, appointment = personal_account
    _token(doctor, str(appointment.id))
    followup = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id
    ).save()

    with get_session() as session:
        with pytest.raises(ProviderRecordScopeDenied):
            ensure_provider_record_scope(
                session=session,
                clerk_issuer=AUTHORITY_SETTINGS.clerk_issuer,
                clerk_subject=doctor.clerk_id,
                record_ref=str(followup.id),
            )
        session.rollback()

    assert ProviderRecordAuthority.count() == 1


@pytest.mark.parametrize("membership_state", ["missing", "revoked", "competing"])
def test_scribe_preserves_personal_membership_without_creating_or_reconciling_it(
    personal_account,
    membership_state,
):
    doctor, patient, appointment = personal_account
    context = _context(_token(doctor, str(appointment.id)))
    doctor.onboarding_completed_at = datetime.now(UTC)
    doctor.save()
    provision_practice_in_force(doctor)
    member = AuthorityTenantMembership.one(actor_id=context.actor_id)
    if membership_state == "missing":
        with get_session() as session:
            session.delete(member)
            session.commit()
    elif membership_state == "revoked":
        member.status = "revoked"
        member.save()
    else:
        AuthorityTenantMembership(
            actor_id=member.actor_id,
            tenant_id=AuthorityTenant(status="active").save().id,
            membership_kind="provider",
            status="active",
        ).save()
    before = AuthorityTenantMembership.count()
    visit = AppointmentFactory.build(doctor_id=doctor.id, patient_id=patient.id).save()
    with get_session() as session:
        with pytest.raises(ProviderRecordScopeDenied):
            ensure_provider_record_scope(
                session=session,
                clerk_issuer=AUTHORITY_SETTINGS.clerk_issuer,
                clerk_subject=doctor.clerk_id,
                record_ref=str(visit.id),
            )
    assert AuthorityTenantMembership.count() == before
    assert (
        AuthorityAuditEvent.where(
            col(AuthorityAuditEvent.event_type)
            == "authority.provider_membership.replaced",
        ).count()
        == 0
    )


def test_personal_prep_survives_real_scribe_onboarding_creator_lookup_and_catalog(
    personal_account,
):
    doctor, patient, appointment = personal_account
    token = _token(doctor, str(appointment.id))
    prep = _context(token)
    original_mapping = _mapping(appointment)
    doctor.onboarding_completed_at = datetime.now(UTC)
    doctor.save()
    practice = provision_practice_in_force(doctor)
    assert doctor.email
    args = dict(
        doctor_id=doctor.id,
        practice_id=practice.id,
        tenant_id=original_mapping.tenant_id,
        scribe_tenant_id=uuid4(),
        verified_primary_email=doctor.email,
        verified_clerk_subject=doctor.clerk_id,
        email_confirmation=doctor.email,
        review_ref="synthetic_review_" + uuid4().hex,
        operator_ref="synthetic_operator_" + uuid4().hex,
        settings=AUTHORITY_SETTINGS,
    )
    with global_session() as session:
        plan = provision_companion_provider(session, **args)
        assert plan.authority_records_to_create == ()
        linked = provision_companion_provider(
            session, **args, apply=True, confirm_plan_sha256=plan.plan_sha256
        )
        assert linked.provider_link_ref is not None

        def invoke(action, **values):
            request = REQUEST_ADAPTER.validate_python(
                dict(
                    action=action,
                    expected_actor_ref=prep.actor_id,
                    expected_tenant_ref=prep.tenant_id,
                    request_nonce=str(uuid4()),
                )
                | values
            )
            return scribe_encounters.encounter_workflow(
                issuer=AUTHORITY_SETTINGS.clerk_issuer,
                subject=doctor.clerk_id,
                session_id="synthetic_session_prep_onboarding",
                request=request,
                settings_provider=lambda: ScribeEncounterSettings(enabled=True),
                authority_settings=AUTHORITY_SETTINGS,
                deadline=RecordBudget(monotonic() + 15),
                existing_session=session,
            )

        created = invoke(
            "create",
            creation_key=str(uuid4()),
            appointment_at=datetime.now(UTC).isoformat(),
            patient={"kind": "existing", "patient_ref": str(patient.id)},
        )["encounter"]
        assert created["patient_ref"] == str(patient.id)
        assert (
            invoke("lookup", workspace_id=created["workspace_id"])["encounter"]
            == created
        )
        assert invoke("list")["encounters"] == [created]

    assert _context(token).session_prep_context_ref == prep.session_prep_context_ref
    assert (
        _context(_token(doctor, created["appointment_ref"])).tenant_id == prep.tenant_id
    )
    assert ProviderPracticeAuthority.count() == 1
    assert (
        AuthorityTenantMembership.where(
            col(AuthorityTenantMembership.actor_id) == prep.actor_id,
            col(AuthorityTenantMembership.status) == "active",
        ).count()
        == 1
    )


def test_scribe_link_cannot_claim_another_accounts_private_mapping(personal_account):
    doctor, _patient, appointment = personal_account
    _token(doctor, str(appointment.id))
    mapping = _mapping(appointment)
    outsider = DoctorFactory.build(
        onboarding_completed_at=datetime.now(UTC),
        last_active_at=datetime.now(UTC),
        email="synthetic_outsider_" + uuid4().hex + "@synthetic.invalid",
    ).save()
    practice = provision_practice_in_force(outsider)
    with get_session() as session:
        with pytest.raises(
            CompanionProvisioningError,
            match="existing_core_practice_correspondence_required",
        ):
            provision_companion_provider(
                session,
                doctor_id=outsider.id,
                practice_id=practice.id,
                tenant_id=mapping.tenant_id,
                scribe_tenant_id=uuid4(),
                verified_primary_email=outsider.email,
                verified_clerk_subject=outsider.clerk_id,
                email_confirmation=outsider.email,
                review_ref="synthetic_review_" + uuid4().hex,
                operator_ref="synthetic_operator_" + uuid4().hex,
                settings=AUTHORITY_SETTINGS,
            )


@pytest.mark.parametrize("namespace", ["account", "practice"])
def test_foreign_provider_namespace_is_not_reused_for_own_new_record(
    personal_account,
    namespace,
):
    doctor, _patient, appointment = personal_account
    if namespace == "practice":
        doctor.onboarding_completed_at = datetime.now(UTC)
        doctor.save()
        provision_practice_in_force(doctor)
    _token(doctor, str(appointment.id))
    mapping = _mapping(appointment)
    outsider = DoctorFactory.build(onboarding_completed_at=None).save()
    patient = PatientFactory.build(doctor_id=outsider.id).save()
    other_visit = AppointmentFactory.build(
        doctor_id=outsider.id, patient_id=patient.id
    ).save()
    actor = AuthorityActor(actor_kind="human", status="active").save()
    AuthorityActorExternalIdentity(
        actor_id=actor.id,
        issuer=AUTHORITY_SETTINGS.clerk_issuer,
        subject=outsider.clerk_id,
        verified_at=datetime.now(UTC),
    ).save()
    AuthorityTenantMembership(
        actor_id=actor.id,
        tenant_id=mapping.tenant_id,
        membership_kind="provider",
        status="active",
    ).save()

    with pytest.raises(authority.AuthorityRequestDeniedError):
        _token(outsider, str(other_visit.id))

    assert ProviderRecordAuthority.count() == 1
    assert ProviderPracticeAuthority.count() == 1


def test_foreign_visit_stays_denied_with_all_active_accounts_enabled(personal_account):
    doctor, _patient, appointment = personal_account
    _token(doctor, str(appointment.id))
    outsider = DoctorFactory.build(onboarding_completed_at=None).save()

    with pytest.raises(authority.AuthorityRequestDeniedError):
        _token(outsider, str(appointment.id))

    assert ProviderRecordAuthority.count() == 1


def test_account_deactivation_revokes_existing_prep(personal_account):
    doctor, _patient, appointment = personal_account
    token = _token(doctor, str(appointment.id))
    assert _context(token).session_prep_context_ref is not None
    doctor.deleted_at = datetime.now(UTC)
    doctor.save()

    with pytest.raises(authority.AuthorityRequestDeniedError):
        _context(token)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        _token(doctor, str(appointment.id))


@pytest.mark.parametrize("change", ["membership_revoked", "tenant_inactive"])
def test_direct_ensure_preserves_explicit_record_authority_revocation(
    personal_account, change
):
    from app.services.provider_record_authority import ensure_session_prep_record_scope

    from app.models.authority import AuthorityTenant

    doctor, _patient, appointment = personal_account
    context = _context(_token(doctor, str(appointment.id)))
    if change == "membership_revoked":
        member = AuthorityTenantMembership.one(
            actor_id=context.actor_id, tenant_id=context.tenant_id
        )
        member.status = "revoked"
        member.save()
    else:
        tenant = AuthorityTenant.one(id=context.tenant_id)
        tenant.status = "revoked"
        tenant.save()

    with get_session() as session:
        with pytest.raises(ProviderRecordScopeDenied):
            ensure_session_prep_record_scope(
                session=session,
                clerk_issuer=AUTHORITY_SETTINGS.clerk_issuer,
                clerk_subject=doctor.clerk_id,
                record_ref=str(appointment.id),
            )
        session.rollback()
    assert ProviderRecordAuthority.count() == 1


@pytest.mark.parametrize("disabled", ["doctor", "seat"])
def test_private_namespace_keeps_scribes_existing_inactive_account_denials(
    personal_account, disabled
):
    from tests.factories import save_practice_member

    doctor, patient, appointment = personal_account
    _token(doctor, str(appointment.id))
    doctor.onboarding_completed_at = datetime.now(UTC)
    doctor.save()
    provision_practice_in_force(doctor)
    followup = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id
    ).save()
    if disabled == "doctor":
        doctor.deleted_at = datetime.now(UTC)
        doctor.save()
    else:
        member = PracticeMember.one(doctor_id=doctor.id)
        member.seat_state = "inactive"
        save_practice_member(member)

    with get_session() as session:
        with pytest.raises(ProviderRecordScopeDenied):
            ensure_provider_record_scope(
                session=session,
                clerk_issuer=AUTHORITY_SETTINGS.clerk_issuer,
                clerk_subject=doctor.clerk_id,
                record_ref=str(followup.id),
            )
        session.rollback()
    assert ProviderRecordAuthority.count() == 1
