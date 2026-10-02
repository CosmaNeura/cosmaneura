"""Adapter projection preserves existing receipts without vendor domain coupling."""

import json
from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from app.configuration import visit_prep_acuity
from app.configuration.visit_prep_acuity import AcuityBookingSettings
from app.services import visit_prep_acuity as adapter
from app.services.visit_prep_booking_contract import BookingSourceUnavailable
from app.services.visit_prep_command_contract import _normalize_recipient
from app.utils.encryption import pseudonymous_digest

from tests.integration.visit_prep_booking_test import _post, _ready


def test_adapter_projection_retains_published_source_digest_and_snapshot(monkeypatch):
    scope, binding, settings, response, _, _ = _ready(monkeypatch)
    booking = adapter.fetch_current_booking(
        appointment_id=54321, binding=binding, settings=settings
    )
    source, projected = adapter.project_current_booking(
        booking=booking, binding=binding, settings=settings
    )
    old_key = pseudonymous_digest(
        json.dumps(
            {
                "source": "acuity",
                "connection": str(settings.connection_id),
                "appointment": 54321,
            },
            sort_keys=True,
        ),
        context="visit-prep-booking-reference-v1",
    )
    old_snapshot = pseudonymous_digest(
        json.dumps(
            {
                "booking": old_key,
                "owner": str(scope.owner.id),
                "practice": str(scope.practice.id),
                "calendar": binding.calendar_id,
                "recipient": asdict(_normalize_recipient(booking.recipient)),
                "scheduled": datetime.fromisoformat(response["datetime"])
                .astimezone(UTC)
                .isoformat(),
                "timezone": binding.timezone_name,
            },
            sort_keys=True,
        ),
        context="visit-prep-booking-snapshot-v1",
    )
    assert projected.booking_digest == old_key
    assert projected.source_snapshot_hmac == old_snapshot
    assert source.owner_doctor_id == scope.doctor.id
    assert not hasattr(projected, "appointment_id")
    assert not hasattr(projected, "calendar_id")
    assert settings.api_key not in repr(source) + repr(projected)
    assert response["email"] not in repr(source) + repr(projected)


def test_projection_and_replay_recheck_current_adapter_generation(monkeypatch):
    _, binding, settings, _, _, client = _ready(monkeypatch)
    booking = adapter.fetch_current_booking(
        appointment_id=54321, binding=binding, settings=settings
    )
    with client:
        assert _post(client, settings).status_code == 200
        assert _post(client, settings).json()["disposition"] == "replayed"
    monkeypatch.setattr(visit_prep_acuity, "SETTINGS", AcuityBookingSettings())
    with pytest.raises(BookingSourceUnavailable):
        adapter.project_current_booking(
            booking=booking, binding=binding, settings=settings
        )
