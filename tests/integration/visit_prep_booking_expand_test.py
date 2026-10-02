"""Booking receipt expansion preserves a populated preparation and its lineage."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config

from activemodel.session_manager import get_session
from sqlalchemy import text

from tests.services.visit_preps_test import _create, _scope


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "208271feaef7",
    reason="Owned populated predecessor rehearsal only",
)
def test_populated_booking_expand_retains_preparations_and_credentials():
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("booking-expand", now=now)
    result = _create(scope, now=now, scheduled_at=now + timedelta(days=7))
    tables = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
    )

    def snapshot():
        with get_session() as session:
            return {
                table: session.execute(
                    text(f"SELECT to_jsonb(t)::text FROM public.{table} t ORDER BY id")
                )
                .scalars()
                .all()
                for table in tables
            }

    before = snapshot()
    command.upgrade(Config("alembic.ini"), "b3f91f1acb40")
    assert snapshot() == before and all(before.values())
    with get_session() as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM visit_prep_booking_receipt")
            ).scalar_one()
            == 0
        )
    assert result.access_token is not None
