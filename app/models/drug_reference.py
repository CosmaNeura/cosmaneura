from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field


class DrugReference(
    BaseModel, TimestampsMixin, TypeIDMixin("drug_reference"), table=True
):
    """Cached openFDA drug-label sections for a drug name.

    Keyed by normalized drug name (not per patient) — the label is the same
    for everyone, so one fetch serves all patients on that drug and we stay
    well under openFDA's per-IP rate limit. Contains no PHI: drug names and
    public-domain FDA label text only.
    """

    normalized_name: str = Field(unique=True, index=True)
    "lowercased drug name used as the cache key and openFDA query"

    sections: list = Field(default_factory=list, sa_type=JSONB)
    "list of {key, label, text} openFDA label sections; empty when the lookup found nothing"

    found: bool = Field(default=False)
    "whether openFDA returned any usable sections (distinguishes 'no data' from 'not yet fetched')"

    fetched_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "when openFDA was last queried for this name"
