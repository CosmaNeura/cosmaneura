"""Synthetic PostgreSQL verification of author privacy and immutable identity."""

import importlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from companion_provider_provisioning_test import session_for
from scribe_encounters_test import workflow as _workflow
from typeid import TypeID

from app.services import authority, scribe_encounters

from app.models.appointment import Appointment
from app.models.patient import Patient
from app.models.scribe_private_note import ScribePrivateNoteReceipt
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlmodel import SQLModel, create_engine, select

workflow = _workflow


@pytest.fixture(scope="module")
def engine():
    raw = os.environ.get("SCRIBE_REDESIGN_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Explicit owned synthetic PostgreSQL required")
    url = make_url(raw)
    marker = json.loads(
        Path(
            "/private/tmp/scribe-redesign-core-lab-20261015/ownership.json"
        ).read_text()
    )
    assert marker == dict(
        created_for="scribe-redesign-20261015",
        database="scribe_redesign_test",
        username="scribe_redesign_owner",
        host="127.0.0.1",
        port=56015,
        only_synthetic=True,
    )
    assert (url.database, url.username, url.host, url.port) == (
        marker["database"],
        marker["username"],
        marker["host"],
        marker["port"],
    )
    for source in (Path(__file__).parents[2] / "app/models").glob("*.py"):
        if source.stem != "__init__":
            importlib.import_module("app.models." + source.stem)
    result = create_engine(url)
    with result.connect() as connection:
        assert connection.execute(
            text("SELECT current_database(), current_user")
        ).one() == (marker["database"], marker["username"])
    SQLModel.metadata.create_all(result)
    yield result
    result.dispose()


def provisional(request, invoke):
    return invoke(
        request(
            patient={"kind": "new", "name": "Invented visit name", "provisional": True}
        )
    )["encounter"]


def test_private_note_retries_versions_ciphertext_and_clinical_exclusion(
    engine, workflow
):
    _, _, request, invoke = workflow
    visit = provisional(request, invoke)
    args = dict(appointment_ref=visit["appointment_ref"])
    assert invoke(request("private_note_read", **args))["private_note"] == {
        "text": "",
        "version": 0,
        "updated_at": None,
    }
    value = request(
        "private_note_save",
        **args,
        change_key=str(uuid4()),
        expected_version=0,
        text="Invented personal reminder, not clinical text",
    )
    saved = invoke(value)
    assert saved["private_note"]["version"] == 1
    retry = invoke(value.model_copy(update={"request_nonce": uuid4()}))
    assert retry["private_note"] == saved["private_note"]
    with pytest.raises(scribe_encounters.EncounterConflict):
        invoke(value.model_copy(update={"text": "Different invented reminder"}))
    with pytest.raises(scribe_encounters.EncounterConflict):
        invoke(
            request(
                "private_note_save",
                **args,
                change_key=str(uuid4()),
                expected_version=0,
                text="Stale invented text",
            )
        )
    empty = invoke(
        request(
            "private_note_save",
            **args,
            change_key=str(uuid4()),
            expected_version=1,
            text="",
        )
    )
    assert empty["private_note"]["version"] == 2 and empty["private_note"]["text"] == ""
    catalog = invoke(request("list"))
    assert "private_note" not in json.dumps(catalog)
    with session_for(engine) as session:
        receipts = session.exec(
            select(ScribePrivateNoteReceipt).where(
                ScribePrivateNoteReceipt.appointment_id
                == TypeID.from_string(visit["appointment_ref"])
            )
        ).all()
        assert len(receipts) == 2 and all(len(r.request_digest) == 64 for r in receipts)
        raw_text = session.execute(
            text("SELECT text FROM scribe_private_note WHERE appointment_id = :visit"),
            {"visit": TypeID.from_string(visit["appointment_ref"]).uuid},
        ).scalar_one()
        assert raw_text != "" and "Invented" not in raw_text


def test_name_only_identity_links_without_rewriting_original_patient(engine, workflow):
    _, _, request, invoke = workflow
    canonical = invoke(request())["encounter"]
    source = provisional(request, invoke)
    assert (
        source["identity"]["state"] == "provisional" and source["date_of_birth"] is None
    )
    assert source["patient_ref"] not in {
        p["patient_ref"] for p in invoke(request("patients"))["patients"]
    }
    candidates = invoke(
        request(
            "patient_match_candidates",
            appointment_ref=source["appointment_ref"],
            query="Invented Scribe",
            date_of_birth="1990-01-01",
        )
    )
    assert canonical["patient_ref"] in {
        p["patient_ref"] for p in candidates["patients"]
    }
    value = request(
        "patient_match_save",
        appointment_ref=source["appointment_ref"],
        change_key=str(uuid4()),
        expected_version=0,
        selection={"kind": "existing", "patient_ref": canonical["patient_ref"]},
    )
    matched = invoke(value)["encounter"]
    assert matched["patient_ref"] == source["patient_ref"]
    assert matched["identity"]["canonical_patient_ref"] == canonical["patient_ref"]
    assert (
        matched["identity"]["state"] == "matched"
        and matched["identity"]["version"] == 1
    )
    assert (
        invoke(value.model_copy(update={"request_nonce": uuid4()}))["encounter"]
        == matched
    )
    with pytest.raises(scribe_encounters.EncounterConflict):
        invoke(value.model_copy(update={"change_key": uuid4()}))
    selectable = {p["patient_ref"] for p in invoke(request("patients"))["patients"]}
    assert (
        source["patient_ref"] not in selectable
        and canonical["patient_ref"] in selectable
    )
    with pytest.raises(authority.AuthorityRequestDeniedError):
        invoke(
            request(patient={"kind": "existing", "patient_ref": source["patient_ref"]})
        )
    assert source["patient_ref"] not in {
        p["patient_ref"]
        for p in invoke(
            request(
                "patient_match_candidates", appointment_ref=canonical["appointment_ref"]
            )
        )["patients"]
    }
    with session_for(engine) as session:
        appointment = session.get(Appointment, source["appointment_ref"])
        assert str(appointment.patient_id) == source["patient_ref"]
        assert session.get(Patient, source["patient_ref"]).date_of_birth is None


def test_confirm_new_later_dob_is_explicit_and_not_overwritten(engine, workflow):
    _, _, request, invoke = workflow
    visit = provisional(request, invoke)
    args = dict(appointment_ref=visit["appointment_ref"])
    result = invoke(
        request(
            "patient_match_save",
            **args,
            change_key=str(uuid4()),
            expected_version=0,
            selection={"kind": "confirm_new", "date_of_birth": "1992-03-04"},
        )
    )
    assert (
        result["identity"]["state"] == "confirmed"
        and result["identity"]["date_of_birth"] == "1992-03-04"
    )
    assert visit["patient_ref"] in {
        p["patient_ref"] for p in invoke(request("patients"))["patients"]
    }
    with pytest.raises(scribe_encounters.EncounterConflict):
        invoke(
            request(
                "patient_match_save",
                **args,
                change_key=str(uuid4()),
                expected_version=1,
                selection={"kind": "confirm_new", "date_of_birth": "1993-03-04"},
            )
        )


@pytest.mark.parametrize("action", ["private_note_read", "patient_match_candidates"])
def test_other_provider_visit_and_changed_actor_are_denied(engine, workflow, action):
    _, _, request, invoke = workflow
    with pytest.raises(authority.AuthorityRequestDeniedError):
        invoke(request(action, appointment_ref=str(TypeID(prefix="appointment"))))
    visit = provisional(request, invoke)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        invoke(
            request(
                action,
                appointment_ref=visit["appointment_ref"],
                expected_actor_ref=str(TypeID(prefix="authority_actor")),
            )
        )


def test_canonical_patient_from_another_doctor_is_denied(engine, workflow):
    args, _, request, invoke = workflow
    visit = provisional(request, invoke)
    from companion_provider_provisioning_test import case

    other = case(engine)
    with session_for(engine) as session:
        patient = Patient(
            doctor_id=other["doctor_id"], name="Invented other practice patient"
        )
        session.add(patient)
        session.commit()
        patient_ref = str(patient.id)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        invoke(
            request(
                "patient_match_save",
                appointment_ref=visit["appointment_ref"],
                change_key=str(uuid4()),
                expected_version=0,
                selection={"kind": "existing", "patient_ref": patient_ref},
            )
        )


def test_dashboard_reads_same_author_note_and_excludes_view_as(
    engine, workflow, monkeypatch
):
    from types import SimpleNamespace

    from companion_provider_provisioning_test import SETTINGS
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.configuration.scribe_encounters import ScribeEncounterSettings
    from app.routes import scribe_visit_extras as browser

    from app.models.doctor import Doctor

    args, _, request, invoke = workflow
    visit = provisional(request, invoke)
    invoke(
        request(
            "private_note_save",
            appointment_ref=visit["appointment_ref"],
            change_key=str(uuid4()),
            expected_version=0,
            text="Invented author reminder",
        )
    )
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        session.expunge(doctor)
    current = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/internal/v1/notes/invented/private-note",
            "headers": [],
            "session": {},
        }
    )
    current.state.doctor = doctor
    current.state.auth_state = SimpleNamespace(
        payload={
            "iss": SETTINGS.clerk_issuer,
            "sub": args["verified_clerk_subject"],
            "sid": "session_invented_scribe",
        }
    )
    monkeypatch.setattr(browser, "get_session", lambda: session_for(engine))
    monkeypatch.setattr(browser, "AUTHORITY_KERNEL_SETTINGS", SETTINGS)
    monkeypatch.setattr(
        browser, "SCRIBE_ENCOUNTER_SETTINGS", ScribeEncounterSettings(enabled=True)
    )
    result = browser.browser_workflow(
        current, visit["appointment_ref"], "private_note_read"
    )
    assert result["private_note"]["text"] == "Invented author reminder"
    current.scope["session"]["view_as_session_id"] = "invented_view_as"
    with pytest.raises(HTTPException) as denied:
        browser.browser_workflow(current, visit["appointment_ref"], "private_note_read")
    assert denied.value.status_code == 403
    current.scope["session"].clear()
    current.state.auth_state.payload["sub"] = "user_invented_another_actor"
    with pytest.raises(HTTPException) as denied:
        browser.browser_workflow(current, visit["appointment_ref"], "private_note_read")
    assert denied.value.status_code == 401


def test_expand_rung_rehearsal_in_isolated_synthetic_schema(engine):
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from sqlalchemy import inspect

    migration = (
        Path(__file__).parents[2]
        / "migrations/versions/dd086d74a921_add_scribe_visit_identity_and_private_notes.py"
    )
    spec = importlib.util.spec_from_file_location("visit_extras_expand", migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = "scribe_expand_" + uuid4().hex
    expected = {
        "scribe_private_note",
        "scribe_private_note_receipt",
        "scribe_patient_identity",
    }
    with engine.begin() as connection:
        connection.execute(text('CREATE SCHEMA "' + schema + '"'))
        connection.execute(text('SET LOCAL search_path TO "' + schema + '", public'))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        observed = inspect(connection)
        assert set(observed.get_table_names(schema=schema)) == expected
        for name in expected:
            columns = {c["name"]: c for c in observed.get_columns(name, schema=schema)}
            model = SQLModel.metadata.tables[name]
            assert set(columns) == set(model.columns.keys())
            assert all(columns[c.name]["nullable"] == c.nullable for c in model.columns)
            assert {
                v["name"] for v in observed.get_unique_constraints(name, schema=schema)
            } == {
                c.name
                for c in model.constraints
                if c.__class__.__name__ == "UniqueConstraint"
            }
        with pytest.raises(RuntimeError, match="retain"):
            module.downgrade()
        assert set(observed.get_table_names(schema=schema)) == expected
        connection.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))


def test_confirmed_matching_connects_reviewed_history_without_private_content(
    engine, workflow, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    from app.configuration.scribe_encounters import ScribeEncounterSettings
    from app.services import session_prep_note_sources as sources

    from app.models.ai_visit_note import AIVisitNote
    from app.models.ai_visit_transcript import AIVisitTranscript
    from app.models.doctor_note_schema import DoctorNoteSchema

    args, _, request, invoke = workflow
    monkeypatch.setattr(
        sources, "SCRIBE_ENCOUNTER_SETTINGS", ScribeEncounterSettings(enabled=True)
    )
    canonical = invoke(request())["encounter"]
    source = invoke(
        request(
            patient={
                "kind": "new",
                "name": "Invented prior visit name",
                "provisional": True,
            },
            appointment_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
        )
    )["encounter"]
    target = invoke(
        request(patient={"kind": "existing", "patient_ref": canonical["patient_ref"]})
    )["encounter"]
    with session_for(engine) as session:
        schema = DoctorNoteSchema(
            doctor_id=args["doctor_id"],
            note_type="Invented history",
            fields={"Plan": "Invented field"},
        )
        transcript = AIVisitTranscript(
            doctor_id=args["doctor_id"],
            appointment_id=TypeID.from_string(source["appointment_ref"]),
            patient_id=TypeID.from_string(source["patient_ref"]),
            duration=1,
            transcript_data=[],
        )
        session.add_all([schema, transcript])
        session.flush()
        note = AIVisitNote(
            doctor_id=args["doctor_id"],
            appointment_id=transcript.appointment_id,
            ai_transcript_id=transcript.id,
            doctor_note_schema_id=schema.id,
            note={"Plan": "Invented reviewed clinical source"},
        )
        session.add(note)
        session.flush()
        evidence = sources.finalize_note_for_session_prep(
            session=session,
            doctor_id=args["doctor_id"],
            note_id=note.id,
            expected_content_sha256=sources.note_content_sha256(note.note),
            observed_at=datetime.now(UTC),
        )
        source_note_ref = str(note.id)
        evidence_patient_ref = str(evidence.patient_id)
        session.commit()
    invoke(
        request(
            "private_note_save",
            appointment_ref=source["appointment_ref"],
            change_key=str(uuid4()),
            expected_version=0,
            text="Invented private content excluded",
        )
    )

    def selected(visit):
        with session_for(engine) as session:
            return sources.load_reviewed_note_sources(
                session=session,
                doctor_id=args["doctor_id"],
                target_appointment_id=TypeID.from_string(visit["appointment_ref"]),
                observed_at=datetime.now(UTC),
            )

    assert selected(target).sources == ()
    invoke(
        request(
            "patient_match_save",
            appointment_ref=source["appointment_ref"],
            change_key=str(uuid4()),
            expected_version=0,
            selection={"kind": "existing", "patient_ref": canonical["patient_ref"]},
        )
    )
    result = selected(target)
    assert len(result.sources) == 1 and result.sources[0].source_ref == source_note_ref
    assert result.sources[0].content == {"Plan": "Invented reviewed clinical source"}
    assert evidence_patient_ref == source["patient_ref"]
    assert "private" not in json.dumps(result.sources[0].content)
    # Existing Core appointments keep their original source patient association.
    with session_for(engine) as session:
        original_appointment = Appointment(
            doctor_id=args["doctor_id"],
            patient_id=TypeID.from_string(source["patient_ref"]),
            appointment_date=datetime.now(UTC).replace(tzinfo=None),
            appointment_name="Invented retained original visit",
        )
        session.add(original_appointment)
        session.flush()
        original_target = {"appointment_ref": str(original_appointment.id)}
        session.commit()
    assert selected(original_target).sources == ()
