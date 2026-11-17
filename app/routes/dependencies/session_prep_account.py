"""Closed HTTP surface where Session Prep follows active-account policy."""

from fastapi import Request

# Settings projects availability without reading PHI. The remaining exact
# route/method pairs either read only the authenticated Doctor's records or
# apply an action whose endpoint independently verifies Doctor-owned lineage.
# New routes are excluded until explicitly reviewed.
SESSION_PREP_ACTIVE_ACCOUNT_ROUTE_METHODS = {
    "view_as_status": frozenset({"GET"}),
    "end_view_as": frozenset({"DELETE"}),
    "settings_data": frozenset({"GET"}),
    "note_list": frozenset({"GET"}),
    "note_detail": frozenset({"GET"}),
    "retained_session_prep_citation": frozenset({"GET"}),
    "correct_session_prep_source_note": frozenset({"POST"}),
    "review_note_for_session_prep": frozenset({"POST"}),
}


def is_session_prep_active_account_route(
    request: Request,
    *,
    rollout_enabled: bool,
) -> bool:
    """Match only one reviewed route/method while the global switch is on."""

    route = request.scope.get("route")
    route_name = route.name if route is not None else None
    methods = SESSION_PREP_ACTIVE_ACCOUNT_ROUTE_METHODS.get(route_name or "")
    return bool(rollout_enabled and methods is not None and request.method in methods)
