"""Real request-wide ActiveModel sessions on an explicitly owned synthetic PG DB.

Run with --confcutdir=tests/authority_kernel and PRIVATE_NOTE_SCOPE_URL pointing
at a new Unix-only lab whose parent ownership.json binds this database/user.
No Session helper or request transaction is replaced by the fixture.
"""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from companion_provider_provisioning_test import SETTINGS, apply, case, plan
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from starlette_context.middleware import RawContextMiddleware
from typeid import TypeID

from app.configuration import session_prep
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.routes import scribe_visit_extras as browser
from app.routes.dependencies.doctor import inject_doctor_record
from app.routes.dependencies.practice_gate import require_practice_in_force
from app.routes.dependencies.view_as import apply_view_as
from app.services import (
    authority,
    provider_record_authority,
    scribe_encounters,
    view_as,
)
from app.services.companion_records import RecordBudget
from app.services.scribe_encounter_contract import REQUEST_ADAPTER

from activemodel.session_manager import (
    SessionManager,
    aglobal_session,
    get_engine,
    get_session,
    global_session,
)
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from app.models.scribe_private_note import ScribePrivateNote, ScribePrivateNoteReceipt
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, SQLModel, select

# Capture defining-module implementations before the authority subtree's
# SQLite-only autouse fixture patches their aliases. This PostgreSQL fixture
# exercises the real record/practice and view-as boundaries instead.
_RECORD_SCOPE = provider_record_authority.validate_provider_record_scope
_VIEW_AS_SCOPE = view_as.mount_context_session_blocked_by_view_as


@pytest.fixture
def owned_engine(monkeypatch):
    raw = os.environ.get("PRIVATE_NOTE_SCOPE_URL")
    if not raw:
        pytest.skip("An explicitly owned synthetic Unix-only PostgreSQL is required")
    url = make_url(raw)
    socket = Path(url.query["host"])
    assert url.host is None and str(socket).startswith("/private/tmp/")
    assert url.database == "private_note_scope_synthetic"
    marker = json.loads((socket.parent / "ownership.json").read_text())
    assert marker["only_synthetic"] is True and marker["listen_addresses"] == ""
    assert (
        marker["database"],
        marker["username"],
        marker["unix_socket"],
        marker["port"],
    ) == (
        url.database,
        url.username,
        str(socket),
        url.query["port"],
    )
    manager = SessionManager(raw)
    monkeypatch.setattr(SessionManager, "_instance", manager)
    monkeypatch.setattr(authority, "validate_provider_record_scope", _RECORD_SCOPE)
    monkeypatch.setattr(
        view_as, "mount_context_session_blocked_by_view_as", _VIEW_AS_SCOPE
    )
    assert authority.validate_provider_record_scope is _RECORD_SCOPE
    assert view_as.mount_context_session_blocked_by_view_as is _VIEW_AS_SCOPE
    engine = manager.get_engine()
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _provision(engine, monkeypatch, subjects=()):
    args = case(engine)
    monkeypatch.setattr(
        session_prep,
        "SESSION_PREP_PROVIDER_SETTINGS",
        session_prep.SessionPrepProviderSettings(
            enabled=True,
            clerk_subjects=frozenset((*subjects, args["verified_clerk_subject"])),
        ),
    )
    provisioned = apply(engine, args, plan(engine, args).plan_sha256)
    with get_session() as session:
        link = session.get(ScribeCaptureProviderLink, provisioned.provider_link_ref)
        actor = str(link.actor_id)
    return args, provisioned, actor


def _request(args, actor, action="create", **fields):
    body = dict(
        action=action,
        expected_actor_ref=actor,
        expected_tenant_ref=str(args["tenant_id"]),
        request_nonce=str(uuid4()),
    )
    if action == "create":
        body.update(
            creation_key=str(uuid4()),
            appointment_at=datetime.now(UTC).isoformat(),
            patient={
                "kind": "new",
                "name": "Invented request-scope patient",
                "provisional": True,
            },
        )
    return REQUEST_ADAPTER.validate_python({**body, **fields})


def _invoke(args, request, **kwargs):
    return scribe_encounters.encounter_workflow(
        issuer=SETTINGS.clerk_issuer,
        subject=args["verified_clerk_subject"],
        session_id="session_scope_synthetic",
        request=request,
        settings_provider=lambda: ScribeEncounterSettings(enabled=True),
        authority_settings=SETTINGS,
        deadline=RecordBudget(time.monotonic() + 15),
        **kwargs,
    )


def _app(subject, *, missing_session=False, view_as_cookie=False):
    def verified_external_clerk(request: Request):
        payload = {
            "iss": SETTINGS.clerk_issuer,
            "sub": subject,
            "sid": "session_scope_synthetic",
        }
        if missing_session:
            payload.pop("sid")
        if view_as_cookie:
            request.session["view_as_session_id"] = "invented_support_view"
        request.state.auth_state = SimpleNamespace(is_signed_in=True, payload=payload)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="fixed-synthetic-session-key")
    app.add_middleware(RawContextMiddleware)
    parent = APIRouter(
        prefix="/internal/v1",
        dependencies=[
            Depends(aglobal_session),
            Depends(verified_external_clerk),
            Depends(inject_doctor_record),
            Depends(apply_view_as),
            Depends(require_practice_in_force),
        ],
    )
    parent.include_router(browser.router)
    app.include_router(parent)
    return app


@pytest.fixture
def owned(owned_engine, monkeypatch):
    args, provisioned, actor = _provision(owned_engine, monkeypatch)
    visit = _invoke(args, _request(args, actor))["encounter"]
    monkeypatch.setattr(browser, "AUTHORITY_KERNEL_SETTINGS", SETTINGS)
    monkeypatch.setattr(
        browser, "SCRIBE_ENCOUNTER_SETTINGS", ScribeEncounterSettings(enabled=True)
    )
    path = "/internal/v1/notes/" + visit["appointment_ref"] + "/private-note"
    return args, provisioned, actor, visit, path


HEADERS = {"X-Cosmaneura-View-As-Binding": "inactive"}


def test_after_request_session_read_save_commits_and_exact_retry_is_idempotent(owned):
    args, _, _, visit, path = owned
    body = {
        "change_key": str(uuid4()),
        "expected_version": 0,
        "text": "Invented private reminder",
    }
    with TestClient(_app(args["verified_clerk_subject"])) as client:
        empty = client.get(path, headers=HEADERS)
        assert empty.status_code == 200 and empty.json() == {
            "text": "",
            "version": 0,
            "updated_at": None,
        }
        saved = client.put(path, headers=HEADERS, json=body)
        assert saved.status_code == 200 and saved.json()["version"] == 1
        assert client.get(path, headers=HEADERS).json() == saved.json()
        assert client.put(path, headers=HEADERS, json=body).json() == saved.json()
        assert (
            client.put(
                path, headers=HEADERS, json={**body, "text": "Changed retry"}
            ).status_code
            == 409
        )
        assert (
            client.put(
                path, headers=HEADERS, json={**body, "change_key": str(uuid4())}
            ).status_code
            == 409
        )
    with get_session() as session:
        assert (
            len(
                session.exec(
                    select(ScribePrivateNoteReceipt).where(
                        ScribePrivateNoteReceipt.appointment_id
                        == TypeID.from_string(visit["appointment_ref"])
                    )
                ).all()
            )
            == 1
        )
        assert (
            session.exec(
                select(ScribePrivateNote).where(
                    ScribePrivateNote.appointment_id
                    == TypeID.from_string(visit["appointment_ref"])
                )
            )
            .one()
            .text
            == body["text"]
        )


def test_after_commit_failure_has_no_success_response_or_saved_private_state(
    owned, monkeypatch
):
    args, _, _, visit, path = owned
    original = Session.commit

    def mark_private_write(session, _context):
        if any(isinstance(row, ScribePrivateNoteReceipt) for row in session.new):
            session.info["synthetic_private_write"] = True

    def commit(session):
        if session.info.get("synthetic_private_write"):
            raise SQLAlchemyError("fixed_synthetic_commit_failure")
        return original(session)

    event.listen(Session, "after_flush", mark_private_write)
    monkeypatch.setattr(Session, "commit", commit)
    try:
        with TestClient(_app(args["verified_clerk_subject"])) as client:
            response = client.put(
                path,
                headers=HEADERS,
                json={
                    "change_key": str(uuid4()),
                    "expected_version": 0,
                    "text": "Invented unsaved reminder",
                },
            )
        assert response.status_code == 503
        with get_session() as session:
            assert (
                session.exec(
                    select(ScribePrivateNote).where(
                        ScribePrivateNote.appointment_id
                        == TypeID.from_string(visit["appointment_ref"])
                    )
                ).all()
                == []
            )
            assert (
                session.exec(
                    select(ScribePrivateNoteReceipt).where(
                        ScribePrivateNoteReceipt.appointment_id
                        == TypeID.from_string(visit["appointment_ref"])
                    )
                ).all()
                == []
            )
    finally:
        event.remove(Session, "after_flush", mark_private_write)


def test_after_foreign_owner_and_missing_session_are_denied(
    owned, owned_engine, monkeypatch
):
    args, _, _, visit, path = owned
    other, _, _ = _provision(
        owned_engine, monkeypatch, (args["verified_clerk_subject"],)
    )
    with TestClient(_app(other["verified_clerk_subject"])) as client:
        assert client.get(path, headers=HEADERS).status_code == 404
    with TestClient(
        _app(args["verified_clerk_subject"], missing_session=True)
    ) as client:
        assert client.get(path, headers=HEADERS).status_code == 401


def test_after_final_link_reauthorization_rolls_back_private_write(owned, monkeypatch):
    args, _, _, visit, path = owned
    original = browser.scribe_encounters.scribe_visit_extras.save_private_note

    def save(session, request, row, link, conflict):
        result = original(session, request, row, link, conflict)
        link.revoked_at = datetime.now(UTC)
        session.add(link)
        session.flush()
        return result

    monkeypatch.setattr(
        browser.scribe_encounters.scribe_visit_extras, "save_private_note", save
    )
    with TestClient(_app(args["verified_clerk_subject"])) as client:
        response = client.put(
            path,
            headers=HEADERS,
            json={
                "change_key": str(uuid4()),
                "expected_version": 0,
                "text": "Invented rollback reminder",
            },
        )
    assert response.status_code == 404
    with get_session() as session:
        assert (
            session.exec(
                select(ScribePrivateNote).where(
                    ScribePrivateNote.appointment_id
                    == TypeID.from_string(visit["appointment_ref"])
                )
            ).all()
            == []
        )
        assert (
            session.exec(
                select(ScribePrivateNoteReceipt).where(
                    ScribePrivateNoteReceipt.appointment_id
                    == TypeID.from_string(visit["appointment_ref"])
                )
            ).all()
            == []
        )


def test_after_supplied_session_must_be_the_actual_active_context(owned):
    args, _, actor, visit, _ = owned
    request = _request(
        args, actor, "private_note_read", appointment_ref=visit["appointment_ref"]
    )
    with global_session(), Session(get_engine()) as foreign_session:
        with pytest.raises(authority.AuthorityRequestDeniedError):
            _invoke(args, request, existing_session=foreign_session)


def test_after_view_as_browser_cookie_is_denied_before_private_access(owned):
    args, _, _, _, path = owned
    with TestClient(
        _app(args["verified_clerk_subject"], view_as_cookie=True)
    ) as client:
        response = client.get(path, headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "VIEW_AS_BINDING_STALE"
