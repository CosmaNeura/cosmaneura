import httpx

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
