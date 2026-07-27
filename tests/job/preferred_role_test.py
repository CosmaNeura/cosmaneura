"""
First-party preferred communication role: scored at intake completion from the
patient's own answers (engagement engine v2, preferred_only view), using the
offline heuristic client in tests.
"""

import app.jobs.intake.summarize as summarize

from app.models.intake.intake import IntakeSchemaQuestion

from tests.factories import SimpleIncompleteIntakeFactory

ROLE_KEYS = {"self_directed", "collaborative", "directed"}


def _questions(answers: list[str]) -> list[IntakeSchemaQuestion]:
    return [
        IntakeSchemaQuestion(
            order=index,
            question=f"Question {index}?",
            required_details="",
            answer=answer,
            answer_timestamp=1_000_000 + index,
        )
        for index, answer in enumerate(answers)
    ]


def test_preferred_role_scored_from_intake_answers():
    intake = SimpleIncompleteIntakeFactory.build(
        intake_questions=_questions(
            [
                "I have been getting severe headaches for three weeks and I have "
                "read a lot about possible causes. I want to understand all my "
                "options and decide together what we try next.",
                "I tried ibuprofen and cutting back on caffeine, and I have been "
                "tracking my sleep because I want to review the data with you.",
            ]
        )
    ).save()

    summarize.perform(intake.id)

    intake.refresh()
    assert intake.summary

    preferred = intake.preferred_communication_role
    assert preferred is not None
    assert preferred.role_key in ROLE_KEYS
    assert preferred.role_name
    assert preferred.cps_role in {"active", "collaborative", "passive"}
    assert 0 <= preferred.confidence <= 1
    assert preferred.provider_guidance

    # provenance: first-party, offline model in tests
    assert preferred.source == "intake_answers"
    assert preferred.model
    assert preferred.engine_version


def test_too_little_text_is_not_scored():
    intake = SimpleIncompleteIntakeFactory.build(
        intake_questions=_questions(["No", "Yes"])
    ).save()

    summarize.perform(intake.id)

    intake.refresh()
    assert intake.summary
    assert intake.preferred_communication_role is None
