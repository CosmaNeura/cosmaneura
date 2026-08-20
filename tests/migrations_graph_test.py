"""Guard the Alembic revision graph.

Hand-written migrations have twice invented "random-looking" revision IDs;
in August 2026 two migrations collided on ``a1b2c3d4e5f6``, which turned the
revision map into a cycle and broke ``db_migrate`` on CI. These tests fail in
seconds with a readable message instead. Never hand-write revision IDs — use
``just db_generate_migration`` or ``secrets.token_hex(6)``.

No app imports and no database: safe to run from lefthook on any machine.
"""

import re
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"

REVISION_RE = re.compile(
    r"^revision(?::\s*str)?\s*=\s*['\"](?P<rev>\w+)['\"]", re.MULTILINE
)
DOWN_REVISION_RE = re.compile(
    r"^down_revision(?::[^=]+)?\s*=\s*(?:['\"](?P<rev>\w+)['\"]|None)", re.MULTILINE
)


def _revision_graph() -> tuple[dict[str, str], dict[str, str | None]]:
    """Return (revision -> filename, revision -> parent) for every migration."""

    files: dict[str, str] = {}
    parents: dict[str, str | None] = {}

    for path in sorted(VERSIONS_DIR.glob("*.py")):
        text = path.read_text()
        revision_match = REVISION_RE.search(text)
        assert revision_match, f"{path.name} has no parseable `revision =` line"
        revision = revision_match.group("rev")

        assert revision not in files, (
            f"Duplicate revision id {revision!r} in {files[revision]} and "
            f"{path.name}. Alembic cannot load a graph with duplicate ids — "
            "regenerate one of them with `just db_generate_migration` or "
            "`python -c 'import secrets; print(secrets.token_hex(6))'`."
        )

        down_match = DOWN_REVISION_RE.search(text)
        assert down_match, f"{path.name} has no parseable `down_revision =` line"

        files[revision] = path.name
        parents[revision] = down_match.group("rev")

    assert files, f"no migrations found under {VERSIONS_DIR}"
    return files, parents


def test_parents_exist() -> None:
    files, parents = _revision_graph()
    for revision, parent in parents.items():
        if parent is not None:
            assert parent in parents, (
                f"{files[revision]} revises {parent!r}, which does not exist"
            )


def test_no_cycles() -> None:
    files, parents = _revision_graph()

    # 0 = unvisited, 1 = on the current parent chain, 2 = known cycle-free
    state: dict[str, int] = {}
    for start in parents:
        if state.get(start):
            continue
        chain: list[str] = []
        node: str | None = start
        while node is not None and state.get(node, 0) == 0:
            state[node] = 1
            chain.append(node)
            node = parents.get(node)
        if node is not None and state.get(node) == 1:
            cycle = chain[chain.index(node) :] + [node]
            raise AssertionError(
                "Cycle in the Alembic revision graph: " + " -> ".join(cycle)
            )
        for visited in chain:
            state[visited] = 2


def test_exactly_one_head() -> None:
    files, parents = _revision_graph()
    claimed = {parent for parent in parents.values() if parent}
    heads = sorted(revision for revision in parents if revision not in claimed)
    assert len(heads) == 1, (
        f"Expected exactly one migration head, found {len(heads)}: "
        f"{[(head, files[head]) for head in heads]}"
    )
