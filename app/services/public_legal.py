"""Versioned public Terms and Privacy metadata and acceptance checks.

The canonical legal text is the static document served to the public site. The
digest here is derived from that exact file rather than transcribed, so the two
cannot drift: a copied constant silently becomes wrong the moment the text is
edited, which is the failure this module exists to prevent.

`tests/services/public_legal_test.py` pins the expected digests. Editing the
document therefore fails that test, forcing a deliberate answer to the question
the effective date cannot answer on its own — was this substantive enough to
publish a new version?
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from app.models.public_onboarding import LegalDocumentKind, PublicLegalAcceptance
from sqlmodel import Session, select

PUBLIC_LEGAL_CONTRACT_VERSION = "public-legal-documents.v1"

# Taken from the effective date printed in both documents. A revision that
# changes the terms must change this; a revision that does not is a text edit
# and must still be acknowledged by updating the pinned digest in the tests.
PUBLIC_LEGAL_VERSION = "2026-04-16"

_PUBLIC_DIRECTORY = Path(__file__).resolve().parents[2] / "web" / "public"


@dataclass(frozen=True)
class CurrentLegalDocument:
    kind: LegalDocumentKind
    version: str
    path: str
    source: Path

    @property
    def sha256(self) -> str:
        return _document_digest(self.source)


@lru_cache(maxsize=None)
def _document_digest(source: Path) -> str:
    return hashlib.sha256(source.read_bytes()).hexdigest()


CURRENT_LEGAL_DOCUMENTS = (
    CurrentLegalDocument(
        kind=LegalDocumentKind.terms,
        version=PUBLIC_LEGAL_VERSION,
        path="/terms-of-service",
        source=_PUBLIC_DIRECTORY / "terms-of-service.html",
    ),
    CurrentLegalDocument(
        kind=LegalDocumentKind.privacy,
        version=PUBLIC_LEGAL_VERSION,
        path="/privacy-policy",
        source=_PUBLIC_DIRECTORY / "privacy-policy.html",
    ),
)

CURRENT_LEGAL_BY_KIND = {
    document.kind: document for document in CURRENT_LEGAL_DOCUMENTS
}


def current_legal_documents() -> tuple[dict[str, str], ...]:
    """The documents a client must acknowledge, safe to serve publicly."""

    return tuple(
        {
            "kind": document.kind.value,
            "version": document.version,
            "path": document.path,
            "sha256": document.sha256,
        }
        for document in CURRENT_LEGAL_DOCUMENTS
    )


def legal_submission_is_current(submitted: Iterable[tuple[str, str, str]]) -> bool:
    """Require one exact current Terms and Privacy tuple, with no extras.

    Set equality rather than a subset check: an acceptance that omits a document,
    duplicates one, or carries an unknown extra is rejected rather than partially
    honoured.
    """

    normalized = {(kind, version, digest) for kind, version, digest in submitted}
    expected = {
        (document.kind.value, document.version, document.sha256)
        for document in CURRENT_LEGAL_DOCUMENTS
    }
    return normalized == expected


def current_legal_acceptance_flags(
    session: Session,
    practice_id: Any,
) -> tuple[bool, bool]:
    """Return whether the current Terms and Privacy versions are accepted.

    Matched on (kind, version). The accepted document's digest is validated at
    submission by :func:`legal_submission_is_current` but is not persisted —
    `PublicLegalAcceptance` has no digest column — so a later audit can prove
    which *version* was accepted but not which exact bytes. Closing that gap
    needs a schema change and belongs in its own slice.
    """

    rows = session.exec(
        select(PublicLegalAcceptance).where(
            PublicLegalAcceptance.practice_id == practice_id
        )
    ).all()
    accepted = {(row.document_kind, row.document_version) for row in rows}

    terms = CURRENT_LEGAL_BY_KIND[LegalDocumentKind.terms]
    privacy = CURRENT_LEGAL_BY_KIND[LegalDocumentKind.privacy]
    return (
        (terms.kind.value, terms.version) in accepted,
        (privacy.kind.value, privacy.version) in accepted,
    )
