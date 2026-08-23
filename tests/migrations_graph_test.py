"""Guard the Alembic revision graph.

Hand-written migrations have twice invented "random-looking" revision IDs;
in August 2026 two migrations collided on ``a1b2c3d4e5f6``, which turned the
revision map into a cycle and broke ``db_migrate`` on CI. These tests fail in
seconds with a readable message instead. Never hand-write revision IDs — use
``just db_generate_migration`` or ``secrets.token_hex(6)``.

No app imports and no database: safe to run from lefthook on any machine.
Merge revisions (``alembic merge``) declare a tuple of parents; the graph
here is parsed with ``ast`` so string, tuple, and multi-line forms all work.
"""

import ast
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _revision_literals(path: Path) -> dict[str, object]:
    """The ``revision``/``down_revision`` literals of one migration file."""

    values: dict[str, object] = {}
    for node in ast.parse(path.read_text()).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name, value_node = node.targets[0].id, node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            name, value_node = node.target.id, node.value
        else:
            continue
        if name in {"revision", "down_revision"}:
            try:
                values[name] = ast.literal_eval(value_node)
            except ValueError as exc:
                raise AssertionError(
                    f"{path.name}: `{name}` must be a literal, not an expression"
                ) from exc
    return values


def _revision_graph() -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """Return (revision -> filename, revision -> parents) for every migration.

    Roots have an empty parents tuple; merge revisions have several parents.
    """

    files: dict[str, str] = {}
    parents: dict[str, tuple[str, ...]] = {}

    for path in sorted(VERSIONS_DIR.glob("*.py")):
        values = _revision_literals(path)

        revision = values.get("revision")
        assert isinstance(revision, str) and revision, (
            f"{path.name} has no parseable `revision =` assignment"
        )
        assert "down_revision" in values, (
            f"{path.name} has no parseable `down_revision =` assignment"
        )

        down = values["down_revision"]
        if down is None:
            parent_tuple: tuple[str, ...] = ()
        elif isinstance(down, str):
            parent_tuple = (down,)
        else:
            assert isinstance(down, (tuple, list)) and all(
                isinstance(parent, str) for parent in down
            ), f"{path.name} has an unsupported `down_revision` value: {down!r}"
            parent_tuple = tuple(down)

        assert revision not in files, (
            f"Duplicate revision id {revision!r} in {files[revision]} and "
            f"{path.name}. Alembic cannot load a graph with duplicate ids — "
            "regenerate one of them with `just db_generate_migration` or "
            "`python -c 'import secrets; print(secrets.token_hex(6))'`."
        )

        files[revision] = path.name
        parents[revision] = parent_tuple

    assert files, f"no migrations found under {VERSIONS_DIR}"
    return files, parents


def test_parents_exist() -> None:
    files, parents = _revision_graph()
    for revision, revision_parents in parents.items():
        for parent in revision_parents:
            assert parent in parents, (
                f"{files[revision]} revises {parent!r}, which does not exist"
            )


def test_no_cycles() -> None:
    files, parents = _revision_graph()

    # 0/absent = unvisited, 1 = on the current chain, 2 = known cycle-free
    state: dict[str, int] = {}

    def visit(node: str, chain: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            cycle = chain[chain.index(node) :] + [node]
            raise AssertionError(
                "Cycle in the Alembic revision graph: " + " -> ".join(cycle)
            )
        state[node] = 1
        chain.append(node)
        for parent in parents.get(node, ()):
            if parent in parents:  # missing parents are test_parents_exist's job
                visit(parent, chain)
        chain.pop()
        state[node] = 2

    for start in parents:
        visit(start, [])


def test_exactly_one_head() -> None:
    files, parents = _revision_graph()
    claimed = {
        parent for revision_parents in parents.values() for parent in revision_parents
    }
    heads = sorted(revision for revision in parents if revision not in claimed)
    assert len(heads) == 1, (
        f"Expected exactly one migration head, found {len(heads)}: "
        f"{[(head, files[head]) for head in heads]}"
    )
