"""
Populate the DrugReference cache from openFDA for a drug name.

Cache is keyed by drug name (not patient), so this runs once per distinct drug
across the whole platform and keeps us under openFDA's per-IP rate limit. No
PHI: drug names and public-domain FDA label text only.
"""

from datetime import datetime, timezone

from app import log
from app.celery import celery_app
from app.services.openfda import fetch_label_sections

from app.models.drug_reference import DrugReference

# openFDA labels change rarely; refetch at most this often per drug.
REFRESH_AFTER_DAYS = 30


def _is_fresh(row: DrugReference) -> bool:
    if row.fetched_at is None:
        return False
    age = datetime.now(timezone.utc) - row.fetched_at
    return age.days < REFRESH_AFTER_DAYS


def perform(normalized_name: str):
    name = (normalized_name or "").strip().lower()
    if not name:
        return

    row = DrugReference.get(normalized_name=name)
    if row is not None and _is_fresh(row):
        log.info("drug reference cache fresh, skipping", drug=name)
        return

    sections = fetch_label_sections(name)

    if row is None:
        row = DrugReference(normalized_name=name)

    row.sections = sections or []
    row.found = bool(sections)
    row.fetched_at = datetime.now(timezone.utc)
    row.save()

    log.info("cached drug reference", drug=name, section_count=len(row.sections))


perform_celery = celery_app.task(perform)


def queue(*args, **kwargs):
    "always use this method to queue jobs so it's easy to swap out the job system"
    perform_celery.delay(*args, **kwargs)
