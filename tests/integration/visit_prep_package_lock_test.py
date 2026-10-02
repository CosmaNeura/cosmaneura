"""Independent PostgreSQL connection evidence for provider reads under locks."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest

from app.services import visit_prep_interviews as interviews
from app.services import visit_prep_packages as packages
from app.services.visit_prep_contributions import correct_contribution
from app.services.visit_prep_interview_contract import ResponseInput
from app.services.visit_preps import VisitPrepNotFound, revoke_visit_prep

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from sqlalchemy import text
from sqlmodel import select

from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
)
from tests.services.visit_prep_packages_test import _read, _scope


def test_revocation_committed_before_waiting_provider_read_prevents_disclosure(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "package-lock-revoke")
    locked, release = Event(), Event()
    original = packages._lock_owner_scope

    def reached_owner_lock(*args, **kwargs):
        locked.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(packages, "_lock_owner_scope", reached_owner_lock)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_read, fixture)
        try:
            assert locked.wait(timeout=5)
            revoke_visit_prep(
                **_scope(fixture, package=False),
                visit_prep_id=fixture.prep_id,
                expected_credential_generation=1,
                reason="physician_revoked",
            )
        finally:
            release.set()
        with pytest.raises(VisitPrepNotFound):
            future.result(timeout=10)
    with get_session() as session:
        assert not session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
                AuthorityAuditEvent.event_type == "visit_prep.package_read",
            )
        ).all()


def test_provider_read_holds_a_consistent_basis_until_its_audit_commits(monkeypatch):
    fixture = _prepared(monkeypatch, "package-lock-correct")
    basis = _projection(fixture).basis
    loaded, release = Event(), Event()
    original = packages._load_contribution

    def snapshot_under_lock(*args, **kwargs):
        contribution = original(*args, **kwargs)
        loaded.set()
        assert release.wait(timeout=5)
        return contribution

    monkeypatch.setattr(packages, "_load_contribution", snapshot_under_lock)
    original_session = interviews.get_session

    @contextmanager
    def tagged_session():
        with original_session() as session:
            session.execute(
                text(
                    "SET LOCAL application_name = 'visit_prep_package_correction_race'"
                )
            )
            yield session

    monkeypatch.setattr(interviews, "get_session", tagged_session)
    with ThreadPoolExecutor(max_workers=2) as executor:
        reading = executor.submit(_read, fixture)
        try:
            assert loaded.wait(timeout=5)
            # No ambient session is inherited; correction uses its own connection.
            correcting = executor.submit(
                correct_contribution,
                access_token=fixture.access_token,
                first_name="José",
                role="patient",
                expected_basis=basis,
                idempotency_key=str(uuid4()),
                response=ResponseInput(
                    "story", "answered", "Synthetic story after provider snapshot."
                ),
            )
            deadline = monotonic() + 5
            blocked = False
            with get_session() as observer:
                while monotonic() < deadline:
                    observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                    blocked = observer.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name = 'visit_prep_package_correction_race' AND wait_event_type = 'Lock')"
                        )
                    ).scalar_one()
                    if blocked:
                        break
                    sleep(0.02)
            assert blocked and not correcting.done()
            release.set()
            before = reading.result(timeout=10)
            correcting.result(timeout=10)
        finally:
            release.set()
    monkeypatch.setattr(packages, "_load_contribution", original)
    after = _read(fixture)
    assert before.contribution.basis.event_sequence == 0
    assert after.contribution.basis.event_sequence == 1
    assert before.source_version != after.source_version
