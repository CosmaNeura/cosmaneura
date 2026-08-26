"""Static guardrails that keep Visit Prep separate from Session Prep."""

from __future__ import annotations

import ast
from pathlib import Path

ALLOWED_APP_IMPORTS = {
    Path("app/routes/visit_prep_interviews.py"): {
        "app",
        "app.services",
        "app.services.visit_prep_interviews",
        "app.services.visit_prep_rate_limits",
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
    Path("app/services/visit_prep_rate_limits.py"): {
        "app.configuration.redis",
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
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                imported.add("." * node.level + (node.module or ""))
                continue
            if node.module is None:
                continue
            imported.add(node.module)
            module_path = Path(*node.module.split("."))
            for alias in node.names:
                candidate = module_path / alias.name
                if candidate.with_suffix(".py").is_file() or candidate.is_dir():
                    imported.add(f"{node.module}.{alias.name}")
    return imported


def test_import_scanner_expands_package_submodule_imports(tmp_path: Path) -> None:
    source = tmp_path / "synthetic_boundary.py"
    source.write_text(
        "from app.services import sovereign_record_grant\n",
        encoding="utf-8",
    )

    assert "app.services.sovereign_record_grant" in _imports(source)


def test_public_interview_has_no_session_prep_or_external_context_dependency() -> None:
    for path, allowed_app_imports in ALLOWED_APP_IMPORTS.items():
        imports = _imports(path)
        assert not {imported for imported in imports if imported.startswith(".")}
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
