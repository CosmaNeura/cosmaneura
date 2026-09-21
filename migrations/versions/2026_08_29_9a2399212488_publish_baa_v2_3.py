"""Publish counsel-approved CN-BAA-CUST-CW v2.3.

The v2.2 row and every historical acceptance remain immutable. This migration
adds v2.3, then makes it the sole current clickwrap version so a substantive
agreement change requires a new acceptance.

Revision ID: 9a2399212488
Revises: da6e199668a4
Create Date: 2026-08-29 11:06:57.608593

"""
from collections.abc import Sequence
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from typeid import TypeID


# revision identifiers, used by Alembic.
revision: str = "9a2399212488"
down_revision: str | None = "da6e199668a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BAA_DOC_ID = "CN-BAA-CUST-CW"
BAA_V22_VERSION = "2.2"
BAA_V23_VERSION = "2.3"
BAA_V23_SHA256 = "9bdc110b6b2ffcc9f5e7aee653af499357d2f35dccf43c57d8bbcf70094ed34e"
BAA_V23_PATH = "app/assets/baa/baa_cn-baa-cust-cw_v2.3.html"
BAA_V23_PUBLISHED_AT = datetime(2026, 8, 29, tzinfo=timezone.utc)
BAA_V23_REFERENCE = f"{BAA_DOC_ID} v{BAA_V23_VERSION}"


def _refuse_material_downgrade() -> None:
    """Never orphan a v2.3 acceptance or reopen access under v2.2."""

    if op.get_context().as_sql:
        raise RuntimeError("BAA v2.3 downgrade is unsupported in offline mode")
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE baa_acceptance, baa_version, practice IN ACCESS EXCLUSIVE MODE")
    )
    material_references = connection.execute(
        sa.text(
            """
            SELECT
                (SELECT count(*)
                 FROM baa_acceptance AS acceptance
                 JOIN baa_version AS version
                   ON version.id = acceptance.baa_version_id
                 WHERE version.doc_id = :doc_id
                   AND version.version = :version)
              + (SELECT count(*)
                 FROM practice
                 WHERE baa_version = :reference)
            """
        ).bindparams(
            doc_id=BAA_DOC_ID,
            version=BAA_V23_VERSION,
            reference=BAA_V23_REFERENCE,
        )
    ).scalar_one()
    if material_references:
        raise RuntimeError(
            "refusing to remove BAA v2.3 after acceptance: "
            f"material_references={material_references}"
        )


def upgrade() -> None:
    # Insert first and fail on any pre-existing conflicting v2.3 row. A legal
    # edition is immutable; migration replay must never overwrite its bytes or
    # publication metadata.
    op.execute(
        sa.text(
            """
            INSERT INTO baa_version
                (id, doc_id, version, sha256, content_path, published_at, is_current)
            VALUES
                (CAST(:id AS uuid), :doc_id, :version, :sha256, :path,
                 :published_at, false)
            """
        ).bindparams(
            id=str(TypeID(prefix="baa_version").uuid),
            doc_id=BAA_DOC_ID,
            version=BAA_V23_VERSION,
            sha256=BAA_V23_SHA256,
            path=BAA_V23_PATH,
            published_at=BAA_V23_PUBLISHED_AT,
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE baa_version
            SET is_current = (version = :version), updated_at = now()
            WHERE doc_id = :doc_id
            """
        ).bindparams(doc_id=BAA_DOC_ID, version=BAA_V23_VERSION)
    )


def downgrade() -> None:
    _refuse_material_downgrade()
    op.execute(
        sa.text(
            "DELETE FROM baa_version WHERE doc_id = :doc_id AND version = :version"
        ).bindparams(doc_id=BAA_DOC_ID, version=BAA_V23_VERSION)
    )
    op.execute(
        sa.text(
            """
            UPDATE baa_version
            SET is_current = (version = :version), updated_at = now()
            WHERE doc_id = :doc_id
            """
        ).bindparams(doc_id=BAA_DOC_ID, version=BAA_V22_VERSION)
    )
