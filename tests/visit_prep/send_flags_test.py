"""Visit Prep send flags stay off/false and unreachable in every environment."""
from __future__ import annotations
from datetime import UTC, datetime, timedelta
import pytest
from typeid import TypeID
from app.configuration import visit_prep as visit_prep_config
from app.configuration.visit_prep import VisitPrepSettings, load_visit_prep_settings
from app.services.visit_prep_access import resolve_visit_prep_access
from app.services.visit_prep_journey import RecipientInput, VisitPrepUnavailable, create_visit_prep
from tests.services.visit_prep_access_test import _doctor, _practice
def test_defaults_keep_send_flags_false_in_every_environment():
    s = load_visit_prep_settings({})
    assert (s.email_send_enabled, s.sms_send_enabled, s.public_api_enabled, s.enabled) == (False, False, False, False)
    assert s.require_synthetic_recipients is True
@pytest.mark.parametrize("values", [
    {},
    {"FF_VISIT_PREP_202608": "true"},
    {"FF_VISIT_PREP_202608": "true", "VISIT_PREP_PUBLIC_API_ENABLED": "true",
     "VISIT_PREP_EMAIL_SEND_ENABLED": "false", "VISIT_PREP_SMS_SEND_ENABLED": "false"},
    {"FF_VISIT_PREP_202608": "true", "VISIT_PREP_EMAIL_SEND_ENABLED": "0", "VISIT_PREP_SMS_SEND_ENABLED": "off"},
])
def test_loaded_settings_never_enable_send_unless_explicit(values):
    s = load_visit_prep_settings(values)
    if s.configuration_valid:
        assert s.email_send_enabled is False and s.sms_send_enabled is False
def test_access_decision_keeps_send_capabilities_false_when_eligible():
    doctor = _doctor("send-flags"); _practice(doctor)
    d = resolve_visit_prep_access(
        doctor,
        settings=VisitPrepSettings(
            enabled=True, enabled_doctor_ids=frozenset({str(doctor.id)}),
            email_send_enabled=False, sms_send_enabled=False,
        ),
    )
    assert d.eligible is True and d.can_send_email is False and d.can_send_sms is False
def test_create_refuses_when_send_flags_are_true(monkeypatch):
    doctor = _doctor("send-block")
    practice, member = _practice(doctor)
    settings = VisitPrepSettings(
        enabled=True, public_api_enabled=True, enabled_doctor_ids=frozenset({str(doctor.id)}),
        email_send_enabled=True, sms_send_enabled=False,
    )
    monkeypatch.setattr(visit_prep_config, "VISIT_PREP_SETTINGS", settings)
    with pytest.raises(VisitPrepUnavailable, match="send channels stay unreachable"):
        create_visit_prep(
            owner_practice_member_id=member.id, owner_doctor_id=doctor.id, practice_id=practice.id,
            idempotency_key="00000000-0000-4000-8000-000000000001",
            recipient=RecipientInput(first_name="Synthetic", email="patient@example.test"),
            physician_display_name="Dr Synthetic",
            scheduled_at=datetime.now(UTC) + timedelta(days=2), timezone_name="UTC",
            question_set_version_id=TypeID("visit_prep_question_set_version"), settings=settings,
        )
