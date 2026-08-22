import app.services.reference_toolkit_gate as gate
from app.services.reference_toolkit_gate import reference_toolkit_enabled_for


def test_dev_and_test_are_ungated():
    assert reference_toolkit_enabled_for("anyone@example.com") is True


def test_missing_email_is_blocked():
    assert reference_toolkit_enabled_for(None) is False
    assert reference_toolkit_enabled_for("") is False


def test_staging_allows_only_pilot_cohort(monkeypatch):
    monkeypatch.setattr(gate, "is_staging", lambda: True)

    assert reference_toolkit_enabled_for("john@cosmaneura.com") is True
    assert reference_toolkit_enabled_for("KATHLEEN@mycatholicdoctor.com") is True
    assert reference_toolkit_enabled_for("stranger@example.com") is False


def test_production_is_locked_until_pilot_validates(monkeypatch):
    monkeypatch.setattr(gate, "is_production", lambda: True)

    assert reference_toolkit_enabled_for("john@cosmaneura.com") is False
