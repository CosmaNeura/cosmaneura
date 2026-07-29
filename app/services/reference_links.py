"""
Deep links into clinical reference resources for a medication or supplement.

We never copy or cache these providers' content — we build URLs the provider
clicks and authenticates against on the far side. This keeps us clear of their
terms of service and copyright while making the connection one click away.

- NatMed / NatMed Pro (TRC Healthcare): subscription. Shown only when the
  provider records that they have a subscription; we deep-link into search /
  the interaction checker so they land in their own paid session.
- openFDA drug labels: public domain (U.S. FDA).
- NIH ODS supplement fact sheets + DSLD: public domain (NIH).

Only drug/ingredient NAMES go into these URLs — never patient identifiers.
"""

from urllib.parse import quote_plus

from pydantic import BaseModel

from app.models.patient_medication import MedicationKind


class ResourceLink(BaseModel, extra="forbid"):
    label: str
    url: str
    source: str
    requires_subscription: bool = False


# Provider-level resource directory: the top-level references the pilot user
# works through by hand, so they are one click away even with no medications
# selected. Attribution/label text is ours; we link, we do not embed.
PROVIDER_RESOURCE_DIRECTORY: list[ResourceLink] = [
    ResourceLink(
        label="NatMed Pro — Natural Medicines database",
        url="https://naturalmedicines.therapeuticresearch.com/",
        source="TRC Healthcare",
        requires_subscription=True,
    ),
    ResourceLink(
        label="Nutrition practice laws by state",
        url="https://holisticcouncil.org/nutrition-laws/",
        source="Holistic Council",
    ),
    ResourceLink(
        label="Functional-medicine lab reference library",
        url="https://lamkinclinic.com/lab-reference-library/",
        source="Lamkin Clinic",
    ),
    ResourceLink(
        label="NIH Office of Dietary Supplements — fact sheets",
        url="https://ods.od.nih.gov/factsheets/list-all/",
        source="NIH ODS",
    ),
]


def _natmed_links(name: str) -> list[ResourceLink]:
    q = quote_plus(name)
    return [
        ResourceLink(
            label=f"NatMed: look up “{name}”",
            url=f"https://naturalmedicines.therapeuticresearch.com/databases/search.aspx?q={q}",
            source="TRC Healthcare",
            requires_subscription=True,
        ),
        ResourceLink(
            label="NatMed: interaction checker",
            url="https://naturalmedicines.therapeuticresearch.com/tools/interaction-checker.aspx",
            source="TRC Healthcare",
            requires_subscription=True,
        ),
    ]


def _public_links(name: str, kind: MedicationKind) -> list[ResourceLink]:
    q = quote_plus(name)
    links: list[ResourceLink] = []

    if kind == MedicationKind.supplement:
        links.append(
            ResourceLink(
                label=f"NIH ODS / DSLD: “{name}”",
                url=f"https://dsld.od.nih.gov/search?query={q}",
                source="NIH ODS",
            )
        )
    else:
        links.append(
            ResourceLink(
                label=f"openFDA drug label: “{name}”",
                url=f"https://labels.fda.gov/?search={q}",
                source="FDA",
            )
        )
        links.append(
            ResourceLink(
                label=f"MedlinePlus: “{name}”",
                url=f"https://medlineplus.gov/search/?query={q}",
                source="NIH MedlinePlus",
            )
        )

    return links


def resource_links_for(
    name: str, kind: MedicationKind, *, has_natmed_subscription: bool
) -> list[ResourceLink]:
    "reference deep links for one medication/supplement, name-only, no PHI"

    links: list[ResourceLink] = []
    if has_natmed_subscription:
        links.extend(_natmed_links(name))
    links.extend(_public_links(name, kind))
    return links
