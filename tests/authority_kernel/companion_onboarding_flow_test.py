"""Real local SQL from reviewed provider mapping to current capture binding."""

import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from companion_provider_provisioning_test import (
    SETTINGS,
    apply,
    case,
    count,
    engine,  # noqa: F401 -- shared owned PG fixture
    plan,
    session_for,
)

from app.configuration import companion_appointment_setup as setup_config
from app.configuration.companion_encounters import CompanionEncounterSettings
from app.configuration.scribe_capture import ScribeCaptureSettings
from app.services import (
    authority,
    companion_appointment_bindings,
    companion_appointment_setup,
    companion_encounters,
    provider_record_authority,
    scribe_capture_scope,
    view_as,
)
from app.services.companion_appointment_setup_contract import AppointmentSetupRequest
from app.services.companion_encounter_contract import (
    ConnectionRequest,
    EncountersRequest,
)
from app.services.companion_records import RecordBudget

from app.models.appointment import Appointment
from app.models.authority import AuthorityPersonBinding
from app.models.patient import Patient
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import event

REAL_VIEW_AS_GUARD = view_as.mount_context_session_blocked_by_view_as
REAL_RECORD_GUARD = provider_record_authority.validate_provider_record_scope


def test_reviewed_connection_to_current_capture_and_revocation(engine, monkeypatch):  # noqa: F811
    # Route services own their session; direct only that factory to the dedicated
    # local PostgreSQL engine, retaining their real transactions and commit path.
    monkeypatch.setattr(companion_encounters, "global_session", lambda: session_for(engine))
    monkeypatch.setattr(companion_appointment_setup, "global_session", lambda: session_for(engine))
    monkeypatch.setattr(authority, "validate_provider_record_scope", REAL_RECORD_GUARD)
    monkeypatch.setattr(
        view_as, "mount_context_session_blocked_by_view_as", REAL_VIEW_AS_GUARD
    )
    monkeypatch.setattr(
        companion_appointment_bindings, "AUTHORITY_KERNEL_SETTINGS", SETTINGS
    )
    monkeypatch.setattr(setup_config, "AUTHORITY_KERNEL_SETTINGS", SETTINGS)
    args = case(engine)
    reviewed = plan(engine, args)
    provisioned = apply(engine, args, reviewed.plan_sha256)
    with session_for(engine) as session:
        patient = Patient(
            doctor_id=args["doctor_id"],
            name="Invented Companion Patient",
            date_of_birth=datetime(1990, 1, 1),
        )
        session.add(patient)
        session.flush()
        appointment = Appointment(
            doctor_id=args["doctor_id"],
            patient_id=patient.id,
            external_id=None,
            appointment_date=datetime(2026, 9, 25, 9),
        )
        session.add(appointment)
        session.commit()
        appointment_ref = str(appointment.id)
    arguments = dict(
        issuer=SETTINGS.clerk_issuer,
        subject=args["verified_clerk_subject"],
        session_id="sess_invented_companion",
        authority_settings=SETTINGS,
    )
    locks = []

    def observed(_connection, _cursor, statement, _parameters, _context, _many):
        if "FOR UPDATE" in statement:
            locks.append(statement)

    event.listen(engine, "before_cursor_execute", observed)
    try:
        with session_for(engine):
            connection = companion_encounters.read_discovery(
                **arguments,
                request=ConnectionRequest(
                    expected_tenant_ref=str(args["tenant_id"]), request_nonce=uuid4()
                ),
                settings=CompanionEncounterSettings(enabled=True),
                deadline=RecordBudget(time.monotonic() + 15),
            ).connection
    finally:
        event.remove(engine, "before_cursor_execute", observed)
    practice_lock = next(
        i for i, query in enumerate(locks) if "FROM practice " in query
    )
    doctor_lock = next(i for i, query in enumerate(locks) if "FROM doctor " in query)
    assert practice_lock < doctor_lock
    common = dict(
        expected_tenant_ref=connection.tenant_ref,
        expected_actor_ref=connection.actor_ref,
        connection_sha256=connection.connection_sha256,
        window={"start_date": "2026-09-25", "end_date": "2026-09-26"},
    )
    setup_arguments = {
        **arguments,
        "settings_provider": lambda: setup_config.CompanionAppointmentSetupSettings(
            enabled=True
        ),
    }
    before = count(engine, AuthorityPersonBinding)
    with session_for(engine):
        preview = companion_appointment_setup.setup_appointments(
            **setup_arguments,
            request=AppointmentSetupRequest(
                **common, action="preview", request_nonce=uuid4()
            ),
            deadline=RecordBudget(time.monotonic() + 15),
        )
    assert preview.eligible_count == 1 and preview.connected_now_count == 0
    assert count(engine, AuthorityPersonBinding) == before
    with (
        session_for(engine),
        pytest.raises(companion_appointment_setup.AppointmentSetupChanged),
    ):
        companion_appointment_setup.setup_appointments(
            **setup_arguments,
            request=AppointmentSetupRequest(
                **common, action="connect", request_nonce=uuid4(), plan_sha256="0" * 64
            ),
            deadline=RecordBudget(time.monotonic() + 15),
        )
    assert count(engine, AuthorityPersonBinding) == before
    with session_for(engine):
        connected = companion_appointment_setup.setup_appointments(
            **setup_arguments,
            request=AppointmentSetupRequest(
                **common,
                action="connect",
                request_nonce=uuid4(),
                plan_sha256=preview.plan_sha256,
            ),
            deadline=RecordBudget(time.monotonic() + 15),
        )
    assert connected.connected_now_count == 1
    assert count(engine, AuthorityPersonBinding) == before + 1
    with session_for(engine):
        visits = companion_encounters.read_discovery(
            **arguments,
            request=EncountersRequest(**common, request_nonce=uuid4()),
            settings=CompanionEncounterSettings(enabled=True),
            deadline=RecordBudget(time.monotonic() + 15),
        )
    assert [visit.appointment.appointment_ref for visit in visits.encounters] == [
        appointment_ref
    ]
    with session_for(engine) as session:
        token = authority.exchange_context_token(
            clerk_issuer=SETTINGS.clerk_issuer,
            clerk_subject=args["verified_clerk_subject"],
            requested_surface="provider_assistant",
            record_ref=appointment_ref,
            session=session,
            settings=SETTINGS,
        )
        workload, turn = str(uuid4()), uuid4()
        resolved = authority.resolve_mount_context(
            workload_client_id=workload,
            mount_context_token=token.context_token,
            client_turn_id=turn,
            session=session,
            settings=SETTINGS,
        )
        session.commit()
        capture_args = dict(
            workload_client_id=workload,
            mount_context_token=token.context_token,
            interaction_ref=resolved.interaction_ref,
            client_turn_id=turn,
            workspace_id=uuid4(),
            appointment_ref=appointment_ref,
            request_nonce=uuid4(),
            settings=SETTINGS,
            capture_settings=ScribeCaptureSettings(enabled=True),
        )
        capture = scribe_capture_scope.resolve_capture_binding(**capture_args)
    assert (
        capture.appointment_ref == appointment_ref
        and capture.actor_ref == connection.actor_ref
    )
    with session_for(engine) as session:
        link = session.get(ScribeCaptureProviderLink, provisioned.provider_link_ref)
        link.revoked_at = datetime.now(UTC)
        session.add(link)
        session.commit()
    with session_for(engine), pytest.raises(authority.AuthorityRequestDeniedError):
        companion_encounters.read_discovery(
            **arguments,
            request=ConnectionRequest(
                expected_tenant_ref=connection.tenant_ref, request_nonce=uuid4()
            ),
            settings=CompanionEncounterSettings(enabled=True),
            deadline=RecordBudget(time.monotonic() + 15),
        )
    with session_for(engine), pytest.raises(scribe_capture_scope.CaptureScopeDenied):
        scribe_capture_scope.resolve_capture_binding(**capture_args)
