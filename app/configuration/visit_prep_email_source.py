"""Explicit deployment qualification of Core's own recorded email choices.

This is not consent, a BAA or identity proof. Leave it false until the displayed
disclosure, bearer attribution and caregiver policy have been qualified. It
does not authorize a first booking invitation or any SMS consent source.
"""

from collections.abc import Mapping

from decouple import config

from app.configuration.visit_prep import _parse_bool


def load_patient_email_source_qualified(
    values: Mapping[str, object] | None = None,
) -> bool:
    name = "VISIT_PREP_PATIENT_EMAIL_SOURCE_QUALIFIED"
    raw = (
        values.get(name, "false")
        if values is not None
        else config(name, default="false")
    )
    try:
        return _parse_bool(raw)
    except (TypeError, ValueError):
        return False


PATIENT_EMAIL_SOURCE_QUALIFIED = load_patient_email_source_qualified()
