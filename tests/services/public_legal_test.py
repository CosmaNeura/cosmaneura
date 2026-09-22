import pytest

from app.services.public_legal import (
    CURRENT_LEGAL_BY_KIND,
    CURRENT_LEGAL_DOCUMENTS,
    PUBLIC_LEGAL_VERSION,
    current_legal_documents,
    legal_submission_is_current,
)

from app.models.public_onboarding import LegalDocumentKind

# Pinned against the documents served from web/public/ on 2026-09-22.
#
# If one of these fails, the legal text was edited. That is not a test to
# update reflexively: decide whether the change was substantive enough to
# require a new PUBLIC_LEGAL_VERSION and a fresh round of acceptances, then
# update this pin to match.
EXPECTED_DIGESTS = {
    LegalDocumentKind.terms: (
        "14257786b1257babce0db697bdc107532be74fc174bb5658b1d9ca99bd0a4a92"
    ),
    LegalDocumentKind.privacy: (
        "0b5d3c950df85afdff3f7a07f94ced338d216512cd47ce4bc87a021b5bbac573"
    ),
}


def _current_tuples() -> set[tuple[str, str, str]]:
    return {
        (document.kind.value, document.version, document.sha256)
        for document in CURRENT_LEGAL_DOCUMENTS
    }


def test_exactly_terms_and_privacy_are_required() -> None:
    assert {document.kind for document in CURRENT_LEGAL_DOCUMENTS} == {
        LegalDocumentKind.terms,
        LegalDocumentKind.privacy,
    }


@pytest.mark.parametrize("kind", [LegalDocumentKind.terms, LegalDocumentKind.privacy])
def test_digest_matches_the_document_actually_served(kind: LegalDocumentKind) -> None:
    """The pin and the served file must agree; see the note on EXPECTED_DIGESTS."""

    document = CURRENT_LEGAL_BY_KIND[kind]

    assert document.source.exists(), document.source
    assert document.sha256 == EXPECTED_DIGESTS[kind]


def test_published_metadata_carries_no_filesystem_detail() -> None:
    """What is served publicly must not leak repository paths."""

    for published in current_legal_documents():
        assert set(published) == {"kind", "version", "path", "sha256"}
        assert published["version"] == PUBLIC_LEGAL_VERSION
        assert published["path"].startswith("/")
        assert len(published["sha256"]) == 64


def test_a_complete_current_submission_is_accepted() -> None:
    assert legal_submission_is_current(_current_tuples())


def test_a_missing_document_is_rejected() -> None:
    incomplete = {
        tuple_
        for tuple_ in _current_tuples()
        if tuple_[0] != LegalDocumentKind.privacy.value
    }

    assert not legal_submission_is_current(incomplete)


def test_an_unknown_extra_document_is_rejected() -> None:
    extra = _current_tuples() | {("marketing", PUBLIC_LEGAL_VERSION, "0" * 64)}

    assert not legal_submission_is_current(extra)


def test_a_stale_version_is_rejected() -> None:
    terms = CURRENT_LEGAL_BY_KIND[LegalDocumentKind.terms]
    stale = {
        tuple_
        for tuple_ in _current_tuples()
        if tuple_[0] != LegalDocumentKind.terms.value
    } | {(terms.kind.value, "2025-01-01", terms.sha256)}

    assert not legal_submission_is_current(stale)


def test_a_tampered_digest_is_rejected() -> None:
    """Acknowledging the right version of the wrong bytes is not acceptance."""

    privacy = CURRENT_LEGAL_BY_KIND[LegalDocumentKind.privacy]
    tampered = {
        tuple_
        for tuple_ in _current_tuples()
        if tuple_[0] != LegalDocumentKind.privacy.value
    } | {(privacy.kind.value, privacy.version, "f" * 64)}

    assert not legal_submission_is_current(tampered)


def test_an_empty_submission_is_rejected() -> None:
    assert not legal_submission_is_current([])
