"""Versioned public Terms and Privacy metadata and acceptance checks.

The canonical legal text remains in the existing public web routes.  These
digests pin the exact reviewed source presented for each dated version; a text
change must publish a new version and update the digest rather than rewriting
an existing acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.models.public_onboarding import LegalDocumentKind, PublicLegalAcceptance
from sqlmodel import Session, select

PUBLIC_LEGAL_CONTRACT_VERSION = "public-legal-documents.v1"
PUBLIC_LEGAL_VERSION = "2026-04-16"


@dataclass(frozen=True)
class CurrentLegalDocument:
    kind: LegalDocumentKind
    version: str
    sha256: str
    path: str


CURRENT_LEGAL_DOCUMENTS = (
    CurrentLegalDocument(
        kind=LegalDocumentKind.terms,
        version=PUBLIC_LEGAL_VERSION,
        sha256="8059b55abe47e1c5767d196b5ce976e878cf5320c41d25d9b023f7e79a92edd9",
        path="/terms-of-service",
    ),
    CurrentLegalDocument(
        kind=LegalDocumentKind.privacy,
        version=PUBLIC_LEGAL_VERSION,
        sha256="3319e31b4a852c3e111df4d9c6686b22556ccd0985d6429b48dd33e54cf27018",
        path="/privacy-policy",
    ),
)

CURRENT_LEGAL_BY_KIND = {
    document.kind: document for document in CURRENT_LEGAL_DOCUMENTS
}


def legal_submission_is_current(
    submitted: Iterable[tuple[str, str, str]],
) -> bool:
    """Require one exact current Terms and Privacy tuple, with no extras."""

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
    """Return whether the exact current Terms and Privacy versions are accepted."""

    rows = list(
        session.exec(
            select(PublicLegalAcceptance).where(
                PublicLegalAcceptance.practice_id == practice_id
            )
        ).all()
    )
    accepted = {
        (row.document_kind, row.document_version, row.content_sha256) for row in rows
    }
    terms = CURRENT_LEGAL_BY_KIND[LegalDocumentKind.terms]
    privacy = CURRENT_LEGAL_BY_KIND[LegalDocumentKind.privacy]
    return (
        (terms.kind.value, terms.version, terms.sha256) in accepted,
        (privacy.kind.value, privacy.version, privacy.sha256) in accepted,
    )
