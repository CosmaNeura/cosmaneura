#!/usr/bin/env python3
"""Find the external parent of one connected set of new Alembic revisions."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any


def _assignments(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, Any] = {}
    for node in tree.body:
        name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                name = target.id
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value = node.value
        if name in {"revision", "down_revision"} and value is not None:
            values[name] = ast.literal_eval(value)
    return values


def _parents(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)) and all(
        isinstance(item, str) for item in value
    ):
        return tuple(value)
    raise ValueError("unsupported down_revision shape")


def find_lint_base(paths: list[Path]) -> str:
    if not paths:
        raise ValueError("no new migration files")

    revisions: dict[str, tuple[str, ...]] = {}
    for path in paths:
        values = _assignments(path)
        revision = values.get("revision")
        if not isinstance(revision, str) or not revision:
            raise ValueError("migration is missing one literal revision")
        if "down_revision" not in values:
            raise ValueError("migration is missing one literal down_revision")
        if revision in revisions:
            raise ValueError("duplicate migration revision")
        revisions[revision] = _parents(values.get("down_revision"))

    revision_ids = set(revisions)
    root_ids = [
        revision
        for revision, parents in revisions.items()
        if not any(parent in revision_ids for parent in parents)
    ]
    if len(root_ids) != 1:
        raise ValueError("new migrations are not one connected ancestry range")

    reachable = set(root_ids)
    while True:
        discovered = {
            revision
            for revision, parents in revisions.items()
            if any(parent in reachable for parent in parents)
        }
        if discovered <= reachable:
            break
        reachable.update(discovered)
    if reachable != revision_ids:
        raise ValueError("new migrations contain a disconnected ancestry cycle")

    external_parents = [
        parent for parent in revisions[root_ids[0]] if parent not in revision_ids
    ]
    if not external_parents:
        return "base"
    if len(external_parents) != 1:
        raise ValueError("new migration range has multiple external parents")
    return external_parents[0]


def main() -> int:
    paths = [Path(line) for line in sys.stdin.read().splitlines() if line]
    try:
        lint_base = find_lint_base(paths)
    except (OSError, SyntaxError, ValueError) as exc:
        print(f"Could not determine new migration ancestry: {exc}", file=sys.stderr)
        return 1
    print(lint_base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
