"""Persisted preference tests use only the existing owned synthetic schema."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_template_preference import NoteTemplatePreference
from sqlmodel import Session, select

from tests.authority_kernel.note_template_fields_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.note_template_fields_test import (
    endpoint as _endpoint,
)

capture_engine = _capture_engine
endpoint = _endpoint
PATH = "/note-template-preference"


def test_missing_preference_read_does_not_write_and_set_clear_survive_fresh_requests(
    endpoint,
):
    client, _, engine, schema_id = endpoint
    initial = client.get(PATH).json()
    assert initial == {"default_schema_ref": None, "revision": 0}
    with Session(engine) as session:
        schema = session.get(DoctorNoteSchema, schema_id)
        assert schema is not None
        owner = schema.doctor_id
        assert (
            session.exec(
                select(NoteTemplatePreference).where(
                    NoteTemplatePreference.doctor_id == owner
                )
            ).all()
            == []
        )
    payload = {"default_schema_ref": str(schema_id), "expected_revision": 0}
    saved = client.put(PATH, json=payload)
    assert saved.status_code == 200
    assert saved.json() == {"default_schema_ref": str(schema_id), "revision": 1}
    assert client.get(PATH).json() == saved.json()
    assert client.put(PATH, json=payload).json() == saved.json()
    assert (
        client.put(
            PATH, json={"default_schema_ref": None, "expected_revision": 0}
        ).status_code
        == 409
    )
    cleared = client.put(
        PATH, json={"default_schema_ref": None, "expected_revision": 1}
    )
    assert cleared.json() == {"default_schema_ref": None, "revision": 2}
    assert client.get(PATH).json() == cleared.json()


@pytest.mark.parametrize("state", ["foreign", "deleted", "absent"])
def test_unavailable_templates_cannot_become_defaults(endpoint, state):
    client, _, engine, schema_id = endpoint
    target = str(schema_id)
    with Session(engine) as session:
        schema = session.get(DoctorNoteSchema, schema_id)
        assert schema is not None
        if state == "foreign":
            other = Doctor(
                clerk_id=f"user_synthetic-default-other-{uuid4()}", biography=None
            )
            session.add(other)
            session.flush()
            schema.doctor_id = other.id
        else:
            schema.deleted_at = datetime.now(UTC)
        session.add(schema)
        session.commit()
    if state == "absent":
        target = "doctor_note_schema_00000000000000000000000000"
    assert (
        client.put(
            PATH, json={"default_schema_ref": target, "expected_revision": 0}
        ).status_code
        == 404
    )
    assert client.get(PATH).json() == {"default_schema_ref": None, "revision": 0}


@pytest.mark.parametrize("state", ["deleted", "foreign"])
def test_existing_default_is_not_exposed_after_template_becomes_unavailable(
    endpoint, state
):
    client, _, engine, schema_id = endpoint
    assert (
        client.put(
            PATH, json={"default_schema_ref": str(schema_id), "expected_revision": 0}
        ).status_code
        == 200
    )
    with Session(engine) as session:
        schema = session.get(DoctorNoteSchema, schema_id)
        assert schema is not None
        if state == "foreign":
            other = Doctor(
                clerk_id=f"user_synthetic-default-moved-{uuid4()}", biography=None
            )
            session.add(other)
            session.flush()
            schema.doctor_id = other.id
        else:
            schema.deleted_at = datetime.now(UTC)
        session.add(schema)
        session.commit()
    assert client.get(PATH).json() == {"default_schema_ref": None, "revision": 1}
    assert client.put(
        PATH, json={"default_schema_ref": None, "expected_revision": 1}
    ).json() == {"default_schema_ref": None, "revision": 2}


def test_two_default_changes_from_same_revision_have_one_winner(endpoint):
    client, _, _, schema_id = endpoint
    other = client.post(
        "/note-schemas",
        json={
            "note_type": "Second synthetic default",
            "fields": [{"heading": "Plan", "guidance": "Only discussed plan"}],
        },
    ).json()["schema_id"]

    def save(ref):
        return client.put(
            PATH, json={"default_schema_ref": str(ref), "expected_revision": 0}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, [schema_id, other]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(
        response.json() for response in responses if response.status_code == 200
    )
    assert client.get(PATH).json() == winner
    assert save(winner["default_schema_ref"]).json() == winner


def test_preference_request_cannot_select_an_owner(endpoint):
    client, _, _, schema_id = endpoint
    assert (
        client.put(
            PATH,
            json={
                "default_schema_ref": str(schema_id),
                "expected_revision": 0,
                "doctor_id": "other",
            },
        ).status_code
        == 422
    )
    assert client.get(PATH).json() == {"default_schema_ref": None, "revision": 0}
