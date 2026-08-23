from app.services.nutrient_depletion import (
    _RULES,
    depletions_for,
)

_VALID_EVIDENCE = {"well_established", "moderate", "limited"}


def test_metformin_b12():
    depletions = depletions_for("metformin")
    nutrients = {d.nutrient for d in depletions}
    assert "Vitamin B12" in nutrients
    b12 = next(d for d in depletions if d.nutrient == "Vitamin B12")
    assert b12.evidence == "well_established"
    assert "doi.org/10.1136/bmj.c2181" in b12.citation.url


def test_matches_normalized_name_with_dose_and_salt():
    assert depletions_for("Metformin 500mg")
    assert depletions_for("metformin hcl")
    assert depletions_for("METFORMIN")


def test_ppi_class_members_all_match():
    for drug in ["omeprazole", "esomeprazole", "pantoprazole"]:
        nutrients = {d.nutrient for d in depletions_for(drug)}
        assert "Magnesium" in nutrients
        assert "Vitamin B12" in nutrients


def test_statin_coq10_is_moderate():
    depletions = depletions_for("atorvastatin")
    coq10 = next(d for d in depletions if d.nutrient == "Coenzyme Q10")
    assert coq10.evidence == "moderate"


def test_loop_diuretic_potassium_magnesium_thiamine():
    nutrients = {d.nutrient for d in depletions_for("furosemide")}
    assert {"Potassium", "Magnesium", "Thiamine (B1)"} <= nutrients


def test_oral_contraceptive_folate_is_limited_evidence():
    depletions = depletions_for("ethinylestradiol")
    folate = next(d for d in depletions if d.nutrient == "Folate")
    # honest tier: modern low-dose OC folate evidence is mixed
    assert folate.evidence == "limited"


def test_unknown_drug_returns_empty():
    assert depletions_for("acetaminophen") == []
    assert depletions_for("") == []


def test_no_partial_substring_false_positive():
    # "formin" must NOT match "metformin"
    assert depletions_for("formin") == []


def test_every_rule_has_valid_evidence_and_citation():
    for rule in _RULES:
        assert rule.depletions
        for d in rule.depletions:
            assert d.evidence in _VALID_EVIDENCE
            assert d.nutrient
            assert d.mechanism
            assert d.citation.reference
            assert d.citation.url.startswith("https://")
