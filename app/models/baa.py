"""BAA versions and acceptance records.

The canonical BAA text lives as a hash-verified HTML asset (see
``BAA_ASSET_DIR``); ``baa_version`` rows point at it and ``baa_acceptance``
rows are the append-only clickwrap execution record (CLAUDE_BAA_IMPLEMENTATION,
45 C.F.R. § 164.316(b)(2) retention). Never update or delete an acceptance
row; a retry returns the one immutable execution for that practice/version.
"""

from datetime import datetime

from app.setup import get_root_path

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

from .doctor import Doctor
from .practice import Practice

BAA_ASSET_DIR = get_root_path() / "app" / "assets" / "baa"

BAA_DOC_ID = "CN-BAA-CUST-CW"
BAA_CURRENT_VERSION = "2.2"
BAA_CURRENT_SHA256 = "13448bf42105db05e36e566f2f8ea0190926223e9dbb03efb8778cf8cfc7332e"
BAA_CURRENT_FILENAME = "baa_cn-baa-cust-cw_v2.2.html"
BAA_PINNED_SHA256_BY_EDITION = {
    (BAA_DOC_ID, BAA_CURRENT_VERSION): BAA_CURRENT_SHA256,
}


class BaaVersion(BaseModel, TimestampsMixin, TypeIDMixin("baa_version"), table=True):
    """One row per published BAA version. Text changes are new rows, never edits."""

    __table_args__ = (
        sa.UniqueConstraint("doc_id", "version", name="baa_version_doc_version_key"),
    )

    doc_id: str
    version: str
    sha256: str
    content_path: str
    "Repo-relative path of the canonical HTML asset."
    published_at: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    is_current: bool = False

    def content(self) -> str:
        return (get_root_path() / self.content_path).read_text()

    def pinned_content_sha256(self) -> str | None:
        return BAA_PINNED_SHA256_BY_EDITION.get((self.doc_id, self.version))

    def matches_pinned_content_sha256(self, content_sha256: str) -> bool:
        pinned_sha256 = self.pinned_content_sha256()
        return (
            pinned_sha256 is not None
            and self.sha256 == pinned_sha256
            and content_sha256 == pinned_sha256
        )


# No update or delete paths exist for acceptances in application code by
# design; retention is indefinite (45 C.F.R. § 164.316(b)(2) is the floor).
class BaaAcceptance(
    BaseModel, TimestampsMixin, TypeIDMixin("baa_acceptance"), table=True
):
    """Append-only clickwrap execution record; never updated or deleted."""

    __table_args__ = (
        sa.UniqueConstraint(
            "practice_id",
            "baa_version_id",
            name="baa_acceptance_practice_version_key",
        ),
    )

    practice_id: TypeIDType = Practice.foreign_key()
    baa_version_id: TypeIDType = BaaVersion.foreign_key()

    covered_entity_legal_name: str
    accepted_by_doctor_id: TypeIDType = Doctor.foreign_key()
    accepted_by_name: str
    accepted_by_title: str
    accepted_by_email: str
    accepted_at_utc: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "Server-generated; the client clock is never trusted."
    ip_address: str | None = None
    user_agent: str | None = Field(
        default=None, sa_column=Column(sa.String(length=512), nullable=True)
    )
    content_sha256: str
    "Hash of the exact text presented at acceptance."
