"""Physical expand constraints with synthetic records in the actual test DB."""

import json

import pytest

from activemodel.session_manager import get_session
from app.models.authority_conversation import CanonicalConversationFinalPhase as Final
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from tests.services.provider_question_context_test import _chat, _client, _final
from tests.services.provider_question_context_test import scope as _scope_fixture

scope = _scope_fixture
# Exact PostgreSQL identifier observed in the actual migrated v3 DB regression.
EXPECTED_CITATION_CONSTRAINT = (
    "canonical_conversation_final_phase_ck_conversation_revi_3e4b"
)


@pytest.mark.parametrize(
    "change",
    [
        "null",
        "json_null",
        "valid",
        "refused",
        "suppressed",
        "mode",
        "version",
        "empty",
        "oversize",
    ],
)
def test_actual_final_table_expand_constraints_preserve_legacy(scope, change):
    client, turn = _client(), _chat(scope[3])
    assert _final(client, scope[3], turn).status_code == 201
    with get_session() as session:
        assert (
            session.exec(
                select(col(Final.reviewed_excerpt_bindings).is_(None)).where(
                    Final.turn_id == turn[2]
                )
            ).one()
            is True
        )
        session.execute(
            text(
                "CREATE TEMP TABLE reviewed_excerpt_expand_probe (LIKE canonical_conversation_final_phase INCLUDING CONSTRAINTS INCLUDING DEFAULTS) ON COMMIT DROP"
            )
        )
        session.execute(
            text(
                "INSERT INTO reviewed_excerpt_expand_probe SELECT * FROM canonical_conversation_final_phase"
            )
        )
        assert (
            session.execute(
                text(
                    "SELECT count(*) FROM reviewed_excerpt_expand_probe WHERE reviewed_excerpt_bindings IS NOT NULL"
                )
            ).scalar_one()
            == 0
        )
        value = {"schema_version": "1.0.0", "excerpts": [{}]}
        outcome, mode, suppressed = "completed", "standard", False
        if change == "refused":
            outcome = "refused"
        elif change == "suppressed":
            suppressed = True
        elif change == "mode":
            mode = "emergency"
        elif change == "version":
            value["schema_version"] = "2.0.0"
        elif change == "empty":
            value["excerpts"] = []
        elif change == "oversize":
            value["padding"] = "x" * 65536
        parameters = {
            "value": None
            if change == "null"
            else json.dumps(None if change == "json_null" else value),
            "outcome": outcome,
            "mode": mode,
            "suppressed": suppressed,
        }
        statement = text(
            "UPDATE reviewed_excerpt_expand_probe SET reviewed_excerpt_bindings=CAST(:value AS jsonb), outcome=:outcome, presentation_mode=:mode, suppress_citations=:suppressed"
        )
        if change in {"null", "valid"}:
            session.execute(statement, parameters)
        else:
            with pytest.raises(IntegrityError) as rejected, session.begin_nested():
                session.execute(statement, parameters)
            assert (
                getattr(
                    getattr(rejected.value.orig, "diag", None), "constraint_name", None
                )
                == EXPECTED_CITATION_CONSTRAINT
            )
        session.rollback()
