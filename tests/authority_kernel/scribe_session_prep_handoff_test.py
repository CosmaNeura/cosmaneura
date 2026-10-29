"""Scribe capture to later Session Prep with real Core persistence and authority.

Only Scribe transport and model responses are synthetic. Run against the owned
PostgreSQL database with --confcutdir=tests/authority_kernel.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.configuration.session_prep as configuration
from app.configuration.scribe_capture import ScribeCaptureSettings
from app.configuration.session_prep import SessionPrepProviderSettings
from app.services import authority
from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_note_edits as edits
from app.services import scribe_capture_notes as notes
from app.services.provider_record_authority import validate_provider_record_scope
from app.services.scribe_capture_scope import resolve_capture_binding
from app.services.session_prep import SessionPrepScopeDenied
from app.services.session_prep_note_sources import (
    finalize_note_for_session_prep,
    load_reviewed_note_sources,
    note_content_sha256,
)
from app.services.session_prep_worker import run_one_build

from activemodel.session_manager import _session_context
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.authority import AuthorityActorExternalIdentity
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from app.models.provider_record_authority import (
    ProviderPracticeAuthority,
    ProviderRecordAuthority,
)
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlmodel import Session, select

import tests.services.session_prep_runtime_test as prep
from tests.authority_kernel.scribe_capture_admission_test import FakeScribe
from tests.authority_kernel.scribe_capture_notes_test import setup as raw_note_setup
from tests.authority_kernel.scribe_capture_scope_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_configured_capture_test import note_setup
from tests.services.session_prep_authority_test import AUTHORITY_SETTINGS, ISSUER

capture_engine = _capture_engine
SCRIBE_WORKLOAD = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def real_record_capture(capture_engine, monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(prep, "NOW", now)
    monkeypatch.setattr(authority, "datetime", prep._AuthorityClock)
    # The isolated kernel suite disables real-record lookup by default. This
    # integration test must exercise it, including dynamic Prep capability.
    monkeypatch.setattr(
        authority, "validate_provider_record_scope", validate_provider_record_scope
    )
    with Session(capture_engine, expire_on_commit=False) as session:
        context = _session_context.set(session)
        try:
            doctor = Doctor(clerk_id=f"user_handoff_{uuid4().hex}", biography=None)
            practice = Practice(
                legal_name="Synthetic handoff practice",
                email_domain="handoff.invalid",
                join_code=uuid4().hex[:16],
                baa_accepted_at=now,
                baa_version=BAA_CURRENT_REFERENCE,
                billing_exempt=True,
            )
            session.add_all([doctor, practice])
            session.flush()
            patient = Patient(
                doctor_id=doctor.id,
                name="Synthetic Handoff Patient",
                date_of_birth=datetime(1990, 1, 1, tzinfo=UTC),
            )
            session.add(patient)
            session.flush()
            previous = Appointment(
                doctor_id=doctor.id,
                patient_id=patient.id,
                appointment_date=now - timedelta(days=7),
                external_id=None,
            )
            target = Appointment(
                doctor_id=doctor.id,
                patient_id=patient.id,
                appointment_date=now,
                external_id=None,
            )
            assert lock_practice_seats(session, practice.id) is not None
            session.add_all(
                [
                    previous,
                    target,
                    PracticeMember(
                        practice_id=practice.id,
                        doctor_id=doctor.id,
                        role="owner",
                        attested_at=now,
                    ),
                ]
            )
            session.commit()
            monkeypatch.setattr(
                configuration,
                "SESSION_PREP_PROVIDER_SETTINGS",
                SessionPrepProviderSettings(True),
            )
            token = authority.exchange_context_token(
                clerk_issuer=ISSUER,
                clerk_subject=doctor.clerk_id,
                requested_surface="provider_assistant",
                record_ref=str(previous.id),
                settings=AUTHORITY_SETTINGS,
            ).context_token
            record = session.exec(
                select(ProviderRecordAuthority).where(
                    ProviderRecordAuthority.appointment_id == previous.id
                )
            ).one()
            mapping = session.get(
                ProviderPracticeAuthority, record.practice_authority_id
            )
            identity = session.exec(
                select(AuthorityActorExternalIdentity).where(
                    AuthorityActorExternalIdentity.issuer == ISSUER,
                    AuthorityActorExternalIdentity.subject == doctor.clerk_id,
                )
            ).one()
            assert mapping is not None
            session.add(
                ScribeCaptureProviderLink(
                    actor_id=identity.actor_id,
                    tenant_id=mapping.tenant_id,
                    source_identity_id=identity.id,
                    doctor_id=doctor.id,
                    practice_id=practice.id,
                    scribe_tenant_id=uuid4(),
                    reviewed_at=now,
                    review_ref=f"synthetic_handoff_{uuid4().hex}",
                )
            )
            session.commit()
            turn = uuid4()
            resolution = authority.resolve_mount_context(
                workload_client_id=SCRIBE_WORKLOAD,
                mount_context_token=token,
                client_turn_id=turn,
                settings=AUTHORITY_SETTINGS,
            )
            caller = admission.CaptureCaller(
                workload_client_id=SCRIBE_WORKLOAD,
                mount_context_token=token,
                client_turn_id=turn,
                interaction_ref=resolution.interaction_ref,
                settings=AUTHORITY_SETTINGS,
                capture_settings=ScribeCaptureSettings(enabled=True),
            )
            binding = resolve_capture_binding(
                workload_client_id=caller.workload_client_id,
                mount_context_token=caller.mount_context_token,
                client_turn_id=caller.client_turn_id,
                interaction_ref=caller.interaction_ref,
                settings=caller.settings,
                capture_settings=caller.capture_settings,
                workspace_id=uuid4(),
                appointment_ref=str(previous.id),
                request_nonce=uuid4(),
            )
            yield SimpleNamespace(
                engine=capture_engine,
                session=session,
                now=now,
                target=target,
                case=SimpleNamespace(
                    doctor=doctor,
                    patient=patient,
                    appointment=previous,
                    actor=SimpleNamespace(id=identity.actor_id),
                ),
                caller=caller,
                binding=binding,
                client=FakeScribe(),
                attestation=admission.ConsentAttestation(
                    modality="in_person",
                    participant_regions=frozenset({"US-NY"}),
                    method="verbal_attestation",
                    all_parties_consented=True,
                    granted_at=now,
                ),
            )
        finally:
            _session_context.reset(context)


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_kind", ["raw", "reviewed_english"])
async def test_capture_note_review_and_correction_feed_later_session_prep(
    real_record_capture, monkeypatch, capture_kind
):
    f = real_record_capture
    kwargs = (
        await note_setup(f)
        if capture_kind == "reviewed_english"
        else await raw_note_setup(f, monkeypatch)
    )
    captured = await notes.capture_note(**kwargs)
    assert captured == await notes.capture_note(**kwargs)
    assert captured.review_required
    note = AIVisitNote.one(id=captured.note_ref)
    transcript = AIVisitTranscript.one(id=captured.transcript_ref)
    assert note.ai_transcript_id == transcript.id
    assert (note.doctor_id, note.appointment_id) == (
        f.case.doctor.id,
        f.case.appointment.id,
    )
    assert (transcript.doctor_id, transcript.patient_id, transcript.appointment_id) == (
        f.case.doctor.id,
        f.case.patient.id,
        f.case.appointment.id,
    )

    def selected():
        f.session.expire_all()
        return load_reviewed_note_sources(
            session=f.session,
            doctor_id=f.case.doctor.id,
            target_appointment_id=f.target.id,
            observed_at=f.now,
        )

    def review(row):
        finalize_note_for_session_prep(
            session=f.session,
            doctor_id=f.case.doctor.id,
            note_id=row.id,
            expected_content_sha256=note_content_sha256(row.note),
            observed_at=f.now,
        )
        f.session.commit()

    assert not selected().sources
    review(note)
    assert [source.source_ref for source in selected().sources] == [str(note.id)]
    token = authority.exchange_context_token(
        clerk_issuer=ISSUER,
        clerk_subject=f.case.doctor.clerk_id,
        requested_surface="provider_assistant",
        record_ref=str(f.target.id),
        settings=AUTHORITY_SETTINGS,
    ).context_token
    record = ProviderRecordAuthority.one(appointment_id=f.target.id)
    action = prep._action(token)
    first_key = "capture-prep-" + uuid4().hex
    assert (
        prep._read(token, record, action, key=first_key).response.result.state
        == "pending"
    )

    def build(expected):
        def generate(selection):
            assert [source.source_ref for source in selection.sources] == [
                str(expected.id)
            ]
            assert selection.sources[0].content_sha256 == note_content_sha256(
                expected.note
            )
            return prep._brief(selection)

        assert (
            run_one_build(
                generate=generate, clock=lambda: f.now, settings=AUTHORITY_SETTINGS
            )
            == "ready"
        )

    build(note)
    ready_key = "capture-ready-" + uuid4().hex
    ready = prep._read(token, record, action, key=ready_key)
    result = ready.response.result
    assert result.state == "ready"
    assert result.prepared_brief is not None
    assert result.prepared_brief.recap.source_refs == [str(note.id)]
    assert any(
        item.included and item.source_ref == str(note.id) for item in result.provenance
    )
    assert prep._read(token, record, action, key=ready_key).response == ready.response

    # Saving in Scribe makes a new note version; it must invalidate the old prep
    # immediately and require a new explicit review before reuse.
    request = {
        key: value for key, value in kwargs.items() if key not in ("prepare", "model")
    }
    assert captured.note_ref is not None
    assert captured.note_sha256 is not None
    corrected = await edits.save_capture_note(
        **request,
        edit=edits.NoteEdit(
            client_key=uuid4(),
            expected_note_ref=captured.note_ref,
            expected_note_sha256=captured.note_sha256,
            expected_source_sha256=captured.source_sha256,
            fields={
                "CC": "Sleep",
                "Plan": "Discuss the corrected sleep goals at follow-up.",
            },
        ),
    )
    assert corrected.note_ref != captured.note_ref and corrected.review_required
    assert not selected().sources
    with pytest.raises(SessionPrepScopeDenied):
        prep._read(token, record, action, key=ready_key)
    revised = AIVisitNote.one(id=corrected.note_ref)
    review(revised)
    assert [source.source_ref for source in selected().sources] == [str(revised.id)]
    next_action = prep._action(token)
    pending_key = "capture-corrected-" + uuid4().hex
    assert (
        prep._read(token, record, next_action, key=pending_key).response.result.state
        == "pending"
    )
    build(revised)
    refreshed = prep._read(
        token, record, next_action, key="capture-refreshed-" + uuid4().hex
    ).response.result
    assert refreshed.state == "ready" and refreshed.brief_version == 2
    assert refreshed.prepared_brief is not None
    assert refreshed.prepared_brief.recap.source_refs == [str(revised.id)]
    assert refreshed.brief_supersedes_ref == result.brief_ref
