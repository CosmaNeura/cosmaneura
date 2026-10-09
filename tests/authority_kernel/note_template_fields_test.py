"""Template endpoint checks in the explicitly scoped synthetic database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.routes import note_template_fields as routes

from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_scope_test import (
    capture_engine as _capture_engine,
)

capture_engine = _capture_engine


@pytest.fixture
def endpoint(capture_engine, monkeypatch):
    with Session(capture_engine) as session:
        owner = Doctor(
            clerk_id=f"user_synthetic-template-owner-{uuid4()}", biography=None
        )
        session.add(owner)
        session.flush()
        schema = DoctorNoteSchema(
            doctor_id=owner.id,
            note_type="Synthetic template",
            fields={"Labs - 02/12/2026": "Reported labs", "Plan": "Discussed plan"},
        )
        session.add(schema)
        session.commit()
        doctor_id, schema_id = owner.id, schema.id
    monkeypatch.setattr(routes, "get_session", lambda: Session(capture_engine))
    app = FastAPI()

    @app.middleware("http")
    async def authenticated_owner(request, call_next):
        request.state.doctor = SimpleNamespace(id=doctor_id)
        return await call_next(request)

    app.include_router(routes.note_template_fields_api)
    with TestClient(app) as client:
        yield client, f"/note-schemas/{schema_id}/fields", capture_engine, schema_id


def test_read_edit_and_lost_response_keep_order_and_identity(endpoint):
    client, path, engine, schema_id = endpoint
    before = client.get(path).json()
    payload = {
        "expected_revision": before["revision"],
        "fields": [
            {"heading": "10", "guidance": "Reported labs"},
            {"heading": "2", "guidance": "Discussed plan"},
        ],
    }
    result = client.put(path, json=payload)
    assert result.status_code == 200
    assert result.json()["revision"] != before["revision"]
    assert result.json()["fields"] == payload["fields"]
    assert client.put(path, json=payload).json() == result.json()
    assert client.get(path).json() == result.json()
    with Session(engine) as session:
        schema = session.get(DoctorNoteSchema, schema_id)
        assert schema is not None
        assert list(schema.fields) == ["10", "2"]
        assert schema.note_type == "Synthetic template"


def test_competing_updates_cannot_overwrite_each_other(endpoint):
    client, path, _, _ = endpoint
    before = client.get(path).json()

    def save(heading):
        return client.put(
            path,
            json={
                "expected_revision": before["revision"],
                "fields": [
                    {"heading": heading, "guidance": "Reported labs"},
                    before["fields"][1],
                ],
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, ["Labs", "Test results"]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    winner = next(r.json() for r in responses if r.status_code == 200)
    assert client.get(path).json() == winner


@pytest.mark.parametrize("state", ["foreign", "deleted", "absent"])
def test_foreign_deleted_and_absent_templates_are_hidden(endpoint, state):
    client, path, engine, schema_id = endpoint
    baseline = client.get(path).json()
    with Session(engine) as session:
        schema = session.get(DoctorNoteSchema, schema_id)
        assert schema is not None
        if state == "foreign":
            other = Doctor(
                clerk_id=f"user_synthetic-template-other-{uuid4()}", biography=None
            )
            session.add(other)
            session.flush()
            schema.doctor_id = other.id
        else:
            schema.deleted_at = datetime.now(UTC)
        session.add(schema)
        session.commit()
    if state == "absent":
        path = "/note-schemas/doctor_note_schema_00000000000000000000000000/fields"
    assert client.get(path).status_code == 404
    assert (
        client.put(
            path,
            json={
                "expected_revision": baseline["revision"],
                "fields": baseline["fields"],
            },
        ).status_code
        == 404
    )


def test_edit_cannot_silently_remove_sections(endpoint):
    client, path, _, _ = endpoint
    before = client.get(path).json()
    assert (
        client.put(
            path,
            json={
                "expected_revision": before["revision"],
                "fields": before["fields"][:1],
            },
        ).status_code
        == 422
    )
    assert client.get(path).json() == before


@pytest.mark.parametrize(
    "fields",
    [
        [],
        [{"heading": " ", "guidance": ""}],
        [{"heading": " Plan", "guidance": ""}],
        [{"heading": "x" * 201, "guidance": ""}],
        [{"heading": "Plan", "guidance": "x" * 4001}],
        [{"heading": "Plan", "guidance": ""}, {"heading": "plan", "guidance": ""}],
        [{"heading": str(i), "guidance": ""} for i in range(101)],
        [{"heading": str(i), "guidance": "x" * 4000} for i in range(26)],
    ],
)
def test_invalid_template_is_rejected_before_storage(fields):
    with pytest.raises(ValidationError):
        routes.UpdateTemplateFields(expected_revision="a" * 64, fields=fields)
