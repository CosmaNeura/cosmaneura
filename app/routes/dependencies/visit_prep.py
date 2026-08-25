"""FastAPI dependencies for the dark Visit Prep release-control seam."""

from fastapi import HTTPException, Request, status

from app.services.visit_prep_access import (
    VisitPrepAccessDecision,
    resolve_visit_prep_access,
)

from app.models.doctor import Doctor

_NOT_FOUND_DETAIL = "Not found"
_VIEW_AS_READ_ONLY_DETAIL = "View-as is read-only. Exit view-as before making changes."


def _decision_for_request(request: Request) -> VisitPrepAccessDecision:
    doctor: Doctor = request.state.doctor
    return resolve_visit_prep_access(
        doctor,
        view_as_active=getattr(request.state, "view_as", None) is not None,
    )


def require_visit_prep_read(request: Request) -> VisitPrepAccessDecision:
    """Expose Visit Prep only when the full server-side eligibility gate passes."""

    decision = _decision_for_request(request)
    if not decision.eligible:
        # Do not reveal whether the feature, cohort, practice, BAA, or billing
        # check caused the route to be unavailable.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND_DETAIL,
        )
    return decision


def require_visit_prep_write(request: Request) -> VisitPrepAccessDecision:
    """Require eligibility plus a real physician (never view-as) mutation."""

    decision = require_visit_prep_read(request)
    if not decision.can_manage:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_VIEW_AS_READ_ONLY_DETAIL,
        )
    return decision
