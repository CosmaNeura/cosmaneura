"""
Per-environment email allowlist for the Session Prep feature.

Same pattern as the Zoom bot allowlist in app/services/zoom_webhooks.py:
staging and production each have an explicit cohort, dev/test are ungated.
The frontend mirrors this list in web/app/utils/session-prep-feature.ts —
keep them in sync.
"""

from app.environments import is_production, is_staging

# Pilot cohort (internal + external close-hold users), staging first.
STAGING_SESSION_PREP_EMAILS = frozenset(
    {
        "john@cosmaneura.com",
        "dev@cosmaneura.com",
        "greg@mycatholicdoctor.com",
        "kathleen@mycatholicdoctor.com",
    }
)

# Intentionally empty until the staging pilot validates the feature.
PROD_SESSION_PREP_EMAILS: frozenset[str] = frozenset()


def session_prep_enabled_for(email: str | None) -> bool:
    if not email:
        return False

    normalized = email.lower()

    if is_staging():
        return normalized in STAGING_SESSION_PREP_EMAILS

    if is_production():
        return normalized in PROD_SESSION_PREP_EMAILS

    # dev/test: no gating
    return True
