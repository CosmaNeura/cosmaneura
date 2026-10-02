import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.configuration.billing_header import parse_policy
from app.configuration.clerk import verified_provider_session
from app.services.billing_header_contract import (
    PrepareBillingHeaderOperation,
    project_note,
)

TIME = "Greater than 19 minutes was spent in patient care, including review of prior records, consultation with other providers, and documentation."
VISIT = {
    "patientAge": 31,
    "patientSex": "F",
    "encounterType": "office_established",
    "placeOfService": "11",
    "providerSpecialty": "Family medicine",
}


def command(**changes):
    return dict(
        operationId=str(uuid4()),
        expectedContentSha256="a" * 64,
        expectedTextSha256="b" * 64,
        confirmedComplete=True,
        visit=VISIT,
        **changes,
    )


def test_ordered_source_is_immutable_and_only_exact_recording_paragraph_is_omitted():
    source = {
        "Assessment": "Fictional current assessment.",
        "Plan": "Follow up.\n\n" + TIME,
    }
    original = json.dumps(source)
    encoded, text, digest, excluded = project_note(source)
    assert json.dumps(source) == original
    assert json.loads(encoded) == list(map(list, source.items()))
    assert digest == hashlib.sha256(encoded.encode()).hexdigest()
    assert (
        text
        == "## Assessment\nFictional current assessment.\n\n## Plan\nFollow up.\n\n"
    )
    assert excluded and TIME not in text
    assert project_note(dict(reversed(list(source.items()))))[2] != digest
    assert project_note({"Plan": "Clinician documented 24 minutes total time."})[
        1
    ].endswith("24 minutes total time.")


@pytest.mark.parametrize(
    "body",
    [
        TIME + " Additional fact.",
        "Quoted: " + TIME,
        "Context\n" + TIME,
        TIME.replace("19", "nineteen"),
        TIME.replace("was spent", "were spent"),
    ],
)
def test_near_matches_and_inline_recording_text_are_not_rewritten(body):
    assert project_note({"Plan": body})[1] == "## Plan\n" + body
    assert project_note({"Plan": body})[3] is False


def test_crlf_and_utf8_preserved_no_bare_cr_or_controls():
    body = "Fictional café.\r\n\r\n" + TIME
    assert project_note({"Plan": body})[1] == "## Plan\nFictional café.\r\n\r\n"
    for invalid in ["a\rb", "a\x00b", "a\ud800b"]:
        with pytest.raises(ValueError):
            project_note({"Plan": invalid})


def test_context_is_explicit_without_birthdate_dos_or_time_defaults():
    parsed = PrepareBillingHeaderOperation.model_validate(command())
    assert "serviceDate" not in parsed.visit.model_dump(exclude_unset=True)
    assert parsed.visit.patientAge == 31
    for visit in [
        {},
        VISIT | {"serviceDate": None},
        VISIT | {"serviceDate": "2026-02-30"},
        VISIT | {"patientAge": True},
        VISIT | {"placeOfService": "02"},
        VISIT | {"providerSpecialty": " "},
        VISIT | {"totalTime": 19},
    ]:
        with pytest.raises(ValidationError):
            PrepareBillingHeaderOperation.model_validate(command() | {"visit": visit})
    for change in [
        {"confirmedComplete": 1},
        {"confirmedComplete": False},
        {"note": "browser text"},
        {"practiceId": "practice_fake"},
        {"operationId": str(uuid4()).upper()},
    ]:
        with pytest.raises(ValidationError):
            PrepareBillingHeaderOperation.model_validate(command() | change)


def test_empty_policy_and_closed_issuer_scope():
    assert not parse_policy("").enabled
    for raw in [
        '{"enabled":true}',
        '{"enabled":false,"enabled":false}',
        '{"admissions":[{"dataClass":"operational"}]}',
        '{"approved":true}',
    ]:
        with pytest.raises(ValueError):
            parse_policy(raw)


def test_only_already_verified_sdk_session_is_normalized():
    payload = {
        "iss": "https://clerk.test.invalid",
        "sub": "user_synthetic",
        "sid": "sess_synthetic",
    }
    state = SimpleNamespace(is_signed_in=True, payload=payload)
    actor = verified_provider_session(state, expected_issuer=payload["iss"])
    assert (
        actor.subject == payload["sub"]
        and actor.session_sha256 == hashlib.sha256(payload["sid"].encode()).hexdigest()
    )
    for invalid in [
        SimpleNamespace(is_signed_in=False, payload=payload),
        SimpleNamespace(is_signed_in=True, payload=payload | {"sid": None}),
    ]:
        with pytest.raises(ValueError):
            verified_provider_session(invalid)
    with pytest.raises(ValueError):
        verified_provider_session(state, expected_issuer="https://different.invalid")
