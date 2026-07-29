"""
RxNorm drug-name normalization via the NLM RxNav API (public domain, no key).

We send drug NAMES only — never patient identifiers. RxNav is not a BAA-covered
endpoint, so nothing that could identify a patient may be included in a request.

Only normalization is used here: NLM discontinued its drug-drug-interaction API
in January 2024, so interactions/depletions come from other sources (see the
provider reference toolkit plan), not from RxNav.
"""

import httpx

from app import log

RXNAV_BASE = "https://rxnav.nlm.nih.gov/REST"
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def normalize_drug_name(name: str) -> tuple[str | None, str | None]:
    """Best-effort (rxnorm_cui, canonical_name) for a drug name.

    Returns (None, None) when RxNav has no match or is unreachable — this is an
    enrichment, never a hard dependency, so callers proceed without it.
    """

    query = name.strip()
    if not query:
        return None, None

    try:
        response = httpx.get(
            f"{RXNAV_BASE}/rxcui.json",
            params={"name": query, "search": "2"},  # 2 = normalized/approximate
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        cuis = (response.json().get("idGroup") or {}).get("rxnormId") or []
        if not cuis:
            return None, None
        cui = cuis[0]
    except Exception as e:
        # name-only query, so no PHI in the error; log the type, not patient data
        log.warning("rxnorm lookup failed", error=str(e))
        return None, None

    canonical = None
    try:
        name_response = httpx.get(
            f"{RXNAV_BASE}/rxcui/{cui}/property.json",
            params={"propName": "RxNorm Name"},
            timeout=_TIMEOUT,
        )
        name_response.raise_for_status()
        props = (name_response.json().get("propConceptGroup") or {}).get(
            "propConcept"
        ) or []
        if props:
            canonical = props[0].get("propValue")
    except Exception as e:
        log.warning("rxnorm name lookup failed", error=str(e))

    return cui, canonical
