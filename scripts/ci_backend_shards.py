"""Stable, exhaustive file partition for the two offline infrastructure workers.

Keep application/database/browser tests on their existing serial recipes. These
workers deliberately use infrastructure's own fixture boundary, as its existing
collection regressions do, rather than initializing the application database.
"""

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def shard_for(path: str) -> int:
    return int.from_bytes(hashlib.sha256(path.encode()).digest()[:8], "big") % 2 + 1


def selected_files(shard: int, root: Path = ROOT) -> list[str]:
    if shard not in (1, 2):
        raise ValueError("infrastructure shard must be 1 or 2")
    paths = sorted(
        path.relative_to(root).as_posix()
        for path in (root / "tests/infrastructure").rglob("*.py")
        if path.name.startswith("test_") or path.name.endswith("_test.py")
    )
    selected = [path for path in paths if shard_for(path) == shard]
    if not selected:
        raise ValueError("infrastructure shard must not be empty")
    return selected


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: ci_backend_shards.py {1|2}")
    print("\n".join(selected_files(int(sys.argv[1]))))
