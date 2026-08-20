"""Fail-closed practice gate for the internal API.

ARCHITECTURE.md §8 and ADR 0016: a self-serve doctor gets no provider
capability until the practice's Cosmaneura Inc BAA is in force, they have
attested under it, and the practice is entitled (trial, subscription, or
exemption). Routes are gated by default so a future endpoint cannot ship
ungated; the allowlist below is the onboarding surface that must work
before the BAA exists.
"""

from fastapi import HTTPException, Request, status

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
    # enforces its own practice/BAA rules
    "complete_onboarding",
}


def require_practice_in_force(request: Request) -> None:
    route = request.scope.get("route")
    if route is not None and route.name in EXEMPT_ROUTE_NAMES:
        return

    doctor: Doctor = request.state.doctor
    if doctor.role == DoctorRole.admin:
        return

    member = PracticeMember.get(doctor_id=doctor.id)

    if member is None:
        # Doctors onboarded before practices existed are grandfathered until
        # the pilot backfill gives them one (ADR 0016).
        if doctor.onboarding_completed_at is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Name or join a practice and accept the BAA before using Cosmas.",
            )
        return

    practice = Practice.get(id=member.practice_id)
    if (
        practice is None
        or practice.baa_accepted_at is None
        or member.attested_at is None
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The Cosmaneura Inc BAA must be in force before Cosmas will process PHI.",
        )

    if not practice.entitled():
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="This practice's free trial has ended. Add billing to continue using Cosmas.",
        )
