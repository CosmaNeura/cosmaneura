"""Real independent-connection races through patient correction/review services."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import uuid4

from app.services.visit_prep_contribution_contract import ContributionMutationReceipt
from app.services.visit_prep_contributions import (
    correct_contribution,
    review_contribution,
)
from app.services.visit_prep_interview_contract import (
    ResponseInput,
    VisitPrepInterviewConflict,
)

from tests.services.visit_prep_contribution_commands_test import _events
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
)


def test_two_divergent_corrections_cannot_both_commit_same_basis(monkeypatch):
    fixture = _prepared(monkeypatch, "command-race-divergent")
    basis = _projection(fixture).basis
    barrier = Barrier(2)

    def run(answer):
        barrier.wait(timeout=5)
        return correct_contribution(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            expected_basis=basis,
            idempotency_key=str(uuid4()),
            response=ResponseInput("story", "answered", answer),
        )

    successes, failures = [], []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(run, answer)
            for answer in ("Invented first revision.", "Invented second revision.")
        ]
        for future in futures:
            try:
                successes.append(future.result(timeout=15))
            except VisitPrepInterviewConflict as error:
                failures.append(error)
    assert len(successes) == len(failures) == 1
    assert len(_events(fixture)) == 1
    assert _projection(fixture).basis.event_sequence == 1


def test_same_correction_uuid_retries_once_under_real_concurrency(monkeypatch):
    fixture = _prepared(monkeypatch, "command-race-replay")
    basis, key = _projection(fixture).basis, str(uuid4())
    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=5)
        return correct_contribution(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            expected_basis=basis,
            idempotency_key=key,
            response=ResponseInput(
                "story", "answered", "An invented replayed revision."
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run) for _ in range(2)]
        receipts = [future.result(timeout=15) for future in futures]
    assert {receipt.disposition for receipt in receipts} == {"accepted", "replayed"}
    assert len(_events(fixture)) == 1


def test_review_and_correction_of_same_displayed_basis_have_one_winner(monkeypatch):
    fixture = _prepared(monkeypatch, "command-race-review")
    basis = _projection(fixture).basis
    barrier = Barrier(2)

    def run(review):
        barrier.wait(timeout=5)
        args: dict[str, Any] = dict(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            expected_basis=basis,
            idempotency_key=str(uuid4()),
        )
        if review:
            return review_contribution(**args)
        return correct_contribution(**args, response=ResponseInput("story", "skipped"))

    successes: list[ContributionMutationReceipt] = []
    failures = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, review) for review in (False, True)]
        for future in futures:
            try:
                successes.append(future.result(timeout=15))
            except VisitPrepInterviewConflict as error:
                failures.append(error)
    assert len(successes) == len(failures) == 1
    assert len(_events(fixture)) == 1
    current = _projection(fixture)
    assert current.current_answers_reviewed == (
        _events(fixture)[0].event_kind == "review"
    )
