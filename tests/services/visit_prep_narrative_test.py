"""The final physician-owned topic is an unconditional patient narrative."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services.visit_prep_question_sets import (
    CANONICAL_DEFAULT_QUESTION_SET,
    VisitPrepQuestionSetNotFound,
    VisitPrepQuestionSetValidation,
    create_question_set,
    get_question_set_version,
    publish_question_set_version,
)

from tests.services.visit_prep_question_sets_test import _owner


def _with_narrative(**changes):
    original = CANONICAL_DEFAULT_QUESTION_SET
    return replace(
        original,
        questions=(
            *original.questions[:-1],
            replace(original.questions[-1], **changes),
        ),
    )


@pytest.mark.parametrize(
    "prompt",
    (
        "Tell me your story",
        "Tell me about your medical history",
        "Tell me what happened since the last time you saw me",
    ),
)
def test_physician_can_publish_different_open_ended_prompts(prompt):
    owner = _owner("narrative")
    created = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=str(uuid4()),
        definition=_with_narrative(prompt=prompt),
    )
    published = publish_question_set_version(
        owner_practice_member_id=owner.id,
        version_id=created.version.id,
        now=datetime.now(UTC) + timedelta(seconds=1),
    )
    narrative = get_question_set_version(
        owner_practice_member_id=owner.id,
        version_id=published.version.id,
    ).questions[-1]
    assert narrative.prompt == prompt
    assert narrative.kind == "story"
    assert narrative.show_if is None
    assert narrative.choices == ()


def test_failed_narrative_publication_does_not_publish_the_draft():
    owner = _owner("narrative-invalid")
    created = create_question_set(
        owner_practice_member_id=owner.id,
        idempotency_key=str(uuid4()),
        definition=_with_narrative(kind="list"),
    )
    with pytest.raises(
        VisitPrepQuestionSetValidation, match="unconditional free-form story"
    ):
        publish_question_set_version(
            owner_practice_member_id=owner.id, version_id=created.version.id
        )
    assert (
        get_question_set_version(
            owner_practice_member_id=owner.id,
            version_id=created.version.id,
        ).published_at
        is None
    )


def test_inactive_owner_cannot_mutate_question_sets():
    owner = _owner("inactive")
    from tests.factories import save_practice_member

    owner.seat_state = "inactive"
    save_practice_member(owner)
    with pytest.raises(VisitPrepQuestionSetNotFound):
        create_question_set(
            owner_practice_member_id=owner.id,
            idempotency_key=str(uuid4()),
            definition=CANONICAL_DEFAULT_QUESTION_SET,
        )
