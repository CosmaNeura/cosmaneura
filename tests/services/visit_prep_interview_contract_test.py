"""Pure patient-input rules using invented data, without public routes."""

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from uuid import uuid4

import pytest
from typeid import TypeID

from app.configuration.visit_prep import load_visit_prep_settings
from app.services.visit_prep_interview_contract import (
    MAX_ANSWER_CHARACTERS,
    ResponseInput,
    VisitPrepInterviewValidation,
    _answer_envelope,
    _calendar_years_after,
    _canonical_idempotency_key,
    _CanonicalResponse,
    _normalized_answer,
    _normalized_confirmation_name,
    _response_payload_hmac,
    _validated_response_input,
)


def test_public_gate_is_independent_and_does_not_enable_delivery():
    settings = load_visit_prep_settings({"VISIT_PREP_PUBLIC_API_ENABLED": "true"})
    assert settings.public_api_enabled
    assert not settings.enabled
    assert not settings.email_send_enabled and not settings.sms_send_enabled
    assert not settings.enabled_doctor_ids and not settings.enabled_practice_ids


def test_confirmation_and_answer_normalize_unicode_without_inventing_content():
    assert _normalized_confirmation_name(" JOSE\u0301 ") == "josé"
    assert _normalized_answer("  First\r\nSecond\rThird e\u0301  ") == (
        "First\nSecond\nThird é"
    )
    assert _normalized_answer("x" * MAX_ANSWER_CHARACTERS) == (
        "x" * MAX_ANSWER_CHARACTERS
    )


@pytest.mark.parametrize(
    "value", [None, True, 123, "", " \n ", "A\x00", "A\tB", "A\u200b"]
)
def test_answer_rejects_empty_controls_and_nonstrings(value):
    with pytest.raises(VisitPrepInterviewValidation, match="answer is invalid"):
        _normalized_answer(value)


def test_answer_limit_rejects_one_character_over():
    with pytest.raises(VisitPrepInterviewValidation):
        _normalized_answer("x" * (MAX_ANSWER_CHARACTERS + 1))


@pytest.mark.parametrize("value", [None, 3, "", "A\nB", "A\u200b", "x" * 121])
def test_confirmation_name_is_bounded(value):
    with pytest.raises(VisitPrepInterviewValidation):
        _normalized_confirmation_name(value)


def test_idempotency_uuid_must_be_canonical():
    key = str(uuid4())
    assert _canonical_idempotency_key(key) == key
    for invalid in [None, True, "", key.upper(), "{" + key + "}", " " + key]:
        with pytest.raises(VisitPrepInterviewValidation):
            _canonical_idempotency_key(invalid)


def test_skip_does_not_accept_an_answer_and_inputs_are_private_in_repr():
    answer = ResponseInput("story", "answered", "Synthetic patient story")
    assert "Synthetic patient story" not in repr(answer)
    assert _validated_response_input(answer) is answer
    assert _validated_response_input(ResponseInput("story", "skipped")).answer is None
    invalid = [
        None,
        ResponseInput("Story", "skipped"),
        ResponseInput("story", "skipped", "Hidden answer"),
        ResponseInput("story", "answered"),
        ResponseInput("story", cast(Any, "unknown")),
    ]
    for value in invalid:
        with pytest.raises(VisitPrepInterviewValidation):
            _validated_response_input(cast(Any, value))


def test_answer_envelope_and_fingerprint_preserve_kind_and_prep_lineage():
    text = "Synthetic story — no instructions to execute"
    assert json.loads(_answer_envelope(kind="story", value=text)) == {
        "kind": "story",
        "value": text,
        "version": 1,
    }
    first, second = TypeID("visit_prep"), TypeID("visit_prep")
    response = _CanonicalResponse("story", "answered", text)
    digest = _response_payload_hmac(response, visit_prep_id=cast(Any, first))
    assert len(digest) == 64 and text not in digest
    assert digest == _response_payload_hmac(response, visit_prep_id=cast(Any, first))
    assert digest != _response_payload_hmac(response, visit_prep_id=cast(Any, second))
    assert digest != _response_payload_hmac(
        _CanonicalResponse("reason", "answered", text), visit_prep_id=cast(Any, first)
    )


def test_calendar_retention_is_utc_anchored_and_handles_leap_day():
    local = datetime(2024, 2, 29, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert _calendar_years_after(local, 7) == datetime(2031, 3, 1, 4, 30, tzinfo=UTC)
    assert _calendar_years_after(datetime(2024, 2, 29, tzinfo=UTC), 7) == (
        datetime(2031, 2, 28, tzinfo=UTC)
    )
