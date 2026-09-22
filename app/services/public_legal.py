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
from pathlib import Path
from typing import Any, Iterable

from app import root
from app.environments import is_production, is_staging

from app.models.public_onboarding import LegalDocumentKind, PublicLegalAcceptance
from sqlmodel import Session, select

PUBLIC_LEGAL_CONTRACT_VERSION = "public-legal-documents.v1"

# Taken from the effective date printed in both documents. A revision that
# changes the terms must change this; a revision that does not is a text edit
# and must still be acknowledged by updating the pinned digest in the tests.
PUBLIC_LEGAL_VERSION = "2026-04-16"


def _public_directory() -> Path:
    # The frontend build copies its static files here before the Python image
    # is assembled. /web is excluded from that image by .dockerignore.
    if is_production() or is_staging():
        return root / "public"
    return root / "web" / "public"


@dataclass(frozen=True)
class CurrentLegalDocument:
    kind: LegalDocumentKind
    version: str
    path: str
    filename: str

    @property
    def source(self) -> Path:
        return _public_directory() / self.filename

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.source.read_bytes()).hexdigest()


CURRENT_LEGAL_DOCUMENTS = (
    CurrentLegalDocument(
        kind=LegalDocumentKind.terms,
        version=PUBLIC_LEGAL_VERSION,
        path="/terms-of-service",
        filename="terms-of-service.html",
    ),
    CurrentLegalDocument(
        kind=LegalDocumentKind.privacy,
        version=PUBLIC_LEGAL_VERSION,
        path="/privacy-policy",
        filename="privacy-policy.html",
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

    Compare both cardinality and exact tuples so duplicate entries cannot be
    silently collapsed into an otherwise valid submission.
    """

    entries = list(submitted)

    expected = {
        (document.kind.value, document.version, document.sha256)
        for document in CURRENT_LEGAL_DOCUMENTS
    }
    return len(entries) == len(expected) and set(entries) == expected


def current_legal_acceptance_flags(
    session: Session,
    practice_id: Any,
) -> tuple[bool, bool]:
    """Match the exact current edition against append-only stored evidence.

    Kind and version alone cannot identify bytes when text changes without a
    version bump. The existing content_sha256 column preserves that distinction.
    """

    rows = session.exec(
        select(PublicLegalAcceptance).where(
            PublicLegalAcceptance.practice_id == practice_id
        )
    ).all()
    accepted = {
        (row.document_kind, row.document_version, row.content_sha256) for row in rows
    }

    terms = CURRENT_LEGAL_BY_KIND[LegalDocumentKind.terms]
    privacy = CURRENT_LEGAL_BY_KIND[LegalDocumentKind.privacy]
    return (
        (terms.kind.value, terms.version, terms.sha256) in accepted,
        (privacy.kind.value, privacy.version, privacy.sha256) in accepted,
    )
