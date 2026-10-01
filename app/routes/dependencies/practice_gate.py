"""Fail-closed practice gate for the internal API.

CLAUDE_BAA_IMPLEMENTATION guardrail 5 and ARCHITECTURE.md §8: no PHI-bearing
feature is reachable by an account without a recorded BAA acceptance for its
practice (or the negotiated-BAA-on-file fallback), an authorized membership,
and entitlement (trial, subscription, or exemption — ADR 0017). Routes are
gated by default so a future endpoint cannot ship ungated; the allowlist is
the onboarding surface that must work before the BAA exists, and the
internal-admin surface is governed by ``require_admin`` rather than by any
customer's BAA.

Doctors predating practices are not exempt: on their next login the frontend
routes them through practice creation and BAA acceptance (or an internal
admin sets ``negotiated_baa_on_file``).
"""

from fastapi import HTTPException, Request, status

from app.services.doctor_access import resolve_doctor_access

from app.models.doctor import Doctor

EXEMPT_ROUTE_NAMES = {
    # settings back the onboarding wizard and hold no patient data
    "settings_data",
    "update_settings_data",
    # the practice/BAA flow itself
    "practice_status",
    "create_practice",
    "accept_practice_baa",
    "lookup_practice",
    "join_practice",
    # an invitee has no membership until acceptance; both enforce their own
    # identity, invitation and BAA checks
    "inspect_practice_invitation",
    "accept_practice_invitation",
    "organization_access_opportunity",
    "claim_organization_access",
    "baa_current",
    "practice_baa_acceptances",
    # support view-as control plane; begin/targets enforce the independent
    # environment-scoped capability and status/end must remain reachable to
    # clear stale sessions even when the actor has no customer practice
    "view_as_status",
    "view_as_targets",
    "begin_view_as",
    "end_view_as",
    # enforces its own practice/BAA rules
    "complete_onboarding",
    # Visit Prep applies its stronger practice/BAA/entitlement/cohort decision
    # and a non-disclosing denial in every route dependency.
    "visit_prep_question_set_list",
    "visit_prep_question_set_version",
    "visit_prep_question_set_create",
    "visit_prep_question_set_replace",
    "visit_prep_question_set_successor",
    "visit_prep_question_set_publish",
    "visit_prep_question_set_default",
}

# These routes still require a real practice, an in-force BAA, and an authorized
# member. Only the entitlement check is skipped so a lapsed owner can pay their
# way back in without creating a broader practice-gate bypass.
ENTITLEMENT_EXEMPT_ROUTE_NAMES = {
    "create_billing_checkout",
    "create_billing_portal",
}

ADMIN_PATH_PREFIX = "/internal/v1/admin"


def require_practice_in_force(request: Request) -> None:
    route = request.scope.get("route")
    route_name = route.name if route is not None else None
    if route_name in EXEMPT_ROUTE_NAMES:
        return

    # the internal-admin surface is Cosmaneura staff tooling: require_admin
    # (401 for non-admins) governs it, not a customer practice's BAA
    if request.url.path.startswith(ADMIN_PATH_PREFIX):
        return

    doctor: Doctor = request.state.doctor
    decision = resolve_doctor_access(
        doctor,
        require_entitlement=route_name not in ENTITLEMENT_EXEMPT_ROUTE_NAMES,
    )
    if decision.allowed:
        return

    if decision.denial_reason == "entitlement_not_in_force":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="This practice's free trial has ended. Add billing to continue using Cosmas.",
        )

    if decision.denial_reason == "practice_membership_required":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Name or join a practice and accept the BAA before using Cosmas.",
        )

    if decision.denial_reason == "practice_membership_inactive":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your practice owner ended your access to Cosmas.",
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="The Cosmaneura Inc BAA must be in force before Cosmas will process PHI.",
    )
