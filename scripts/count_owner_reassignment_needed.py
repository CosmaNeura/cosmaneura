# /// script
# requires-python = ">=3.13"
# ///
"""Read-only count of practices with owner_reassignment_needed_at set.

John runs this through his normal path before flipping the production
self-delete latch. Agents never run it against production or any real
patient database.

Usage:

    uv run python scripts/count_owner_reassignment_needed.py
    # or
    uv run python -m app.cli count-owner-reassignment-needed
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    from activemodel.session_manager import global_session
    from app.models.practice import Practice
    from sqlmodel import col, func, select

    with global_session():
        from activemodel.session_manager import get_session

        with get_session() as session:
            count = session.exec(
                select(func.count())
                .select_from(Practice)
                .where(col(Practice.owner_reassignment_needed_at).is_not(None))
            ).one()

    print(
        json.dumps(
            {
                "owner_reassignment_needed_count": int(count),
                "read_only": True,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
