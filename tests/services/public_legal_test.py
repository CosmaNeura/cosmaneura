import hashlib
from pathlib import Path

from app.services.public_legal import (
    CURRENT_LEGAL_DOCUMENTS,
    legal_submission_is_current,
)

ROOT = Path(__file__).parents[2]


def test_current_legal_digests_pin_the_existing_canonical_route_sources() -> None:
    source_by_kind = {
        "terms": ROOT / "web" / "app" / "routes" / "terms-of-service.tsx",
        "privacy": ROOT / "web" / "app" / "routes" / "privacy-policy.tsx",
    }

    for document in CURRENT_LEGAL_DOCUMENTS:
        assert (
            hashlib.sha256(source_by_kind[document.kind.value].read_bytes()).hexdigest()
            == document.sha256
        )


def test_legal_submission_requires_both_exact_current_documents() -> None:
    exact = [
        (document.kind.value, document.version, document.sha256)
        for document in CURRENT_LEGAL_DOCUMENTS
    ]
    assert legal_submission_is_current(exact)
    assert not legal_submission_is_current(exact[:1])
    assert not legal_submission_is_current([*exact, ("terms", "old", "0" * 64)])
    assert not legal_submission_is_current(
        [(kind, version, "0" * 64) for kind, version, _ in exact]
    )
