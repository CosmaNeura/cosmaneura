"""
Curated, citation-backed drug-induced nutrient depletion (DIND) reference.

This is a small, conservative seed of well-documented drug -> depleted-nutrient
relationships drawn from the peer-reviewed literature and regulatory sources.
It is intentionally NOT a copy of any subscription database (e.g. NatMed): the
underlying drug-nutrient facts are widely published, and every row here carries
its own primary or authoritative citation. It covers a fraction of what a
licensed database does; it is a license-clean baseline that grows one cited row
at a time (additions go through code review, so citations stay vetted).

Contains no PHI and no patient data — drug names and public references only.

Evidence tiers (shown to the provider, never collapsed away):
- well_established: RCT / regulatory label / clear pharmacology
- moderate: consistent observational or mechanistic evidence
- limited: mixed or older evidence; flagged so the provider weights it
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Citation:
    reference: str
    url: str


@dataclass(frozen=True)
class Depletion:
    nutrient: str
    evidence: str  # well_established | moderate | limited
    mechanism: str
    citation: Citation


@dataclass(frozen=True)
class _Rule:
    # normalized (lowercase) ingredient names this rule applies to
    drugs: frozenset[str]
    depletions: tuple[Depletion, ...]


DISCLAIMER = (
    "Drug-induced nutrient depletion reference for clinician review. "
    "Evidence and clinical significance vary; verify against a full clinical "
    "resource and the patient's labs before acting. Not medical advice."
)

# --- Citations (real, verified) -------------------------------------------

_DE_JAGER_2010 = Citation(
    reference="de Jager J, et al. BMJ. 2010;340:c2181 (RCT).",
    url="https://doi.org/10.1136/bmj.c2181",
)
_MOHN_2018 = Citation(
    reference="Mohn ES, et al. Pharmaceutics. 2018;10(1):36 (review).",
    url="https://doi.org/10.3390/pharmaceutics10010036",
)
_FDA_PPI_MG_2011 = Citation(
    reference="FDA Drug Safety Communication: PPIs & low serum magnesium (2011).",
    url="https://www.fda.gov/drugs/drug-safety-and-availability/fda-drug-safety-communication-low-magnesium-levels-can-be-associated-long-term-use-proton-pump",
)
_MARCOFF_2007 = Citation(
    reference="Marcoff L, Thompson PD. J Am Coll Cardiol. 2007;49(23):2231-2237 (systematic review).",
    url="https://doi.org/10.1016/j.jacc.2007.02.049",
)
_SICA_2007 = Citation(
    reference="Sica DA. Congest Heart Fail. 2007;13(4):244-247 (loop diuretics & thiamine).",
    url="https://doi.org/10.1111/j.1527-5299.2007.06260.x",
)
_WILSON_2011 = Citation(
    reference="Wilson SMC, et al. Nutr Rev. 2011;69(10):572-583 (review).",
    url="https://doi.org/10.1111/j.1753-4887.2011.00419.x",
)

# --- Depletion rules -------------------------------------------------------

_RULES: list[_Rule] = [
    _Rule(
        drugs=frozenset({"metformin"}),
        depletions=(
            Depletion(
                nutrient="Vitamin B12",
                evidence="well_established",
                mechanism="Long-term metformin lowers serum B12 (ileal absorption); RCT-confirmed.",
                citation=_DE_JAGER_2010,
            ),
        ),
    ),
    _Rule(
        # proton pump inhibitors
        drugs=frozenset(
            {
                "omeprazole",
                "esomeprazole",
                "lansoprazole",
                "pantoprazole",
                "rabeprazole",
                "dexlansoprazole",
            }
        ),
        depletions=(
            Depletion(
                nutrient="Magnesium",
                evidence="well_established",
                mechanism="Long-term PPI use associated with hypomagnesemia (FDA label warning).",
                citation=_FDA_PPI_MG_2011,
            ),
            Depletion(
                nutrient="Vitamin B12",
                evidence="moderate",
                mechanism="Reduced gastric acid impairs B12 absorption with chronic use.",
                citation=_MOHN_2018,
            ),
        ),
    ),
    _Rule(
        # HMG-CoA reductase inhibitors (statins)
        drugs=frozenset(
            {
                "atorvastatin",
                "simvastatin",
                "rosuvastatin",
                "pravastatin",
                "lovastatin",
                "pitavastatin",
                "fluvastatin",
            }
        ),
        depletions=(
            Depletion(
                nutrient="Coenzyme Q10",
                evidence="moderate",
                mechanism="Statins block the mevalonate pathway shared with CoQ10 synthesis, lowering serum CoQ10; clinical significance debated.",
                citation=_MARCOFF_2007,
            ),
        ),
    ),
    _Rule(
        # loop diuretics
        drugs=frozenset({"furosemide", "bumetanide", "torsemide", "ethacrynic acid"}),
        depletions=(
            Depletion(
                nutrient="Potassium",
                evidence="well_established",
                mechanism="Increased renal potassium excretion.",
                citation=_MOHN_2018,
            ),
            Depletion(
                nutrient="Magnesium",
                evidence="well_established",
                mechanism="Increased renal magnesium excretion.",
                citation=_MOHN_2018,
            ),
            Depletion(
                nutrient="Thiamine (B1)",
                evidence="moderate",
                mechanism="Increased urinary thiamine loss; relevant in heart failure and the elderly.",
                citation=_SICA_2007,
            ),
        ),
    ),
    _Rule(
        # thiazide diuretics
        drugs=frozenset(
            {"hydrochlorothiazide", "chlorthalidone", "indapamide", "metolazone"}
        ),
        depletions=(
            Depletion(
                nutrient="Potassium",
                evidence="well_established",
                mechanism="Increased renal potassium excretion.",
                citation=_MOHN_2018,
            ),
            Depletion(
                nutrient="Magnesium",
                evidence="moderate",
                mechanism="Increased renal magnesium excretion with chronic use.",
                citation=_MOHN_2018,
            ),
        ),
    ),
    _Rule(
        # systemic corticosteroids
        drugs=frozenset(
            {
                "prednisone",
                "prednisolone",
                "methylprednisolone",
                "dexamethasone",
                "hydrocortisone",
            }
        ),
        depletions=(
            Depletion(
                nutrient="Calcium",
                evidence="well_established",
                mechanism="Reduced intestinal calcium absorption and increased renal excretion; drives bone loss.",
                citation=_MOHN_2018,
            ),
            Depletion(
                nutrient="Vitamin D",
                evidence="moderate",
                mechanism="Altered vitamin D metabolism contributes to corticosteroid-induced bone loss; supplementation is standard.",
                citation=_MOHN_2018,
            ),
        ),
    ),
    _Rule(
        # combined oral contraceptives
        drugs=frozenset(
            {
                "ethinylestradiol",
                "ethinyl estradiol",
                "levonorgestrel",
                "norethindrone",
                "drospirenone",
                "norgestimate",
            }
        ),
        depletions=(
            Depletion(
                nutrient="Vitamin B6",
                evidence="moderate",
                mechanism="Combined oral contraceptives negatively affect B6 status.",
                citation=_WILSON_2011,
            ),
            Depletion(
                nutrient="Folate",
                evidence="limited",
                mechanism="Older high-estrogen formulations lowered folate; evidence for modern low-dose OCs is mixed.",
                citation=_WILSON_2011,
            ),
        ),
    ),
    _Rule(
        drugs=frozenset({"methotrexate"}),
        depletions=(
            Depletion(
                nutrient="Folate",
                evidence="well_established",
                mechanism="Dihydrofolate reductase inhibitor (folate antagonist); folic acid supplementation is standard of care.",
                citation=_MOHN_2018,
            ),
        ),
    ),
]

# Flatten to a lookup by drug name for O(1) exact matches.
_BY_DRUG: dict[str, tuple[Depletion, ...]] = {}
for _rule in _RULES:
    for _drug in _rule.drugs:
        _BY_DRUG[_drug] = _rule.depletions


def depletions_for(name: str) -> list[Depletion]:
    """Known depletions for a drug name (empty when we have no cited entry).

    Matches the normalized name exactly, then falls back to a whole-word token
    match so 'metformin 500mg' or 'metformin hcl' still resolves. Conservative
    by design: no fuzzy/class inference that could mislabel a drug.
    """

    cleaned = (name or "").strip().lower()
    if not cleaned:
        return []

    if cleaned in _BY_DRUG:
        return list(_BY_DRUG[cleaned])

    tokens = set(cleaned.replace("/", " ").replace("-", " ").split())
    for drug, depletions in _BY_DRUG.items():
        # only single-word drug keys are safe to match as a bare token
        if " " not in drug and drug in tokens:
            return list(depletions)

    return []
