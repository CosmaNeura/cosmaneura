import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services.visit_prep_question_sets import (
    CANONICAL_DEFAULT_QUESTION_SET,
    DEFAULT_QUESTION_SET_NAME,
    DEFAULT_QUESTION_SET_SEED_KEY,
    QuestionDraftInput,
    QuestionSetVersionInput,
    VisitPrepQuestionSetConflict,
    VisitPrepQuestionSetNotFound,
    VisitPrepQuestionSetValidation,
    create_question_set,
    create_successor_draft,
    ensure_default_question_set,
    get_question_set,
    get_question_set_version,
    list_question_sets,
    publish_question_set_version,
    replace_question_set_draft,
    set_default_question_set_version,
)

from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.visit_prep_question_set import (
    VisitPrepQuestion,
    VisitPrepQuestionSet,
    VisitPrepQuestionSetVersion,
)

from tests.factories import save_practice_member


def _owner(label: str) -> PracticeMember:
    nonce = secrets.token_hex(6)
    doctor = Doctor(
        clerk_id=f"user_visit_prep_domain_{label}_{nonce}",
        email=f"visit-prep-domain-{label}-{nonce}@example.com",
        biography="Synthetic Visit Prep domain test account.",
    ).save()
    practice = Practice(
        legal_name=f"Synthetic Visit Prep Domain Practice {nonce}",
        join_code=f"Q{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
    ).save()
    return save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=doctor.id,
            role="owner",
        )
    )


def _draft(*, name: str = "Synthetic questions") -> QuestionSetVersionInput:
    return QuestionSetVersionInput(
        name=name,
        questions=(
            QuestionDraftInput(
                key="chief_concern",
                prompt="What would you most like to discuss?",
                kind="story",
            ),
            QuestionDraftInput(
                key="medications",
                prompt="Which medications are you prescribed?",
                kind="list",
            ),
        ),
    )


def _publishable(*, name: str = "Synthetic five questions") -> QuestionSetVersionInput:
    return replace(CANONICAL_DEFAULT_QUESTION_SET, name=name)


def _future_time(value: datetime | None) -> datetime:
    assert value is not None
    return value.astimezone(UTC) + timedelta(seconds=1)


def _required_time(value: datetime | None) -> datetime:
    assert value is not None
    return value


def test_seed_materialization_has_the_exact_approved_content_and_is_idempotent() -> (
    None
):
    owner = _owner("seed")
    published_at = datetime.now(UTC) + timedelta(seconds=1)

    created = ensure_default_question_set(
        owner_practice_member_id=owner.id,
        now=published_at,
    )
    replayed = ensure_default_question_set(
        owner_practice_member_id=owner.id,
        now=published_at + timedelta(minutes=1),
    )

    assert created.disposition == "created"
    assert replayed.disposition == "replayed"
    assert replayed.question_set.id == created.question_set.id
    assert replayed.version.id == created.version.id
    assert created.question_set.seed_key == DEFAULT_QUESTION_SET_SEED_KEY
    assert created.version.name == DEFAULT_QUESTION_SET_NAME
    assert created.version.published_at == published_at
    assert created.version.is_default is True
    assert [question.position for question in created.version.questions] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert [question.key for question in created.version.questions] == [
        "reason",
        "medications",
        "adherence",
        "adherence_barrier",
        "story",
    ]
    assert [question.prompt for question in created.version.questions] == [
        "What is the main reason for this visit?",
        "What medications are you prescribed right now?",
        "Are you taking them as prescribed?",
        "If not — what got in the way?",
        "Tell me your health story — whatever feels relevant.",
    ]
    assert [question.kind for question in created.version.questions] == [
        "story",
        "list",
        "choice",
        "story",
        "story",
    ]
    assert [question.helper_text for question in created.version.questions] == [
        "In your own words. The thing you most want understood before you sit down.",
        "Include the dose if you know it. Over-the-counter and supplements count too.",
        "There is no wrong answer. Honesty here saves the visit.",
        (
            "Cost, side effects, a chaotic morning, forgetting, not being sure it "
            "helps. All of that is useful."
        ),
        (
            "What has been happening in your body, and in your life, that belongs "
            "in this visit?"
        ),
    ]
    adherence = created.version.questions[2]
    barrier = created.version.questions[3]
    assert [
        (choice.value, choice.label, choice.hint) for choice in adherence.choices
    ] == [
        ("yes", "Yes, as prescribed", "Most days, as written"),
        ("some", "Some of them", "A few get skipped"),
        ("no", "Not really", "Something got in the way"),
        ("none", "I don't take any", None),
    ]
    assert barrier.show_if is not None
    assert barrier.show_if.question_id == adherence.id
    assert barrier.show_if.question_key == "adherence"
    assert barrier.show_if.values == ("some", "no")
    assert (
        VisitPrepQuestionSet.where(
            VisitPrepQuestionSet.owner_practice_member_id == owner.id
        ).count()
        == 1
    )
    assert (
        VisitPrepQuestionSetVersion.where(
            VisitPrepQuestionSetVersion.owner_practice_member_id == owner.id
        ).count()
        == 1
    )
    assert (
        VisitPrepQuestion.where(
            VisitPrepQuestion.question_set_version_id == created.version.id
        ).count()
        == 5
    )


def test_custom_create_replays_only_the_same_normalized_payload() -> None:
    owner = _owner("create-idempotency")
    idempotency_key = str(uuid4())
    first_definition = QuestionSetVersionInput(
        name="  Synthetic questions  ",
        questions=(
            QuestionDraftInput(
                key="chief_concern",
                prompt="  What would you most like to discuss?\r\n",
                kind="story",
            ),
        ),
    )

    created = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=idempotency_key,
        definition=first_definition,
    )
    replayed = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=idempotency_key,
        definition=QuestionSetVersionInput(
            name="Synthetic questions",
            questions=(
                QuestionDraftInput(
                    key="chief_concern",
                    prompt="What would you most like to discuss?",
                    kind="story",
                ),
            ),
        ),
    )

    assert created.disposition == "created"
    assert replayed.disposition == "replayed"
    assert replayed.question_set.id == created.question_set.id
    assert replayed.version.id == created.version.id
    assert replayed.version.questions[0].id == created.version.questions[0].id

    with pytest.raises(
        VisitPrepQuestionSetConflict,
        match="different payload",
    ):
        create_question_set(
            owner_practice_member_id=owner.id,
            idempotency_key=idempotency_key,
            definition=_draft(name="A divergent retry"),
        )


def test_create_key_remains_bound_to_original_payload_after_draft_edit() -> None:
    owner = _owner("create-idempotency-after-edit")
    idempotency_key = str(uuid4())
    original = _draft(name="Original create payload")
    created = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=idempotency_key,
        definition=original,
    )
    edited = _draft(name="Later draft edit")
    original_updated_at = _required_time(created.version.updated_at)
    updated = replace_question_set_draft(
        owner_practice_member_id=owner.id,
        version_id=created.version.id,
        expected_updated_at=original_updated_at,
        definition=edited,
        now=_future_time(original_updated_at),
    )

    original_retry = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=idempotency_key,
        definition=original,
    )

    assert original_retry.disposition == "replayed"
    assert original_retry.version.id == updated.version.id
    assert original_retry.version.name == "Later draft edit"
    with pytest.raises(
        VisitPrepQuestionSetConflict,
        match="different payload",
    ):
        create_question_set(
            owner_practice_member_id=owner.id,
            idempotency_key=idempotency_key,
            definition=edited,
        )


def test_publish_rejects_incomplete_draft_without_changing_it() -> None:
    owner = _owner("incomplete-publish")
    created = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=str(uuid4()),
        definition=_draft(),
    )

    with pytest.raises(
        VisitPrepQuestionSetValidation,
        match="question count is invalid",
    ):
        publish_question_set_version(
            owner_practice_member_id=owner.id,
            version_id=created.version.id,
        )

    current = get_question_set_version(
        owner_practice_member_id=owner.id,
        version_id=created.version.id,
    )
    assert current.published_at is None
    assert current.is_default is False
    assert [question.key for question in current.questions] == [
        "chief_concern",
        "medications",
    ]


def test_replace_uses_optimistic_concurrency_but_allows_exact_retry() -> None:
    owner = _owner("optimistic")
    created = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=str(uuid4()),
        definition=_draft(),
    )
    original_updated_at = _required_time(created.version.updated_at)
    replacement = _draft(name="Updated synthetic questions")

    updated = replace_question_set_draft(
        owner_practice_member_id=owner.id,
        version_id=created.version.id,
        expected_updated_at=original_updated_at,
        definition=replacement,
        now=_future_time(original_updated_at),
    )
    exact_retry = replace_question_set_draft(
        owner_practice_member_id=owner.id,
        version_id=created.version.id,
        expected_updated_at=original_updated_at,
        definition=replacement,
    )

    assert updated.disposition == "updated"
    assert exact_retry.disposition == "replayed"
    assert exact_retry.version.updated_at == updated.version.updated_at
    assert [question.id for question in exact_retry.version.questions] == [
        question.id for question in updated.version.questions
    ]

    with pytest.raises(VisitPrepQuestionSetConflict, match="draft is stale"):
        replace_question_set_draft(
            owner_practice_member_id=owner.id,
            version_id=created.version.id,
            expected_updated_at=original_updated_at,
            definition=_draft(name="A conflicting stale edit"),
        )

    current = get_question_set_version(
        owner_practice_member_id=owner.id,
        version_id=created.version.id,
    )
    assert current.name == "Updated synthetic questions"


def test_owner_scoping_hides_foreign_set_and_version_ids() -> None:
    owner = _owner("owner")
    other_owner = _owner("other-owner")
    created = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=str(uuid4()),
        definition=_draft(),
    )

    with pytest.raises(VisitPrepQuestionSetNotFound):
        get_question_set(
            owner_practice_member_id=other_owner.id,
            question_set_id=created.question_set.id,
        )
    with pytest.raises(VisitPrepQuestionSetNotFound):
        get_question_set_version(
            owner_practice_member_id=other_owner.id,
            version_id=created.version.id,
        )
    with pytest.raises(VisitPrepQuestionSetNotFound):
        publish_question_set_version(
            owner_practice_member_id=other_owner.id,
            version_id=created.version.id,
        )


def test_listing_is_read_only_and_never_materializes_a_seed() -> None:
    owner = _owner("read-only-list")

    assert list_question_sets(owner_practice_member_id=owner.id) == ()
    assert (
        VisitPrepQuestionSet.where(
            VisitPrepQuestionSet.owner_practice_member_id == owner.id
        ).count()
        == 0
    )


def test_default_switch_is_atomic_and_reselect_is_idempotent() -> None:
    owner = _owner("default-switch")
    seed = ensure_default_question_set(
        owner_practice_member_id=owner.id,
        now=datetime.now(UTC) + timedelta(seconds=1),
    )
    custom = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=str(uuid4()),
        definition=_publishable(),
    )
    published = publish_question_set_version(
        owner_practice_member_id=owner.id,
        version_id=custom.version.id,
        make_default=True,
        now=_future_time(custom.version.created_at),
    )
    replayed = set_default_question_set_version(
        owner_practice_member_id=owner.id,
        version_id=custom.version.id,
    )

    assert published.disposition == "updated"
    assert published.version.is_default is True
    assert replayed.disposition == "replayed"
    sets = list_question_sets(owner_practice_member_id=owner.id)
    versions = [version for question_set in sets for version in question_set.versions]
    assert [version.id for version in versions if version.is_default] == [
        custom.version.id
    ]
    assert (
        next(
            version for version in versions if version.id == seed.version.id
        ).is_default
        is False
    )


def test_unpublished_version_cannot_be_selected_as_default() -> None:
    owner = _owner("draft-default")
    draft = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=str(uuid4()),
        definition=_draft(),
    )

    with pytest.raises(
        VisitPrepQuestionSetConflict,
        match="only a published question-set version can be selected",
    ):
        set_default_question_set_version(
            owner_practice_member_id=owner.id,
            version_id=draft.version.id,
        )


def test_successor_creation_is_linear_and_idempotent() -> None:
    owner = _owner("successor")
    first = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=str(uuid4()),
        definition=_publishable(name="Original version"),
    )
    published = publish_question_set_version(
        owner_practice_member_id=owner.id,
        version_id=first.version.id,
        now=_future_time(first.version.created_at),
    )

    successor = create_successor_draft(
        owner_practice_member_id=owner.id,
        source_version_id=published.version.id,
        name="Next version",
    )
    replayed = create_successor_draft(
        owner_practice_member_id=owner.id,
        source_version_id=published.version.id,
        name="Next version",
    )

    assert successor.disposition == "created"
    assert replayed.disposition == "replayed"
    assert replayed.version.id == successor.version.id
    assert successor.version.version_number == 2
    assert successor.version.supersedes_version_id == published.version.id
    assert successor.version.published_at is None
    assert [question.id for question in successor.version.questions] != [
        question.id for question in published.version.questions
    ]
    assert [
        (question.key, question.prompt, question.kind, question.show_if)
        for question in successor.version.questions
    ] == [
        (question.key, question.prompt, question.kind, question.show_if)
        for question in replayed.version.questions
    ]

    with pytest.raises(
        VisitPrepQuestionSetConflict,
        match="different name",
    ):
        create_successor_draft(
            owner_practice_member_id=owner.id,
            source_version_id=published.version.id,
            name="A divergent retry",
        )

    published_successor = publish_question_set_version(
        owner_practice_member_id=owner.id,
        version_id=successor.version.id,
        now=_future_time(successor.version.created_at),
    )
    assert published_successor.version.published_at is not None
    with pytest.raises(
        VisitPrepQuestionSetConflict,
        match="no longer an editable lineage tip",
    ):
        create_successor_draft(
            owner_practice_member_id=owner.id,
            source_version_id=published.version.id,
            name="Next version",
        )


def test_published_version_refuses_draft_replacement() -> None:
    owner = _owner("published-edit")
    draft = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=str(uuid4()),
        definition=_publishable(),
    )
    published = publish_question_set_version(
        owner_practice_member_id=owner.id,
        version_id=draft.version.id,
        now=_future_time(draft.version.created_at),
    )

    with pytest.raises(
        VisitPrepQuestionSetConflict,
        match="published question-set versions cannot be edited",
    ):
        replace_question_set_draft(
            owner_practice_member_id=owner.id,
            version_id=published.version.id,
            expected_updated_at=_required_time(published.version.updated_at),
            definition=_publishable(name="Forbidden edit"),
        )


def test_revoked_owner_cannot_mutate_question_sets() -> None:
    owner = _owner("revoked")
    revoked_at = datetime.now(UTC)
    owner.access_revoked_at = revoked_at
    owner.access_revoked_by_ref = "test-suite"
    owner.access_revocation_reason = "Test offboarding"
    owner.access_records_retain_until = revoked_at + timedelta(days=365 * 8)
    save_practice_member(owner)

    with pytest.raises(
        VisitPrepQuestionSetNotFound,
        match="owner was not found",
    ):
        create_question_set(
            owner_practice_member_id=owner.id,
            idempotency_key=str(uuid4()),
            definition=_draft(),
        )


@pytest.mark.parametrize(
    ("idempotency_key", "message"),
    (
        ("not-a-uuid", "idempotency key is invalid"),
        (str(uuid4()).upper(), "idempotency key is not canonical"),
    ),
)
def test_create_rejects_noncanonical_idempotency_keys(
    idempotency_key: str,
    message: str,
) -> None:
    owner = _owner("invalid-idempotency")

    with pytest.raises(VisitPrepQuestionSetValidation, match=message):
        create_question_set(
            owner_practice_member_id=owner.id,
            idempotency_key=idempotency_key,
            definition=_draft(),
        )
