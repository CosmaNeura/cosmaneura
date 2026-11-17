from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import app.services.openfda as openfda
from app.services.openfda import fetch_label_sections


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err",
                request=httpx.Request("GET", "http://test"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


def _label(**fields):
    return {"results": [fields]}


def test_parses_and_labels_sections(monkeypatch):
    payload = _label(
        adverse_reactions=["Nausea, diarrhea, and vitamin B12 deficiency."],
        drug_interactions=["May interact with cimetidine."],
        boxed_warning=["Lactic acidosis risk."],
    )
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))

    sections = fetch_label_sections("metformin")
    assert sections is not None
    labels = {s["label"] for s in sections}
    assert "Adverse reactions" in labels
    assert "Drug interactions" in labels
    assert "Boxed warning" in labels
    adverse = next(s for s in sections if s["label"] == "Adverse reactions")
    assert "B12" in adverse["text"]


def test_deduplicates_warnings(monkeypatch):
    payload = _label(
        warnings_and_cautions=["Use caution."],
        warnings=["Use caution."],
    )
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))

    sections = fetch_label_sections("somedrug")
    assert sections is not None
    assert len([s for s in sections if s["label"].startswith("Warnings")]) == 1


def test_truncates_long_sections(monkeypatch):
    payload = _label(adverse_reactions=["x" * 10_000])
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))

    sections = fetch_label_sections("bigdrug")
    assert sections is not None
    assert len(sections[0]["text"]) == openfda._MAX_SECTION_CHARS


def test_flags_truncation_and_links_to_the_full_label(monkeypatch):
    "partial safety text must never look complete to a clinician"
    payload = _label(adverse_reactions=["x" * 10_000])
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))

    sections = fetch_label_sections("bigdrug")
    assert sections is not None
    assert sections[0]["truncated"] is True
    assert sections[0]["source_url"] == (
        "https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=all&query=bigdrug"
    )


@pytest.mark.parametrize(
    "name",
    ["drug & labeltype=evil", "https://attacker.invalid/#label", "drug?x=1#section"],
)
def test_full_label_link_keeps_drug_names_in_the_query(name: str) -> None:
    url = urlsplit(openfda.dailymed_search_url(name))

    assert url.scheme == "https"
    assert url.netloc == "dailymed.nlm.nih.gov"
    assert url.path == "/dailymed/search.cfm"
    assert not url.fragment
    assert parse_qs(url.query) == {"labeltype": ["all"], "query": [name]}


def test_does_not_flag_truncation_when_the_section_fits(monkeypatch):
    payload = _label(adverse_reactions=["Nausea."])
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))

    sections = fetch_label_sections("smalldrug")
    assert sections is not None
    assert sections[0]["truncated"] is False


def test_keeps_every_block_of_a_multi_block_section(monkeypatch):
    "openFDA splits sections into arrays; dropping all but the first loses safety text"
    payload = _label(
        adverse_reactions=["First reported reaction.", "Second reported reaction."]
    )
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, payload))

    sections = fetch_label_sections("multidrug")
    assert sections is not None
    assert "First reported reaction." in sections[0]["text"]
    assert "Second reported reaction." in sections[0]["text"]


def test_no_results_returns_none(monkeypatch):
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse(200, {"results": []})
    )
    assert fetch_label_sections("nope") is None


def test_404_returns_none(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(404))
    assert fetch_label_sections("missing") is None


def test_network_error_degrades_to_none(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "get", boom)
    assert fetch_label_sections("metformin") is None


def test_empty_name_returns_none():
    assert fetch_label_sections("  ") is None
