import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event, Thread, current_thread
from uuid import uuid4

from app.services.visit_prep_question_sets import (
    CANONICAL_DEFAULT_QUESTION_SET,
    VisitPrepQuestionSetConflict,
    create_question_set,
    get_question_set_version,
    publish_question_set_version,
    replace_question_set_draft,
)

from activemodel.session_manager import SessionManager
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from sqlalchemy import event

from tests.factories import save_practice_member


def _owner() -> PracticeMember:
    nonce = secrets.token_hex(6)
    doctor = Doctor(
        clerk_id=f"user_visit_prep_publish_lock_{nonce}",
        email=f"visit-prep-publish-lock-{nonce}@example.com",
        biography="Synthetic Visit Prep publication lock test account.",
    ).save()
    practice = Practice(
        legal_name=f"Synthetic Visit Prep Lock Practice {nonce}",
        join_code=f"L{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
    ).save()
    return save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="owner",
        )
    )


def test_publish_lock_serializes_a_concurrent_question_mutation() -> None:
    owner = _owner()
    draft = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=str(uuid4()),
        definition=CANONICAL_DEFAULT_QUESTION_SET,
    )
    publish_at = (draft.version.created_at or datetime.now(UTC)) + timedelta(seconds=1)
    draft_updated_at = draft.version.updated_at
    assert draft_updated_at is not None

    engine = SessionManager.get_instance().get_engine()
    version_lock_held = Event()
    release_publish = Event()
    mutation_started = Event()
    mutation_finished = Event()
    published = []
    publish_errors: list[BaseException] = []
    mutation_errors: list[BaseException] = []

    def pause_after_version_lock(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if (
            current_thread().name == "visit-prep-publisher"
            and "FROM visit_prep_question_set_version" in statement
            and "FOR UPDATE" in statement
        ):
            version_lock_held.set()
            if not release_publish.wait(timeout=5):
                raise RuntimeError("timed out waiting to release the publication lock")

    def publish() -> None:
        try:
            published.append(
                publish_question_set_version(
                    owner_practice_member_id=owner.id,
                    version_id=draft.version.id,
                    now=publish_at,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted in main thread
            publish_errors.append(exc)

    def mutate_question() -> None:
        try:
            mutation_started.set()
            replace_question_set_draft(
                owner_practice_member_id=owner.id,
                version_id=draft.version.id,
                expected_updated_at=draft_updated_at,
                definition=replace(
                    CANONICAL_DEFAULT_QUESTION_SET,
                    name="A concurrent mutation must not win",
                ),
                now=publish_at + timedelta(seconds=1),
            )
        except BaseException as exc:  # pragma: no cover - asserted in main thread
            mutation_errors.append(exc)
        finally:
            mutation_finished.set()

    event.listen(engine, "after_cursor_execute", pause_after_version_lock)
    publisher = Thread(target=publish, name="visit-prep-publisher")
    mutator = Thread(target=mutate_question, name="visit-prep-mutator")
    try:
        publisher.start()
        assert version_lock_held.wait(timeout=5), "publish never locked the version row"
        mutator.start()
        assert mutation_started.wait(timeout=5), "concurrent mutation never started"
        assert not mutation_finished.wait(timeout=0.25), (
            "question mutation did not wait for the publication fence"
        )
    finally:
        release_publish.set()
        publisher.join(timeout=5)
        mutator.join(timeout=5)
        event.remove(engine, "after_cursor_execute", pause_after_version_lock)

    assert not publisher.is_alive()
    assert not mutator.is_alive()
    assert publish_errors == []
    assert len(published) == 1
    assert published[0].version.published_at == publish_at
    assert len(mutation_errors) == 1
    assert isinstance(mutation_errors[0], VisitPrepQuestionSetConflict)
    assert "published question-set versions cannot be edited" in str(mutation_errors[0])

    current = get_question_set_version(
        owner_practice_member_id=owner.id,
        version_id=draft.version.id,
    )
    assert current.name == CANONICAL_DEFAULT_QUESTION_SET.name
