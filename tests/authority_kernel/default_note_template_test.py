"""Real locking tests in the root-owned, synthetic-only default SOAP database."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.routes import internal
from app.routes import note_template_fields as routes
from app.routes.dependencies import view_as
from app.services.default_note_template import provision_default_note_template
from app.services.note_template_preferences import preference_snapshot

from activemodel.session_manager import _serialize_pydantic_model
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_template_preference import NoteTemplatePreference
from app.models.patient import Patient
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, select


@pytest.fixture(scope="module")
def engine():
    raw = os.environ.get("DEFAULT_SOAP_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Explicit root-owned synthetic default SOAP database required")
    url = make_url(raw)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host is not None
        or url.port != 55485
        or url.database != "cn_default_soap_demo_20261106"
        or url.username != "jw"
        or url.password is not None
        or dict(url.query) != {"host": "/private/tmp/cn-demo-followup-pg-20261106"}
    ):
        pytest.fail("Refusing a database outside the assigned synthetic SOAP scope")
    from sqlmodel import create_engine

    value = create_engine(url, json_serializer=_serialize_pydantic_model)
    SQLModel.metadata.create_all(value)
    yield value
    value.dispose()


@pytest.fixture
def owner(engine):
    with Session(engine, expire_on_commit=False) as session:
        doctor = Doctor(clerk_id="user_synthetic_soap_" + uuid4().hex, biography=None)
        session.add(doctor)
        session.commit()
        return doctor


@pytest.fixture
def client(engine, owner, monkeypatch):
    monkeypatch.setattr(routes, "get_session", lambda: Session(engine))
    app = FastAPI()

    @app.middleware("http")
    async def authenticated_owner(request: Request, call_next):
        request.state.doctor = owner
        return await call_next(request)

    app.include_router(routes.note_template_fields_api)
    with TestClient(app) as value:
        yield value


def schemas(session, doctor):
    return session.exec(
        select(DoctorNoteSchema).where(DoctorNoteSchema.doctor_id == doctor.id)
    ).all()


def test_initialization_races_produce_one_owned_soap_and_default(client, engine, owner):
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda _: client.post("/note-schemas/initialize", json={}), range(4)
            )
        )
    assert all(
        r.status_code == 200 and r.json()["status"] == "available" for r in responses
    )
    assert sum(r.json()["created"] for r in responses) == 1
    with Session(engine) as session:
        rows = schemas(session, owner)
        assert len(rows) == 1 and rows[0].note_type == "SOAP"
        starting_point = json.loads(
            (
                Path(__file__).parents[1] / "fixtures/default_soap_starting_point.json"
            ).read_text()
        )
        assert rows[0].fields == {
            field["heading"]: field["guidance"] for field in starting_point
        }
        preference = preference_snapshot(session, owner.id)
        assert preference.default_schema_ref == str(rows[0].id)
    assert client.post("/note-schemas/initialize", json={}).json()["created"] is False


@pytest.mark.parametrize(
    "name,fields",
    [
        ("SOAP", {"Custom": "Synthetic custom guidance"}),
        ("My custom template", {"Custom": "Synthetic custom guidance"}),
        ("SOAP", {"Subjective": "", "Objective": "", "Assessment": "", "Plan": ""}),
    ],
)
def test_existing_fields_and_chosen_default_are_unchanged(
    client, engine, owner, name, fields
):
    with Session(engine) as session:
        schema = DoctorNoteSchema(
            doctor_id=owner.id,
            note_type=name,
            fields=fields,
        )
        session.add(schema)
        session.flush()
        reference = schema.id
        session.add(
            NoteTemplatePreference(
                doctor_id=owner.id, default_schema_id=reference, revision=7
            )
        )
        session.commit()
    assert client.post("/note-schemas/initialize", json={}).status_code == 200
    with Session(engine) as session:
        original = session.get(DoctorNoteSchema, reference)
        assert original is not None and original.fields == fields
        assert preference_snapshot(session, owner.id).model_dump() == {
            "default_schema_ref": str(reference),
            "revision": 7,
        }


def test_explicit_choose_each_time_is_preserved(client, engine, owner):
    with Session(engine) as session:
        session.add(
            NoteTemplatePreference(
                doctor_id=owner.id, default_schema_id=None, revision=3
            )
        )
        session.commit()
    assert client.post("/note-schemas/initialize", json={}).json()["created"] is True
    assert client.get("/note-template-preference").json() == {
        "default_schema_ref": None,
        "revision": 3,
    }


def test_prior_deleted_soap_is_never_replaced(client, engine, owner):
    with Session(engine) as session:
        row = DoctorNoteSchema(
            doctor_id=owner.id,
            note_type="SOAP",
            fields={"Custom": "Retained"},
        )
        row.deleted_at = datetime.now(UTC)
        session.add(row)
        session.commit()
    for _ in range(2):
        assert client.post("/note-schemas/initialize", json={}).json() == {
            "status": "deleted",
            "created": False,
        }
    with Session(engine) as session:
        assert len(schemas(session, owner)) == 1


def test_deletion_racing_initialization_retains_one_tombstone(client, engine, owner):
    assert client.post("/note-schemas/initialize", json={}).status_code == 200
    with Session(engine) as session:
        reference = schemas(session, owner)[0].id

    # Exercise both operations concurrently in real PostgreSQL. Either legal
    # order may report available before deletion or deleted after it, but the
    # final owner catalogue must retain the same deletion receipt.
    with ThreadPoolExecutor(max_workers=5) as pool:
        deletion = pool.submit(client.delete, f"/note-schemas/{reference}")
        initializations = [
            pool.submit(client.post, "/note-schemas/initialize", json={})
            for _ in range(4)
        ]
        assert deletion.result().status_code == 204
        for future in initializations:
            response = future.result()
            assert response.status_code == 200
            assert response.json()["status"] in {"available", "deleted"}
            assert response.json()["created"] is False
    with Session(engine) as session:
        rows = schemas(session, owner)
        assert len(rows) == 1 and rows[0].id == reference
        assert rows[0].deleted_at is not None
        assert preference_snapshot(session, owner.id).default_schema_ref is None
    assert client.post("/note-schemas/initialize", json={}).json() == {
        "status": "deleted",
        "created": False,
    }


def test_deletion_retains_note_reference_and_never_resurrects(client, engine, owner):
    assert client.post("/note-schemas/initialize", json={}).status_code == 200
    with Session(engine) as session:
        row = schemas(session, owner)[0]
        reference = row.id
        example = DoctorNoteExample(
            doctor_id=owner.id,
            doctor_note_schema_id=reference,
            field_examples={"Plan": "Synthetic retained example"},
        )
        session.add(example)
        session.commit()
        example_ref = example.id
        patient = Patient(doctor_id=owner.id, name="Test Patient Alpha")
        session.add(patient)
        session.flush()
        appointment = Appointment(
            doctor_id=owner.id,
            patient_id=patient.id,
            external_id=None,
            appointment_date=datetime.now(UTC),
        )
        session.add(appointment)
        session.flush()
        transcript = AIVisitTranscript(
            doctor_id=owner.id,
            patient_id=patient.id,
            appointment_id=appointment.id,
            duration=44,
            transcript_data=[],
        )
        session.add(transcript)
        session.flush()
        note = AIVisitNote(
            doctor_id=owner.id,
            appointment_id=appointment.id,
            ai_transcript_id=transcript.id,
            doctor_note_schema_id=reference,
            note={"Plan": "Synthetic saved draft"},
        )
        session.add(note)
        session.commit()
        note_ref = note.id
    for _ in range(2):
        assert client.delete(f"/note-schemas/{reference}").status_code == 204
    assert (
        client.post("/note-schemas/initialize", json={}).json()["status"] == "deleted"
    )
    assert client.get(f"/note-schemas/{reference}/fields").status_code == 404
    with Session(engine) as session:
        row = session.get(DoctorNoteSchema, reference)
        retained = session.get(DoctorNoteExample, example_ref)
        assert row is not None and row.deleted_at is not None
        assert retained is not None and retained.doctor_note_schema_id == reference
        assert retained.field_examples == {"Plan": "Synthetic retained example"}
        saved = session.get(AIVisitNote, note_ref)
        assert saved is not None and saved.doctor_note_schema_id == reference
        assert (
            saved.note == {"Plan": "Synthetic saved draft"}
            and saved.note_type == "SOAP"
        )
        assert preference_snapshot(session, owner.id).default_schema_ref is None


def test_foreign_ownership_and_body_owner_injection_are_refused(client, engine, owner):
    with Session(engine) as session:
        other = Doctor(clerk_id="user_synthetic_other_" + uuid4().hex, biography=None)
        session.add(other)
        session.flush()
        row = DoctorNoteSchema(
            doctor_id=other.id, note_type="SOAP", fields={"Plan": ""}
        )
        session.add(row)
        session.commit()
        reference = row.id
    assert client.delete(f"/note-schemas/{reference}").status_code == 404
    assert (
        client.post(
            "/note-schemas/initialize", json={"doctor_id": str(owner.id)}
        ).status_code
        == 422
    )
    with Session(engine) as session:
        assert not schemas(session, owner)


def test_template_capacity_preserves_existing_catalogue(client, engine, owner):
    with Session(engine) as session:
        session.add_all(
            [
                DoctorNoteSchema(
                    doctor_id=owner.id, note_type=f"Synthetic {i}", fields={"Plan": ""}
                )
                for i in range(100)
            ]
        )
        session.commit()
    assert client.post("/note-schemas/initialize", json={}).json() == {
        "status": "capacity",
        "created": False,
    }
    with Session(engine) as session:
        assert len(schemas(session, owner)) == 100


def test_catalogue_preference_reads_do_not_provision(client, engine, owner):
    assert client.get("/note-template-preference").status_code == 200
    with Session(engine) as session:
        assert not schemas(session, owner)


def test_deleted_owner_cannot_initialize(client, engine, owner):
    with Session(engine) as session:
        row = session.get(Doctor, owner.id)
        assert row is not None
        row.deleted_at = datetime.now(UTC)
        session.add(row)
        session.commit()
    with (
        Session(engine) as session,
        pytest.raises(ValueError, match="default_template_owner_unavailable"),
    ):
        provision_default_note_template(session, owner.id)


def test_failed_transaction_rolls_back_template_and_default(engine, owner):
    with Session(engine) as session:
        provision_default_note_template(session, owner.id)
        session.rollback()
    with Session(engine) as session:
        assert not schemas(session, owner)
        assert preference_snapshot(session, owner.id).default_schema_ref is None


def request_for(owner, *, method="POST", session=None):
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": "/internal/v1/note-schemas/initialize",
            "headers": [],
            "session": session or {},
        }
    )
    request.state.doctor = owner
    return request


@pytest.mark.parametrize(
    "denial", [None, "practice_membership_required", "baa_required"]
)
def test_onboarding_records_template_only_after_access_gate(
    engine, owner, monkeypatch, denial
):
    monkeypatch.setattr(internal, "get_session", lambda: Session(engine))
    monkeypatch.setattr(
        internal,
        "resolve_doctor_access",
        lambda *_args, **_kwargs: SimpleNamespace(
            allowed=denial is None, denial_reason=denial
        ),
    )
    calls = []

    def activated(**kwargs):
        with Session(engine) as session:
            current = session.get(Doctor, owner.id)
            assert current is not None and current.onboarding_completed_at is not None
            assert len(schemas(session, owner)) == 1
        calls.append(kwargs)

    monkeypatch.setattr(internal, "activate_scribe_after_commit", activated)
    if denial:
        with pytest.raises(HTTPException):
            internal.complete_onboarding(request_for(owner))
    else:
        internal.complete_onboarding(request_for(owner))
    with Session(engine) as session:
        assert len(schemas(session, owner)) == (0 if denial else 1)
    assert len(calls) == (0 if denial else 1)


def test_onboarding_failure_rolls_back_completion_and_template(
    engine, owner, monkeypatch
):
    owner_ref = owner.id
    monkeypatch.setattr(internal, "get_session", lambda: Session(engine))
    monkeypatch.setattr(
        internal,
        "resolve_doctor_access",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
    )

    def failed(session, doctor_id):
        provision_default_note_template(session, doctor_id)
        raise RuntimeError("synthetic transaction failure")

    monkeypatch.setattr(internal, "provision_default_note_template", failed)
    calls = []
    monkeypatch.setattr(
        internal, "activate_scribe_after_commit", lambda **kwargs: calls.append(kwargs)
    )
    with pytest.raises(RuntimeError, match="synthetic transaction failure"):
        internal.complete_onboarding(request_for(owner))
    with Session(engine) as session:
        current = session.get(Doctor, owner_ref)
        assert current is not None and current.onboarding_completed_at is None
        assert not schemas(session, current)
    assert not calls


@pytest.mark.parametrize("revoked", [False, True])
def test_native_activation_keeps_revoked_access_closed_and_provisions_eligible_owner(
    engine, revoked
):
    from app.services.scribe_activation import ActivationDenied

    from tests.authority_kernel.companion_provider_provisioning_test import case
    from tests.authority_kernel.scribe_activation_test import activate, grant

    args = case(engine)
    if revoked:
        with Session(engine) as session:
            doctor = session.get(Doctor, args["doctor_id"])
            assert doctor is not None
            grant(session, doctor, args["practice_id"], revoked=True)
            session.commit()
        with pytest.raises(ActivationDenied):
            activate(engine, args)
    else:
        activate(engine, args)
    with Session(engine) as session:
        rows = session.exec(
            select(DoctorNoteSchema).where(
                DoctorNoteSchema.doctor_id == args["doctor_id"]
            )
        ).all()
        assert len(rows) == (0 if revoked else 1)


def test_capture_admission_requires_consent_before_template_setup(engine):
    from app.services import scribe_capture_admission as admission
    from app.services.scribe_capture_scope import resolve_capture_binding

    from activemodel.session_manager import _session_context

    from tests.authority_kernel.scribe_capture_scope_test import make_scope_case

    with Session(engine, expire_on_commit=False) as session:
        token = _session_context.set(session)
        try:
            case = make_scope_case(session)
            binding = resolve_capture_binding(**case.arguments)
            caller = admission.CaptureCaller(
                **{
                    key: value
                    for key, value in case.arguments.items()
                    if key in admission.CaptureCaller.__dataclass_fields__
                }
            )
            request_key = uuid4().hex

            def prepare(attestation: admission.ConsentAttestation | None):
                return admission._prepare(
                    caller=caller,
                    binding_ref=binding.binding_ref,
                    workspace_id=binding.workspace_id,
                    client_request_key=request_key,
                    attestation=attestation,
                )

            with pytest.raises(admission.AdmissionConsentRequired):
                prepare(None)
            assert not schemas(session, case.doctor)
            consent = admission.ConsentAttestation(
                modality="in_person",
                participant_regions=frozenset(),
                method="verbal_attestation",
                all_parties_consented=True,
                granted_at=datetime.now(UTC),
            )
            prepare(consent)
            assert len(schemas(session, case.doctor)) == 1
        finally:
            _session_context.reset(token)


@pytest.mark.parametrize(
    "method,route_name",
    [("POST", "initialize_note_templates"), ("DELETE", "delete_note_template")],
)
def test_new_mutations_inherit_actual_readonly_view_as_guard(
    engine, owner, monkeypatch, method, route_name
):
    request = request_for(
        owner,
        method=method,
        session={
            view_as.SESSION_KEY_VIEW_AS_ID: "synthetic",
            view_as.SESSION_KEY_VIEW_AS_NONCE: "synthetic",
        },
    )
    request.scope["route"] = SimpleNamespace(name=route_name)
    request.scope["headers"] = [
        (
            view_as.VIEW_AS_REQUEST_BINDING_HEADER.lower().encode(),
            b"synthetic-view-binding",
        )
    ]
    request.state.auth_state = SimpleNamespace(
        payload={
            "iss": "https://synthetic.clerk.accounts.dev",
            "sub": owner.clerk_id,
            "sid": "synthetic-session",
        }
    )
    monkeypatch.setattr(view_as, "get_session", lambda: Session(engine))
    monkeypatch.setattr(
        view_as, "configured_superadmin_capability_environment", lambda: "synthetic"
    )
    monkeypatch.setattr(
        view_as, "view_as_request_binding", lambda _record: "synthetic-view-binding"
    )

    def resolve(_session, **kwargs):
        assert kwargs["unsafe_request"] is True
        return SimpleNamespace(
            clear_browser_session=False,
            rejected_unsafe_fallback=False,
            active=SimpleNamespace(session=object()),
        )

    monkeypatch.setattr(view_as, "resolve_view_as", resolve)
    with pytest.raises(HTTPException) as error:
        view_as.apply_view_as(request)
    assert error.value.status_code == 403
    with Session(engine) as session:
        assert not schemas(session, owner)
