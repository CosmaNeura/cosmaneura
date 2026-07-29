"""
openFDA Drug Label API client (public domain, U.S. FDA).

Given a drug NAME, fetch the label sections a provider wants when a patient's
symptoms might be an adverse effect of their prescription: adverse reactions,
warnings, drug interactions, and any boxed warning.

Only drug NAMES go over the wire — never patient identifiers. openFDA is not a
BAA endpoint. This is an enrichment: any failure degrades to None and callers
proceed without it.

Data is U.S. Government public domain (open.fda.gov/license). We display it
with attribution; we do not modify or interpret it.
"""

from urllib.parse import quote

import httpx

from app import log

OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# Sections we surface, mapped to the openFDA field names. Each openFDA field is
# an array of (often large) strings; we keep the first block and cap length so
# a note page stays scannable.
_SECTIONS = {
    "adverse_reactions": "Adverse reactions",
    "drug_interactions": "Drug interactions",
    "warnings_and_cautions": "Warnings & cautions",
    "warnings": "Warnings",
    "boxed_warning": "Boxed warning",
}

_MAX_SECTION_CHARS = 4000


class DrugLabelSection:
    def __init__(self, key: str, label: str, text: str):
        self.key = key
        self.label = label
        self.text = text


def _search_query(name: str) -> str:
    # match brand OR generic name; both are nested arrays under openfda
    phrase = quote(f'"{name}"')
    return f"(openfda.brand_name:{phrase}+OR+openfda.generic_name:{phrase})"


def fetch_label_sections(name: str) -> list[dict] | None:
    """Return [{key, label, text}] for a drug name, or None if unavailable.

    None means "no data / lookup failed" — never an error the caller must
    handle. Sections absent from the label are simply omitted.
    """

    query = name.strip()
    if not query:
        return None

    url = f"{OPENFDA_LABEL_URL}?search={_search_query(query)}&limit=1"

    try:
        response = httpx.get(url, timeout=_TIMEOUT)
        if response.status_code == 404:
            # openFDA returns 404 when a search matches nothing
            return None
        response.raise_for_status()
        results = response.json().get("results") or []
    except Exception as e:
        # name-only request → no PHI in the error
        log.warning("openfda lookup failed", error=str(e))
        return None

    if not results:
        return None

    label = results[0]
    sections: list[dict] = []
    seen_texts: set[str] = set()

    for field, display in _SECTIONS.items():
        value = label.get(field)
        if not value or not isinstance(value, list):
            continue
        text = str(value[0]).strip()
        if not text:
            continue
        # "warnings" and "warnings_and_cautions" carry different labels but
        # frequently hold identical text; dedup on the content, keep the first.
        dedup_key = text[:200]
        if dedup_key in seen_texts:
            continue
        seen_texts.add(dedup_key)
        sections.append(
            {
                "key": field,
                "label": display,
                "text": text[:_MAX_SECTION_CHARS],
            }
        )

    return sections or None
