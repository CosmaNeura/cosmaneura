"""PostgreSQL concurrency regressions for physician Visit Prep commands."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Thread, current_thread
from time import monotonic, sleep
from typing import Any, cast
from uuid import uuid4

import pytest

from app.services import visit_preps as visit_prep_service
from app.services.visit_prep_question_sets import ensure_default_question_set
from app.services.visit_preps import (
    RecipientInput,
    VisitPrepConflict,
    VisitPrepMutationResult,
    access_token_hash,
    create_visit_prep,
    revoke_visit_prep,
    rotate_visit_prep_credential,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepScheduleGeneration,
)
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select


@dataclass(frozen=True)
class _Scope:
    doctor: Doctor
    practice: Practice
    owner: PracticeMember
    question_set_version_id: TypeIDType


def _scope(label: str, *, now: datetime) -> _Scope:
    nonce = secrets.token_hex(5)
    doctor = Doctor(
        clerk_id=f"user_visit_prep_command_lock_{label}_{nonce}",
        email=f"visit-prep-command-lock-{label}-{nonce}@example.test",
        biography="Synthetic Visit Prep command-lock test account.",
    ).save()
    practice = Practice(
        legal_name=f"Synthetic Visit Prep Command Lock {label} {nonce}",
        join_code=f"L{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.test",
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=now - timedelta(days=1),
        negotiated_baa_reference=f"test:visit-prep-command-lock:{label}:{nonce}",
        billing_exempt=True,
    ).save()
    owner = PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="owner",
        attested_at=now - timedelta(days=1),
        attestation_name="Synthetic Physician",
    ).save()
    seeded = ensure_default_question_set(
        owner_practice_member_id=owner.id,
        now=datetime.now(UTC) + timedelta(seconds=1),
    )
    return _Scope(
        doctor=doctor,
        practice=practice,
        owner=owner,
        question_set_version_id=seeded.version.id,
    )


def _create_command(
    scope: _Scope,
    *,
    idempotency_key: str,
    now: datetime,
    scheduled_at: datetime,
) -> VisitPrepMutationResult:
    return create_visit_prep(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        idempotency_key=idempotency_key,
        recipient=RecipientInput(
            first_name="Synthetic Patient",
            role="patient",
            email="synthetic-patient@example.com",
            phone="+12025550123",
        ),
        physician_display_name="Dr. Synthetic",
        scheduled_at=scheduled_at,
        timezone_name="UTC",
        question_set_version_id=scope.question_set_version_id,
        now=now,
    )


def _race(
    *commands: Callable[[], VisitPrepMutationResult],
) -> tuple[list[VisitPrepMutationResult], list[BaseException]]:
    """Release independent database sessions together and collect both outcomes."""

    from threading import Barrier

    starting_line = Barrier(len(commands))

    def invoke(
        command: Callable[[], VisitPrepMutationResult],
    ) -> VisitPrepMutationResult:
        starting_line.wait(timeout=5)
        return command()

    results: list[VisitPrepMutationResult] = []
    errors: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        futures = [executor.submit(invoke, command) for command in commands]
        for future in futures:
            try:
                results.append(future.result(timeout=10))
            except BaseException as exc:  # pragma: no cover - asserted by callers
                errors.append(exc)
    return results, errors


def _lineage(
    visit_prep_id: TypeIDType,
) -> tuple[
    VisitPrep,
    list[VisitPrepScheduleGeneration],
    list[VisitPrepCredentialGeneration],
]:
    with get_session() as session:
        visit_prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == visit_prep_id)
        ).one()
        schedules = list(
            session.exec(
                select(VisitPrepScheduleGeneration)
                .where(VisitPrepScheduleGeneration.visit_prep_id == visit_prep_id)
                .order_by(cast(Any, VisitPrepScheduleGeneration.generation))
            ).all()
        )
        credentials = list(
            session.exec(
                select(VisitPrepCredentialGeneration)
                .where(VisitPrepCredentialGeneration.visit_prep_id == visit_prep_id)
                .order_by(cast(Any, VisitPrepCredentialGeneration.generation))
            ).all()
        )
    return visit_prep, schedules, credentials


def test_concurrent_same_key_create_issues_plaintext_once() -> None:
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduled_at = now + timedelta(days=7)
    scope = _scope("create", now=now)
    idempotency_key = str(uuid4())

    def create() -> VisitPrepMutationResult:
        return _create_command(
            scope,
            idempotency_key=idempotency_key,
            now=now,
            scheduled_at=scheduled_at,
        )

    results, errors = _race(create, create)

    assert errors == []
    assert sorted(result.disposition for result in results) == ["created", "replayed"]
    assert len({result.visit_prep.id for result in results}) == 1
    issued_tokens = [result.access_token for result in results if result.access_token]
    assert len(issued_tokens) == 1

    visit_prep_id = results[0].visit_prep.id
    _visit_prep, schedules, credentials = _lineage(visit_prep_id)
    assert len(schedules) == 1
    assert len(credentials) == 1
    assert credentials[0].token_hash == access_token_hash(issued_tokens[0])

    with get_session() as session:
        matching_aggregates = session.exec(
            select(VisitPrep).where(
                VisitPrep.owner_practice_member_id == scope.owner.id,
                VisitPrep.creation_idempotency_key == idempotency_key,
            )
        ).all()
    assert len(matching_aggregates) == 1


def test_concurrent_rotation_with_same_generation_has_one_winner() -> None:
    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduled_at = now + timedelta(days=7)
    scope = _scope("rotate", now=now)
    created = _create_command(
        scope,
        idempotency_key=str(uuid4()),
        now=now,
        scheduled_at=scheduled_at,
    )

    def rotate() -> VisitPrepMutationResult:
        return rotate_visit_prep_credential(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=created.visit_prep.id,
            expected_credential_generation=1,
            now=now + timedelta(minutes=5),
        )

    results, errors = _race(rotate, rotate)

    assert len(results) == 1
    assert results[0].disposition == "rotated"
    assert results[0].access_token is not None
    assert len(errors) == 1
    assert isinstance(errors[0], VisitPrepConflict)
    assert "stale" in str(errors[0])

    _visit_prep, schedules, credentials = _lineage(created.visit_prep.id)
    assert len(schedules) == 1
    assert [credential.generation for credential in credentials] == [1, 2]
    assert credentials[0].revoked_at is not None
    active_credentials = [
        credential for credential in credentials if credential.revoked_at is None
    ]
    assert len(active_credentials) == 1
    assert active_credentials[0].generation == 2
    assert active_credentials[0].token_hash == access_token_hash(
        results[0].access_token
    )


def test_copy_holds_the_command_fence_against_concurrent_revoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale revoke must not revoke the successor credential from a copy."""

    now = datetime.now(UTC) + timedelta(seconds=2)
    scheduled_at = now + timedelta(days=7)
    scope = _scope("copy-revoke", now=now)
    created = _create_command(
        scope,
        idempotency_key=str(uuid4()),
        now=now,
        scheduled_at=scheduled_at,
    )

    original_lock = visit_prep_service._lock_visit_prep
    copy_holds_root = Event()
    release_copy = Event()
    revoke_started = Event()
    revoke_finished = Event()
    results: list[VisitPrepMutationResult] = []
    errors: list[BaseException] = []

    def lock_visit_prep_and_pause(
        session: Session,
        *,
        owner_practice_member_id: TypeIDType,
        visit_prep_id: TypeIDType,
    ) -> VisitPrep:
        visit_prep = original_lock(
            session,
            owner_practice_member_id=owner_practice_member_id,
            visit_prep_id=visit_prep_id,
        )
        if current_thread().name == "visit-prep-copy":
            copy_holds_root.set()
            if not release_copy.wait(timeout=5):
                raise RuntimeError("timed out waiting to release the copy command")
        return visit_prep

    monkeypatch.setattr(
        visit_prep_service,
        "_lock_visit_prep",
        lock_visit_prep_and_pause,
    )

    def copy_credential() -> None:
        try:
            results.append(
                rotate_visit_prep_credential(
                    owner_practice_member_id=scope.owner.id,
                    owner_doctor_id=scope.doctor.id,
                    practice_id=scope.practice.id,
                    visit_prep_id=created.visit_prep.id,
                    expected_credential_generation=1,
                    now=now + timedelta(minutes=5),
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def revoke() -> None:
        try:
            revoke_started.set()
            results.append(
                revoke_visit_prep(
                    owner_practice_member_id=scope.owner.id,
                    owner_doctor_id=scope.doctor.id,
                    practice_id=scope.practice.id,
                    visit_prep_id=created.visit_prep.id,
                    expected_credential_generation=1,
                    reason="physician_revoked",
                    now=now + timedelta(minutes=6),
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            revoke_finished.set()

    copy_thread = Thread(target=copy_credential, name="visit-prep-copy")
    revoke_thread = Thread(target=revoke, name="visit-prep-revoke")
    copy_thread.start()
    try:
        assert copy_holds_root.wait(timeout=5), "copy never locked the Visit Prep"
        revoke_thread.start()
        assert revoke_started.wait(timeout=5), "concurrent revoke never started"
        assert not revoke_finished.wait(timeout=0.25), (
            "revoke did not wait for the Visit Prep command fence"
        )
    finally:
        release_copy.set()
        copy_thread.join(timeout=5)
        if revoke_thread.ident is not None:
            revoke_thread.join(timeout=5)

    assert not copy_thread.is_alive()
    assert not revoke_thread.is_alive()
    assert len(results) == 1
    assert results[0].disposition == "rotated"
    assert results[0].access_token is not None
    assert len(errors) == 1
    assert isinstance(errors[0], VisitPrepConflict)
    assert "stale" in str(errors[0])

    visit_prep, _schedules, credentials = _lineage(created.visit_prep.id)
    assert visit_prep.revoked_at is None
    assert [credential.generation for credential in credentials] == [1, 2]
    assert credentials[0].revocation_reason == "rotated"
    assert credentials[1].revoked_at is None
    assert credentials[1].token_hash == access_token_hash(results[0].access_token)


def test_physician_command_matches_practice_first_security_lock_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("practice-first", now=now)
    original_get_session = visit_prep_service.get_session

    @contextmanager
    def tagged_service_session() -> Iterator[Session]:
        with original_get_session() as session:
            session.execute(
                text(
                    "SET LOCAL application_name = "
                    "'visit_prep_physician_practice_lock_test'"
                )
            )
            yield session

    monkeypatch.setattr(visit_prep_service, "get_session", tagged_service_session)
    request_started = Event()

    def create() -> VisitPrepMutationResult:
        request_started.set()
        return _create_command(
            scope,
            idempotency_key=str(uuid4()),
            now=now,
            scheduled_at=now + timedelta(days=7),
        )

    with get_session() as security_session:
        security_session.exec(
            select(Practice).where(Practice.id == scope.practice.id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(create)
            try:
                assert request_started.wait(timeout=5)
                wait_deadline = monotonic() + 5
                blocked = False
                while monotonic() < wait_deadline:
                    security_session.execute(text("SELECT pg_stat_clear_snapshot()"))
                    blocked = security_session.execute(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_stat_activity "
                            "WHERE application_name = "
                            "'visit_prep_physician_practice_lock_test' "
                            "AND wait_event_type = 'Lock')"
                        )
                    ).scalar_one()
                    if blocked:
                        break
                    sleep(0.02)
                assert blocked

                # A member-first physician command would hold this row while
                # waiting on Practice and form a cycle with offboarding.
                security_session.exec(
                    select(Doctor).where(Doctor.id == scope.doctor.id).with_for_update()
                ).one()
                security_session.exec(
                    select(PracticeMember)
                    .where(PracticeMember.id == scope.owner.id)
                    .with_for_update()
                ).one()
            finally:
                security_session.rollback()

            result = future.result(timeout=10)

    assert result.disposition == "created"


def test_physician_command_fails_fast_against_doctor_then_practice_lock_order() -> None:
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("doctor-practice-inversion", now=now)

    with get_session() as inverse_session:
        inverse_session.exec(
            select(Doctor).where(Doctor.id == scope.doctor.id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _create_command,
                scope,
                idempotency_key=str(uuid4()),
                now=now,
                scheduled_at=now + timedelta(days=7),
            )
            with pytest.raises(OperationalError) as caught:
                future.result(timeout=5)

        assert getattr(caught.value.orig, "sqlstate", None) == "55P03"

        # The failed command rolled back its Practice lock, so the inverse
        # holder can complete D -> P without a deadlock.
        inverse_session.exec(
            select(Practice)
            .where(Practice.id == scope.practice.id)
            .with_for_update(nowait=True)
        ).one()
