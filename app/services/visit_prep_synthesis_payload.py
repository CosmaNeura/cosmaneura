"""Explicit encrypted artifact codec; not authorization or a model caller.

An authorized caller supplies the current selection at both boundaries. The
envelope preserves selected patient wording and binds it to that exact source
version, attribution and completion/review basis. No transparent ORM decryption.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from app.services.visit_prep_synthesis_contract import (
    VisitPrepPreparedBrief,
    VisitPrepSynthesisInvalid,
    VisitPrepSynthesisSelection,
    validate_generated_brief,
    validate_selection,
)
from app.utils.encryption import (
    DecryptionError,
    EncryptionError,
    decrypt_value,
    encrypt_value,
)

MAX_PAYLOAD_BYTES = 47_000
MAX_CIPHERTEXT_BYTES = 65_536


def _manifest(selection: VisitPrepSynthesisSelection) -> dict:
    validate_selection(selection)
    return {
        "format_version": 1,
        "package_id": selection.package_id,
        "source_version": selection.source_version,
        "question_set_version_id": selection.question_set_version_id,
        "basis": {
            "original_response_count": selection.basis.original_response_count,
            "event_sequence": selection.basis.event_sequence,
        },
        "actor_role": selection.actor_role,
        "original_interview_completed": selection.original_interview_completed,
        "current_answers_complete": selection.current_answers_complete,
        "current_answers_reviewed": selection.current_answers_reviewed,
        "applicable_question_keys": list(selection.applicable_question_keys),
        "missing_question_keys": list(selection.missing_question_keys),
        "skipped_question_keys": list(selection.skipped_question_keys),
        "sources": [
            {
                "question_key": source.question_key,
                "position": source.position,
                "prompt": source.prompt,
                "kind": source.kind,
                "source_ref": source.source_ref,
                "recorded_at": source.recorded_at.isoformat(),
                "answer": source.answer,
                "choice_label": source.choice_label,
            }
            for source in selection.sources
        ],
    }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def encrypt_synthesis_payload(
    prepared: VisitPrepPreparedBrief, selection: VisitPrepSynthesisSelection
) -> str:
    validate_generated_brief(prepared, selection)
    payload = _json({"manifest": _manifest(selection), "brief": prepared.model_dump()})
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise VisitPrepSynthesisInvalid()
    try:
        encrypted = encrypt_value(payload)
    except EncryptionError:
        raise VisitPrepSynthesisInvalid() from None
    if not 1 <= len(encrypted.encode("utf-8")) <= MAX_CIPHERTEXT_BYTES:
        raise VisitPrepSynthesisInvalid()
    return encrypted


def decrypt_synthesis_payload(
    ciphertext: str, selection: VisitPrepSynthesisSelection
) -> VisitPrepPreparedBrief:
    expected_manifest = _manifest(selection)
    if (
        not isinstance(ciphertext, str)
        or not 1 <= len(ciphertext.encode("utf-8")) <= MAX_CIPHERTEXT_BYTES
    ):
        raise VisitPrepSynthesisInvalid()
    try:
        plaintext = decrypt_value(ciphertext)
        if len(plaintext.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise VisitPrepSynthesisInvalid()
        payload = json.loads(plaintext)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"manifest", "brief"}
            or _json(payload["manifest"]) != _json(expected_manifest)
        ):
            raise VisitPrepSynthesisInvalid()
        prepared = VisitPrepPreparedBrief.model_validate_json(_json(payload["brief"]))
        validate_generated_brief(prepared, selection)
        return prepared
    except (DecryptionError, ValueError, TypeError, RecursionError, ValidationError):
        raise VisitPrepSynthesisInvalid() from None
