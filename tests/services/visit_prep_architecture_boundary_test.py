"""Static guardrails that keep Visit Prep separate from Session Prep."""

from __future__ import annotations

import ast
from pathlib import Path

ALLOWED_APP_IMPORTS = {
    Path("app/routes/visit_prep_interviews.py"): {
        "app",
        "app.services",
        "app.services.visit_prep_interviews",
    },
    Path("app/services/visit_prep_interviews.py"): {
        "app.models.doctor",
        "app.models.practice",
        "app.models.visit_prep",
        "app.models.visit_prep_question_set",
        "app.models.visit_prep_response",
        "app.services.visit_prep_access",
        "app.services.visit_preps",
        "app.utils.encryption",
    },
}
BANNED_STORAGE_REFERENCES = (
    "ai_visit_note",
    "intake.summary",
    "intake_summary",
    "patient_context",
    "session_prep",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def test_public_interview_has_no_session_prep_or_external_context_dependency() -> None:
    for path, allowed_app_imports in ALLOWED_APP_IMPORTS.items():
        imports = _imports(path)
        app_imports = {
            imported
            for imported in imports
            if imported == "app" or imported.startswith("app.")
        }
        assert app_imports <= allowed_app_imports

        source = path.read_text(encoding="utf-8").casefold()
        assert not {
            reference for reference in BANNED_STORAGE_REFERENCES if reference in source
        }
