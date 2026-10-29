"""PHI-free Session Prep discovery; never an execution authorization."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionPrepDiscovery:
    eligible: bool
    can_review_sources: bool


def resolve_session_prep_discovery(
    *,
    selected_for_beta: bool,
    provider_access_allowed: bool,
    view_as_active: bool,
) -> SessionPrepDiscovery:
    """Project account-level availability for provider-facing UI.

    ``provider_access_allowed`` is the same current Doctor/practice/BAA/
    entitlement decision used by the source-review route's practice gate.
    Record and note ownership remain request-specific and are still resolved
    by every execution route.
    """

    return SessionPrepDiscovery(
        eligible=selected_for_beta,
        can_review_sources=(
            selected_for_beta and provider_access_allowed and not view_as_active
        ),
    )
