import app.services.session_prep_gate as gate
from app.services.session_prep_gate import session_prep_enabled_for


def test_dev_and_test_are_ungated():
    assert session_prep_enabled_for("anyone@example.com") is True


def test_missing_email_is_blocked():
    assert session_prep_enabled_for(None) is False
    assert session_prep_enabled_for("") is False


def test_staging_allows_only_pilot_cohort(monkeypatch):
    monkeypatch.setattr(gate, "is_staging", lambda: True)

    assert session_prep_enabled_for("john@cosmaneura.com") is True
    assert session_prep_enabled_for("JOHN@cosmaneura.com") is True
    assert session_prep_enabled_for("greg@mycatholicdoctor.com") is True
    assert session_prep_enabled_for("stranger@example.com") is False


def test_production_is_locked_until_pilot_validates(monkeypatch):
    monkeypatch.setattr(gate, "is_production", lambda: True)

    assert session_prep_enabled_for("john@cosmaneura.com") is False
