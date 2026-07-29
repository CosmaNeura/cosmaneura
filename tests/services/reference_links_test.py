from app.models.patient_medication import MedicationKind
from app.services.reference_links import (
    PROVIDER_RESOURCE_DIRECTORY,
    resource_links_for,
)


def test_supplement_gets_ods_link_no_natmed_when_unsubscribed():
    links = resource_links_for(
        "fish oil", MedicationKind.supplement, has_natmed_subscription=False
    )
    sources = {link.source for link in links}
    assert "NIH ODS" in sources
    assert "TRC Healthcare" not in sources  # no subscription → no NatMed links
    assert all(not link.requires_subscription for link in links)


def test_prescription_gets_fda_and_medlineplus():
    links = resource_links_for(
        "metformin", MedicationKind.prescription, has_natmed_subscription=False
    )
    sources = {link.source for link in links}
    assert "FDA" in sources
    assert "NIH MedlinePlus" in sources


def test_natmed_links_shown_when_subscribed():
    links = resource_links_for(
        "metformin", MedicationKind.prescription, has_natmed_subscription=True
    )
    natmed = [link for link in links if link.source == "TRC Healthcare"]
    assert natmed
    assert all(link.requires_subscription for link in natmed)
    # the medication name is url-encoded into the lookup deep link
    assert any("metformin" in link.url for link in natmed)


def test_name_is_url_encoded():
    links = resource_links_for(
        "vitamin d", MedicationKind.supplement, has_natmed_subscription=True
    )
    assert any("vitamin+d" in link.url for link in links)


def test_directory_is_non_empty_and_links_out():
    assert PROVIDER_RESOURCE_DIRECTORY
    assert all(link.url.startswith("https://") for link in PROVIDER_RESOURCE_DIRECTORY)
