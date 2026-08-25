import json
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from typeid import TypeID

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.visit_prep_question_set import (
    VisitPrepQuestion,
    VisitPrepQuestionChoice,
    VisitPrepQuestionSet,
    VisitPrepQuestionSetVersion,
)
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel


def _owner(label: str = "owner") -> PracticeMember:
    nonce = secrets.token_hex(6)
    doctor = Doctor(
        clerk_id=f"user_visit_prep_questions_{label}_{nonce}",
        email=f"visit-prep-{label}-{nonce}@example.com",
        biography="Synthetic Visit Prep model test account.",
    ).save()
    practice = Practice(
        legal_name=f"Synthetic Visit Prep Practice {nonce}",
        join_code=f"V{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
    ).save()
    return PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="owner",
    ).save()


def _question_set(
    owner: PracticeMember, *, seed_key: str | None = None
) -> VisitPrepQuestionSet:
    return VisitPrepQuestionSet(
        owner_practice_member_id=owner.id,
        seed_key=seed_key,
    ).save()


def _version(
    question_set: VisitPrepQuestionSet,
    *,
    version_number: int = 1,
    name: str = "Synthetic Visit Prep questions",
    published: bool = False,
    is_default: bool = False,
    supersedes_version_id=None,
) -> VisitPrepQuestionSetVersion:
    published_at = datetime.now(UTC) + timedelta(minutes=1) if published else None
    return VisitPrepQuestionSetVersion(
        question_set_id=question_set.id,
        owner_practice_member_id=question_set.owner_practice_member_id,
        version_number=version_number,
        name=name,
        published_at=published_at,
        is_default=is_default,
        supersedes_version_id=supersedes_version_id,
    ).save()


def _question(
    version: VisitPrepQuestionSetVersion,
    *,
    question_key: str = "reason_for_visit",
    position: int = 1,
    kind: str = "story",
    choice_options: list[VisitPrepQuestionChoice] | None = None,
    show_if_question_id=None,
    show_if_values: list[str] | None = None,
) -> VisitPrepQuestion:
    return VisitPrepQuestion(
        question_set_version_id=version.id,
        question_key=question_key,
        position=position,
        prompt_text=f"Synthetic prompt for {question_key}?",
        kind=kind,
        choice_options=choice_options or [],
        show_if_question_id=show_if_question_id,
        show_if_values=show_if_values,
    ).save()


def test_version_owner_must_match_question_set_owner() -> None:
    first_owner = _owner("first")
    second_owner = _owner("second")
    question_set = _question_set(first_owner)

    with pytest.raises(DBAPIError, match="fk_visit_prep_qset_version_owner"):
        VisitPrepQuestionSetVersion(
            question_set_id=question_set.id,
            owner_practice_member_id=second_owner.id,
            version_number=1,
            name="Mismatched synthetic owner",
        ).save()


def test_seed_key_is_unique_per_owner_but_reusable_by_another_owner() -> None:
    first_owner = _owner("seed-first")
    second_owner = _owner("seed-second")
    _question_set(first_owner, seed_key="default.v1")
    _question_set(second_owner, seed_key="default.v1")

    with pytest.raises(DBAPIError, match="uq_visit_prep_qset_owner_seed"):
        _question_set(first_owner, seed_key="default.v1")


def test_question_set_owner_and_seed_lineage_are_immutable() -> None:
    first_owner = _owner("immutable-seed-first")
    second_owner = _owner("immutable-seed-second")
    question_set = _question_set(first_owner, seed_key="default.v1")
    set_table = SQLModel.metadata.tables["visit_prep_question_set"]

    with get_session() as session:
        with pytest.raises(DBAPIError, match="seed lineage are immutable"):
            with session.begin_nested():
                session.execute(
                    update(set_table)
                    .where(set_table.c.id == question_set.id)
                    .values(seed_key="default.v2")
                )
        with pytest.raises(DBAPIError, match="seed lineage are immutable"):
            with session.begin_nested():
                session.execute(
                    update(set_table)
                    .where(set_table.c.id == question_set.id)
                    .values(owner_practice_member_id=second_owner.id)
                )


def test_version_number_is_unique_within_a_question_set() -> None:
    question_set = _question_set(_owner("version-number"))
    _version(question_set, version_number=1)

    with pytest.raises(DBAPIError, match="uq_visit_prep_qset_version_number"):
        _version(question_set, version_number=1, name="Duplicate synthetic version")


def test_draft_version_lineage_is_immutable_but_name_is_editable() -> None:
    version = _version(_question_set(_owner("draft-version-lineage")))
    version_table = SQLModel.metadata.tables["visit_prep_question_set_version"]

    version.name = "Renamed synthetic draft"
    version.save()

    with get_session() as session:
        with pytest.raises(DBAPIError, match="version lineage is immutable"):
            with session.begin_nested():
                session.execute(
                    update(version_table)
                    .where(version_table.c.id == version.id)
                    .values(version_number=2)
                )

    version.refresh()
    assert version.name == "Renamed synthetic draft"
    assert version.version_number == 1


def test_only_one_default_version_is_allowed_per_owner() -> None:
    owner = _owner("default")
    first_set = _question_set(owner, seed_key="first")
    second_set = _question_set(owner, seed_key="second")
    _version(first_set, published=True, is_default=True)

    with pytest.raises(DBAPIError, match="uq_visit_prep_qset_default_owner"):
        _version(second_set, published=True, is_default=True)


def test_default_version_must_be_published() -> None:
    question_set = _question_set(_owner("unpublished-default"))

    with pytest.raises(DBAPIError, match="default_is_published"):
        _version(question_set, is_default=True)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("question_key", "Invalid-Key"),
        ("position", 0),
        ("position", 6),
    ),
)
def test_question_key_shape_and_position_range_are_enforced(
    field: str, value: str | int
) -> None:
    version = _version(_question_set(_owner(f"range-{field}-{value}")))
    values = {"question_key": "valid_key", "position": 1}
    values[field] = value

    with pytest.raises(DBAPIError, match="violates check constraint"):
        _question(version, **values)


@pytest.mark.parametrize("duplicate_field", ("question_key", "position"))
def test_question_key_and_position_are_unique_within_a_version(
    duplicate_field: str,
) -> None:
    version = _version(_question_set(_owner(f"duplicate-{duplicate_field}")))
    _question(version, question_key="first_question", position=1)
    duplicate = {"question_key": "second_question", "position": 2}
    duplicate[duplicate_field] = {
        "question_key": "first_question",
        "position": 1,
    }[duplicate_field]

    with pytest.raises(DBAPIError, match="uq_visit_prep_question_version"):
        _question(version, **duplicate)


@pytest.mark.parametrize(
    ("kind", "choice_options"),
    (
        ("scale", []),
        ("story", [{"value": "yes", "label": "Yes"}]),
        ("choice", [{"value": "yes", "label": "Yes"}]),
        ("choice", {"yes": "Yes", "no": "No"}),
    ),
)
def test_database_rejects_invalid_kind_and_choice_json_shapes(
    kind: str,
    choice_options: object,
) -> None:
    version = _version(_question_set(_owner(f"choices-{kind}")))
    with pytest.raises(DBAPIError):
        with get_session() as session:
            session.execute(
                text(
                    "INSERT INTO visit_prep_question ("
                    "id, question_set_version_id, question_key, position, "
                    "prompt_text, kind, choice_options"
                    ") VALUES ("
                    ":id, :version_id, :question_key, :position, "
                    ":prompt_text, :kind, CAST(:choice_options AS jsonb)"
                    ")"
                ),
                params={
                    "id": TypeID("visit_prep_question").uuid,
                    "version_id": version.id.uuid,
                    "question_key": "invalid_choices",
                    "position": 1,
                    "prompt_text": "Synthetic invalid choice prompt?",
                    "kind": kind,
                    "choice_options": json.dumps(choice_options),
                },
            )


def test_conditional_question_must_reference_the_same_version() -> None:
    question_set = _question_set(_owner("conditional-version"))
    first_version = _version(question_set, version_number=1)
    second_version = _version(question_set, version_number=2)
    controlling_question = _question(
        first_version,
        question_key="has_concern",
        position=1,
        kind="choice",
        choice_options=[
            VisitPrepQuestionChoice(value="yes", label="Yes"),
            VisitPrepQuestionChoice(value="no", label="No"),
        ],
    )
    _question(
        first_version,
        question_key="concern_details",
        position=2,
        show_if_question_id=controlling_question.id,
        show_if_values=["yes"],
    )

    with get_session() as session:
        with pytest.raises(
            DBAPIError,
            match="fk_visit_prep_question_condition_version",
        ):
            with session.begin_nested():
                session.add(
                    VisitPrepQuestion(
                        question_set_version_id=second_version.id,
                        question_key="cross_version_condition",
                        position=1,
                        prompt_text="Synthetic cross-version condition?",
                        kind="story",
                        choice_options=[],
                        show_if_question_id=controlling_question.id,
                        show_if_values=["yes"],
                    )
                )
                session.flush()
                # The production constraint is intentionally deferred. The
                # test harness wraps each case in an outer transaction, so
                # make this constraint immediate inside the savepoint.
                session.execute(
                    text(
                        "SET CONSTRAINTS "
                        "fk_visit_prep_question_condition_version IMMEDIATE"
                    )
                )


def test_ordered_json_round_trips_as_typed_choices() -> None:
    version = _version(_question_set(_owner("json-order")))
    question = _question(
        version,
        question_key="priority",
        kind="choice",
        choice_options=[
            VisitPrepQuestionChoice(value="third", label="Third", hint="Last"),
            VisitPrepQuestionChoice(value="first", label="First"),
            VisitPrepQuestionChoice(value="second", label="Second"),
        ],
    )

    question.refresh()

    assert [choice.value for choice in question.choice_options] == [
        "third",
        "first",
        "second",
    ]
    assert all(
        isinstance(choice, VisitPrepQuestionChoice)
        for choice in question.choice_options
    )
    assert question.choice_options[0].hint == "Last"


def test_draft_may_have_fewer_than_five_questions() -> None:
    version = _version(_question_set(_owner("incomplete-draft")))
    _question(version, question_key="first", position=1)
    _question(version, question_key="second", position=2)

    assert version.published_at is None
    assert version.is_default is False
    assert (
        VisitPrepQuestion.where(
            VisitPrepQuestion.question_set_version_id == version.id
        ).count()
        == 2
    )


def test_published_question_content_rejects_update_and_delete() -> None:
    version = _version(_question_set(_owner("published-question")))
    question = _question(version)
    version.published_at = datetime.now(UTC) + timedelta(minutes=1)
    version.save()
    question_table = SQLModel.metadata.tables["visit_prep_question"]

    with get_session() as session:
        with pytest.raises(DBAPIError, match="published Visit Prep questions"):
            with session.begin_nested():
                session.execute(
                    update(question_table)
                    .where(question_table.c.id == question.id)
                    .values(prompt_text="Mutated prompt")
                )
        with pytest.raises(DBAPIError, match="published Visit Prep questions"):
            with session.begin_nested():
                session.execute(
                    delete(question_table).where(question_table.c.id == question.id)
                )


def test_published_version_rejects_late_question_insert() -> None:
    version = _version(_question_set(_owner("published-insert")), published=True)

    with pytest.raises(DBAPIError, match="published Visit Prep questions"):
        _question(version)


def test_published_version_content_and_publication_reject_mutation() -> None:
    version = _version(_question_set(_owner("published-version")), published=True)
    version_table = SQLModel.metadata.tables["visit_prep_question_set_version"]

    with get_session() as session:
        with pytest.raises(DBAPIError, match="question-set versions are immutable"):
            with session.begin_nested():
                session.execute(
                    update(version_table)
                    .where(version_table.c.id == version.id)
                    .values(name="Mutated version name")
                )
        with pytest.raises(DBAPIError, match="question-set versions are immutable"):
            with session.begin_nested():
                session.execute(
                    update(version_table)
                    .where(version_table.c.id == version.id)
                    .values(published_at=None)
                )
        with pytest.raises(DBAPIError, match="question-set versions are immutable"):
            with session.begin_nested():
                session.execute(
                    delete(version_table).where(version_table.c.id == version.id)
                )


def test_published_default_selection_remains_mutable_metadata() -> None:
    version = _version(_question_set(_owner("default-metadata")), published=True)

    version.is_default = True
    version.save()
    version.is_default = False
    version.save()
    version.refresh()

    assert version.published_at is not None
    assert version.is_default is False
