"""CI hash check for the canonical BAA legal text (CLAUDE_BAA_IMPLEMENTATION
step 8): an accidental edit to the asset fails the build. A legitimate text
change is a NEW version — new file, new hash, counsel approval, and
re-acceptance by customers — never an in-place edit.

The hash is intentionally duplicated here rather than imported from
app.models.baa: two independent records of the canonical value, and this
test stays runnable with no app imports and no environment.
"""

import hashlib
from pathlib import Path

ASSET_DIR = Path(__file__).resolve().parents[1] / "app" / "assets" / "baa"

CANONICAL_ASSETS = {
    "baa_cn-baa-cust-cw_v2.2.html": (
        "13448bf42105db05e36e566f2f8ea0190926223e9dbb03efb8778cf8cfc7332e"
    ),
}


def test_canonical_baa_assets_are_unmodified() -> None:
    for filename, expected_sha256 in CANONICAL_ASSETS.items():
        path = ASSET_DIR / filename
        assert path.exists(), f"canonical BAA asset missing: {path}"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == expected_sha256, (
            f"{filename} does not match its recorded SHA-256. The BAA legal "
            "text must never be edited in place — publish a new version "
            "(new file, new hash, counsel approval) instead, or revert the "
            "accidental change."
        )


def test_every_baa_asset_is_registered() -> None:
    on_disk = {p.name for p in ASSET_DIR.glob("*.html")}
    assert on_disk == set(CANONICAL_ASSETS), (
        "app/assets/baa contains HTML files not registered in this test's "
        f"CANONICAL_ASSETS map: {sorted(on_disk ^ set(CANONICAL_ASSETS))}. "
        "Register new versions here with their counsel-approved hash."
    )
