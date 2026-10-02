"""The combined client contract must retain both Visit Prep and current Core."""

import json
from pathlib import Path

from app.routes.internal import internal_api_app
from app.utils.openapi import generate_openapi_schema


def test_checked_in_combined_internal_schema_matches_actual_application():
    root = Path(__file__).resolve().parents[2]
    snapshot = json.loads((root / "web/openapi.json").read_text())
    actual = generate_openapi_schema(internal_api_app)

    assert snapshot == actual
    assert "/internal/v1/visit-prep/preps" in snapshot["paths"]
    assert "/internal/v1/visit-prep/preps/{prep_id}/package" in snapshot["paths"]
    assert any("billing" in path for path in snapshot["paths"])
