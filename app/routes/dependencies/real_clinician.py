"""Authorization context for capture actions that may not use admin delegation."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from activemodel.types import TypeIDType
from app.models.doctor import Doctor, DoctorRole
from app.models.practice import PracticeMember


@dataclass(frozen=True)
class RealClinicianContext:
    actor_id: TypeIDType
    practice_id: TypeIDType
    actor_binding: str


def require_real_clinician(request: Request) -> RealClinicianContext:
    """Reject administrator and login-as identities for desktop capture.

    The ordinary internal-router dependencies authenticate Clerk, load the
    effective doctor, apply login-as, and enforce the practice gate first.
    Capture then applies this stricter boundary: the Clerk subject must be the
    same non-admin clinician who owns the appointment.
    """

    doctor = getattr(request.state, "doctor", None)
    auth_state = getattr(request.state, "auth_state", None)
    payload = getattr(auth_state, "payload", None)
    actor_binding = payload.get("sub") if isinstance(payload, dict) else None
    delegated_admin = getattr(request.state, "admin_user", None)

    if (
        not isinstance(doctor, Doctor)
        or not isinstance(actor_binding, str)
        or not actor_binding
        or doctor.role != DoctorRole.normal
        or doctor.clerk_id != actor_binding
        or delegated_admin is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Desktop capture is unavailable for this account.",
        )

    membership = PracticeMember.get(doctor_id=doctor.id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Desktop capture is unavailable for this account.",
        )

    return RealClinicianContext(
        actor_id=doctor.id,
        practice_id=membership.practice_id,
        actor_binding=actor_binding,
    )
