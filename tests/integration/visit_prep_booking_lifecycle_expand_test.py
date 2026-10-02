"""Owned populated expansion preserves booking and patient evidence unchanged."""

import os

import pytest
from alembic import command
from alembic.config import Config

from activemodel.session_manager import get_engine
from sqlalchemy import text
from sqlmodel import Session

from tests.integration.visit_prep_booking_test import _post, _ready


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "c3aa7a31f3ae",
    reason="Owned populated predecessor rehearsal only",
)
def test_populated_lifecycle_expand_retains_original_booking_and_preparation(
    monkeypatch,
):
    _, _, settings, _, _, client = _ready(monkeypatch)
    with client:
        assert _post(client, settings).status_code == 200
    tables = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
        "visit_prep_booking_receipt",
    )

    def snapshot():
        with Session(get_engine()) as database:
            return {
                table: database.execute(
                    text(f"SELECT to_jsonb(t)::text FROM public.{table} t ORDER BY id")
                )
                .scalars()
                .all()
                for table in tables
            }

    before = snapshot()
    assert all(before.values())
    command.upgrade(Config("alembic.ini"), "ac4d76856637")
    assert snapshot() == before
    with Session(get_engine()) as database:
        assert (
            database.execute(
                text("SELECT count(*) FROM visit_prep_booking_event")
            ).scalar_one()
            == 0
        )
