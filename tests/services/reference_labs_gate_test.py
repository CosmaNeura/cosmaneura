import app.services.reference_labs_gate as labs
import app.services.reference_toolkit_gate as toolkit


def test_lab_latch_does_not_disable_other_reference_tools(monkeypatch):
    monkeypatch.setattr(labs, "REFERENCE_LABS_ENABLED", False)
    monkeypatch.setattr(toolkit, "is_staging", lambda: True)
    assert toolkit.reference_toolkit_enabled_for("john@cosmaneura.com")
    assert not labs.reference_labs_enabled_for("john@cosmaneura.com")


def test_lab_activation_does_not_expand_existing_cohort(monkeypatch):
    monkeypatch.setattr(labs, "REFERENCE_LABS_ENABLED", True)
    monkeypatch.setattr(toolkit, "is_staging", lambda: True)
    assert labs.reference_labs_enabled_for("JOHN@cosmaneura.com")
    assert not labs.reference_labs_enabled_for("unlisted@example.com")
    assert not labs.reference_labs_enabled_for(None)
    monkeypatch.setattr(toolkit, "is_staging", lambda: False)
    monkeypatch.setattr(toolkit, "is_production", lambda: True)
    assert not labs.reference_labs_enabled_for("john@cosmaneura.com")
