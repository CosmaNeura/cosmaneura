from datetime import datetime, timedelta, timezone

import app.jobs.fetch_drug_reference as job
from app.jobs.fetch_drug_reference import perform

from app.models.drug_reference import DrugReference


def test_caches_found_sections(monkeypatch):
    monkeypatch.setattr(
        job,
        "fetch_label_sections",
        lambda name: [
            {
                "key": "adverse_reactions",
                "label": "Adverse reactions",
                "text": "Nausea.",
            }
        ],
    )

    perform("Metformin")

    row = DrugReference.get(normalized_name="metformin")
    assert row is not None
    assert row.found is True
    assert row.sections[0]["text"] == "Nausea."
    assert row.fetched_at is not None


def test_caches_negative_result(monkeypatch):
    monkeypatch.setattr(job, "fetch_label_sections", lambda name: None)

    perform("obscuredrug")

    row = DrugReference.get(normalized_name="obscuredrug")
    assert row is not None
    assert row.found is False
    assert row.sections == []


def test_skips_when_fresh(monkeypatch):
    DrugReference(
        normalized_name="aspirin",
        sections=[{"key": "warnings", "label": "Warnings", "text": "old"}],
        found=True,
        fetched_at=datetime.now(timezone.utc),
    ).save()

    calls = {"n": 0}

    def counting(name):
        calls["n"] += 1
        return [{"key": "warnings", "label": "Warnings", "text": "new"}]

    monkeypatch.setattr(job, "fetch_label_sections", counting)
    perform("aspirin")

    assert calls["n"] == 0  # fresh cache → no refetch
    assert DrugReference.get(normalized_name="aspirin").sections[0]["text"] == "old"


def test_refetches_when_stale(monkeypatch):
    DrugReference(
        normalized_name="ibuprofen",
        sections=[],
        found=False,
        fetched_at=datetime.now(timezone.utc) - timedelta(days=90),
    ).save()

    monkeypatch.setattr(
        job,
        "fetch_label_sections",
        lambda name: [{"key": "warnings", "label": "Warnings", "text": "fresh"}],
    )
    perform("ibuprofen")

    row = DrugReference.get(normalized_name="ibuprofen")
    assert row.found is True
    assert row.sections[0]["text"] == "fresh"


def test_empty_name_is_noop():
    perform("")
    # nothing created
    assert DrugReference.count() == 0
