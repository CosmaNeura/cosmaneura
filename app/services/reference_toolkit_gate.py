"""
Per-environment email allowlist for the provider reference toolkit
(medication registry, resource panel, and downstream interaction/lab tools).

Same pattern as app/services/zoom_webhooks.py: staging and production each have
an explicit cohort, dev/test are ungated. The frontend mirrors this list in
web/app/utils/reference-toolkit-feature.ts — keep them in sync.

This list is deliberately NOT the shared beta allowlist in
web/app/utils/beta-access.ts. That one already carries a production cohort for
Zoom and the engagement badge; borrowing it here would show the toolkit UI to
production users while this module still refuses every request.
"""

from app.environments import is_production, is_staging

# Pilot cohort, staging first. John is the staging tester for now; the
# nutritionist pilot user is deliberately NOT here yet and is added only once
# the feature has been validated in staging.
STAGING_REFERENCE_TOOLKIT_EMAILS = frozenset(
    {
        "john@cosmaneura.com",
        "dev@cosmaneura.com",
        "greg@mycatholicdoctor.com",
        "kathleen@mycatholicdoctor.com",
    }
)

# Empty until the staging pilot validates the feature.
PROD_REFERENCE_TOOLKIT_EMAILS: frozenset[str] = frozenset()


def reference_toolkit_enabled_for(email: str | None) -> bool:
    if not email:
        return False

    normalized = email.lower()

    if is_staging():
        return normalized in STAGING_REFERENCE_TOOLKIT_EMAILS

    if is_production():
        return normalized in PROD_REFERENCE_TOOLKIT_EMAILS

    # dev/test: no gating
    return True
