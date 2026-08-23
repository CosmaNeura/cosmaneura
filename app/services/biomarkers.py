"""
Canonical biomarker registry: the shared vocabulary lab results and reference
ranges key on.

Lab values arrive with messy names ("Vit D, 25-OH", "TSH", "Ferritin") — this
maps them to a stable canonical key so a result can be joined to its reference
range (see app/services/lab_ranges.py) regardless of how the source labeled it.

No PHI: biomarker names and units only.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Biomarker:
    key: str
    name: str
    unit: str
    aliases: frozenset[str]


# Conservative starter set covering the markers the functional-medicine
# workflow leans on most. Grows one reviewed entry at a time.
_BIOMARKERS: list[Biomarker] = [
    Biomarker(
        "vitamin_d_25oh",
        "Vitamin D, 25-OH",
        "ng/mL",
        frozenset(
            {
                "vitamin d",
                "25-oh vitamin d",
                "25 hydroxyvitamin d",
                "vit d",
                "vitamin d 25 oh",
                "25-hydroxyvitamin d",
            }
        ),
    ),
    Biomarker(
        "vitamin_b12",
        "Vitamin B12",
        "pg/mL",
        frozenset({"b12", "vitamin b12", "cobalamin", "vit b12"}),
    ),
    Biomarker(
        "folate", "Folate", "ng/mL", frozenset({"folate", "folic acid", "serum folate"})
    ),
    Biomarker("ferritin", "Ferritin", "ng/mL", frozenset({"ferritin"})),
    Biomarker(
        "tsh",
        "TSH",
        "mIU/L",
        frozenset({"tsh", "thyroid stimulating hormone", "thyrotropin"}),
    ),
    Biomarker(
        "free_t3",
        "Free T3",
        "pg/mL",
        frozenset({"free t3", "ft3", "triiodothyronine free"}),
    ),
    Biomarker(
        "free_t4", "Free T4", "ng/dL", frozenset({"free t4", "ft4", "thyroxine free"})
    ),
    Biomarker(
        "hba1c",
        "Hemoglobin A1c",
        "%",
        frozenset({"hba1c", "a1c", "hemoglobin a1c", "glycated hemoglobin"}),
    ),
    Biomarker(
        "magnesium",
        "Magnesium",
        "mg/dL",
        frozenset({"magnesium", "mg", "serum magnesium"}),
    ),
    Biomarker(
        "potassium",
        "Potassium",
        "mmol/L",
        frozenset({"potassium", "k", "serum potassium"}),
    ),
    Biomarker(
        "crp_hs",
        "hs-CRP",
        "mg/L",
        frozenset(
            {
                "hs-crp",
                "high sensitivity crp",
                "hscrp",
                "c-reactive protein high sensitivity",
            }
        ),
    ),
    Biomarker(
        "homocysteine", "Homocysteine", "µmol/L", frozenset({"homocysteine", "hcy"})
    ),
    Biomarker(
        "ldl_c",
        "LDL cholesterol",
        "mg/dL",
        frozenset({"ldl", "ldl-c", "ldl cholesterol"}),
    ),
    Biomarker(
        "hdl_c",
        "HDL cholesterol",
        "mg/dL",
        frozenset({"hdl", "hdl-c", "hdl cholesterol"}),
    ),
    Biomarker(
        "triglycerides",
        "Triglycerides",
        "mg/dL",
        frozenset({"triglycerides", "trig", "tg"}),
    ),
]

BY_KEY: dict[str, Biomarker] = {b.key: b for b in _BIOMARKERS}

_ALIAS_TO_KEY: dict[str, str] = {}
for _b in _BIOMARKERS:
    _ALIAS_TO_KEY[_b.name.lower()] = _b.key
    _ALIAS_TO_KEY[_b.key] = _b.key
    for _alias in _b.aliases:
        _ALIAS_TO_KEY[_alias.lower()] = _b.key


def normalize_biomarker(raw_name: str) -> str | None:
    "map a raw lab-result name to a canonical key, or None if unrecognized"
    cleaned = (raw_name or "").strip().lower()
    if not cleaned:
        return None
    return _ALIAS_TO_KEY.get(cleaned)


def display_name(key: str) -> str:
    marker = BY_KEY.get(key)
    return marker.name if marker else key


def default_unit(key: str) -> str | None:
    marker = BY_KEY.get(key)
    return marker.unit if marker else None
