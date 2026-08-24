from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    ROOT / "app/jobs/sync_clerk.py",
    ROOT / "app/jobs/extract.py",
    ROOT / "app/jobs/anonymize.py",
    ROOT / "app/routes/zoom_auth.py",
    ROOT / "app/jobs/sync_zoom_meetings.py",
    ROOT / "app/routes/dependencies/doctor.py",
    ROOT / "app/routes/dependencies/view_as.py",
)
CONTEXT_TARGETS = (
    ROOT / "app/routes/api.py",
    ROOT / "app/routes/dependencies/doctor.py",
    ROOT / "app/routes/dependencies/view_as.py",
)

SAFE_LOG_FIELDS = {
    "count",
    "conflicting_doctor_ref",
    "admin_ref",
    "actor_ref",
    "clerk_ref",
    "doctor_ref",
    "email_ref",
    "entry_count",
    "event_code",
    "example_ref",
    "fetched",
    "field_count",
    "note_ref",
    "processed",
    "schema_ref",
    "skipped_collision",
    "skipped_missing_email",
    "skipped_missing_primary",
    "skipped_missing_user",
    "skipped_no_start_time",
    "synced",
    "transcript_ref",
    "target_ref",
    "view_as_actor_ref",
    "zoom_user_ref",
}


def _log_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "log"
    ]


def test_clinical_workflow_logs_have_a_fixed_metadata_contract() -> None:
    """Prevent identifiers, clinical content, and provider errors entering logs."""
    for path in TARGETS:
        calls = _log_calls(path)
        assert calls, path
        for call in calls:
            assert all(isinstance(argument, ast.Constant) for argument in call.args)
            fields = {keyword.arg for keyword in call.keywords}
            assert fields <= SAFE_LOG_FIELDS, (path, fields - SAFE_LOG_FIELDS)

            event_code = next(
                (
                    keyword.value
                    for keyword in call.keywords
                    if keyword.arg == "event_code"
                ),
                None,
            )
            assert isinstance(event_code, ast.Constant), (path, ast.dump(call))


def test_clinical_workflow_logs_never_serialize_sensitive_objects() -> None:
    forbidden_fragments = (
        "response.text",
        "result.output_text",
        "str(e)",
        "str(error)",
        "log.exception",
    )
    for path in TARGETS:
        for call in _log_calls(path):
            rendered = ast.unparse(call)
            assert not any(fragment in rendered for fragment in forbidden_fragments), (
                path,
                rendered,
            )


def test_request_context_never_stores_identity_models_or_raw_ids() -> None:
    forbidden_assignments = {
        "admin_user",
        "api_user",
        "doctor",
        "admin_id",
        "api_user_id",
        "doctor_id",
    }
    for path in CONTEXT_TARGETS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "context"
                    and isinstance(target.slice, ast.Constant)
                ):
                    continue
                assert target.slice.value not in forbidden_assignments, (
                    path,
                    target.slice.value,
                )
