from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migration_lint_base.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_lint_base", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATIONS = _load_module()


def _migration(path: Path, revision: str, down_revision: object) -> Path:
    path.write_text(
        f"revision: str = {revision!r}\ndown_revision = {down_revision!r}\n",
        encoding="utf-8",
    )
    return path


def test_finds_ancestry_base_independent_of_filename_order(tmp_path: Path) -> None:
    newest_name_first = _migration(tmp_path / "01_newest.py", "third", "second")
    oldest_name_last = _migration(tmp_path / "99_oldest.py", "first", "existing")
    middle = _migration(tmp_path / "50_middle.py", "second", "first")

    assert (
        MIGRATIONS.find_lint_base([newest_name_first, oldest_name_last, middle])
        == "existing"
    )


def test_accepts_annotated_revision_assignments(tmp_path: Path) -> None:
    migration = tmp_path / "migration.py"
    migration.write_text(
        'revision: str = "new"\ndown_revision: str | None = "previous"\n',
        encoding="utf-8",
    )

    assert MIGRATIONS.find_lint_base([migration]) == "previous"


def test_root_revision_uses_alembic_base(tmp_path: Path) -> None:
    migration = _migration(tmp_path / "migration.py", "first", None)

    assert MIGRATIONS.find_lint_base([migration]) == "base"


def test_disconnected_ranges_fail_closed(tmp_path: Path) -> None:
    first = _migration(tmp_path / "first.py", "first", "existing-a")
    second = _migration(tmp_path / "second.py", "second", "existing-b")

    with pytest.raises(ValueError, match="not one connected"):
        MIGRATIONS.find_lint_base([first, second])


def test_disconnected_cycle_fails_closed(tmp_path: Path) -> None:
    root = _migration(tmp_path / "root.py", "root", "existing")
    cycle_a = _migration(tmp_path / "cycle_a.py", "cycle-a", "cycle-b")
    cycle_b = _migration(tmp_path / "cycle_b.py", "cycle-b", "cycle-a")

    with pytest.raises(ValueError, match="disconnected ancestry cycle"):
        MIGRATIONS.find_lint_base([root, cycle_a, cycle_b])


def test_duplicate_or_dynamic_revision_fails_closed(tmp_path: Path) -> None:
    first = _migration(tmp_path / "first.py", "same", "existing")
    duplicate = _migration(tmp_path / "duplicate.py", "same", "existing")
    dynamic = tmp_path / "dynamic.py"
    dynamic.write_text(
        'PREFIX = "new"\nrevision = PREFIX + "-revision"\ndown_revision = "existing"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate"):
        MIGRATIONS.find_lint_base([first, duplicate])
    with pytest.raises(ValueError, match="malformed node or string"):
        MIGRATIONS.find_lint_base([dynamic])

    missing_parent = tmp_path / "missing_parent.py"
    missing_parent.write_text('revision = "new"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="missing one literal down_revision"):
        MIGRATIONS.find_lint_base([missing_parent])


def test_db_lint_uses_ancestry_helper_instead_of_filename_order() -> None:
    recipe = (ROOT / "just" / "database.just").read_text(encoding="utf-8")
    db_lint = recipe[recipe.index("db_lint:") : recipe.index("# open the database")]

    assert "scripts/migration_lint_base.py" in db_lint
    assert 'oldest=$(echo "$new_migrations" | head -1)' not in db_lint
    assert 'alembic upgrade "${prev_rev}:head" --sql' in db_lint
