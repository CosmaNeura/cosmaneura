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

from app.services.organization_access import member_access_in_force

from app.models.doctor import Doctor, DoctorRole
from app.models.practice import Practice, PracticeMember

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
    "organization_access_opportunity",
    "claim_organization_access",
    "baa_current",
    "practice_baa_acceptances",
    # enforces its own practice/BAA rules
    "complete_onboarding",
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
    if doctor.role == DoctorRole.admin:
        return

    member = PracticeMember.get(doctor_id=doctor.id)
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Name or join a practice and accept the BAA before using Cosmas.",
        )

    practice = Practice.get(id=member.practice_id)
    if (
        practice is None
        or not practice.baa_in_force()
        or not member_access_in_force(member)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The Cosmaneura Inc BAA must be in force before Cosmas will process PHI.",
        )

    if route_name in ENTITLEMENT_EXEMPT_ROUTE_NAMES:
        return

    if not practice.entitled():
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="This practice's free trial has ended. Add billing to continue using Cosmas.",
        )
