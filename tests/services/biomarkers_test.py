from app.services.biomarkers import (
    BY_KEY,
    default_unit,
    display_name,
    normalize_biomarker,
)


def test_normalizes_aliases_to_canonical_key():
    assert normalize_biomarker("Vitamin D") == "vitamin_d_25oh"
    assert normalize_biomarker("25-OH Vitamin D") == "vitamin_d_25oh"
    assert normalize_biomarker("TSH") == "tsh"
    assert normalize_biomarker("HbA1c") == "hba1c"
    assert normalize_biomarker("ferritin") == "ferritin"


def test_canonical_key_maps_to_itself():
    assert normalize_biomarker("vitamin_b12") == "vitamin_b12"


def test_unknown_returns_none():
    assert normalize_biomarker("unobtanium level") is None
    assert normalize_biomarker("") is None


def test_display_name_and_unit():
    assert display_name("tsh") == "TSH"
    assert default_unit("tsh") == "mIU/L"
    assert display_name("unknown_key") == "unknown_key"
    assert default_unit("unknown_key") is None


def test_registry_is_internally_consistent():
    for key, marker in BY_KEY.items():
        assert marker.key == key
        assert marker.name
        assert marker.unit
        # every alias resolves back to this key
        for alias in marker.aliases:
            assert normalize_biomarker(alias) == key
