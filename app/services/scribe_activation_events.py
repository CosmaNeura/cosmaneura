"""Best-effort activation after the domain event has already committed.

Activation failure cannot roll back onboarding, enrollment or billing. Each
provider uses a new transaction; nested legacy authorization reads share it.
No identity service, Stripe or patient-data I/O occurs here.
"""

from typing import Any

from app import log
from app.configuration.scribe_activation import (
    SCRIBE_ACTIVATION_SETTINGS,
    ScribeActivationSettings,
)
from app.services.scribe_activation import (
    ActivationDenied,
    ActivationSetupRequired,
    ActivationTrigger,
    activate_scribe,
)

from activemodel.session_manager import _session_context, get_session
from app.models.doctor import Doctor
from app.models.practice import PracticeMember
from sqlmodel import Session, col, select


def activate_scribe_after_commit(
    *,
    trigger: ActivationTrigger,
    doctor_ref: Any = None,
    practice_ref: Any = None,
    bind=None,
    settings: ScribeActivationSettings = SCRIBE_ACTIVATION_SETTINGS,
) -> None:
    if not settings.enabled:
        return
    if (doctor_ref is None) == (practice_ref is None):
        log.warning("scribe activation event scope unavailable", trigger=trigger)
        return
    try:
        if bind is None:
            with get_session() as current:
                bind = current.get_bind()
        with Session(bind) as lookup:
            query = select(col(Doctor.clerk_id)).where(col(Doctor.deleted_at).is_(None))
            if doctor_ref is not None:
                query = query.where(Doctor.id == doctor_ref)
            else:
                if not settings.allows_practice(str(practice_ref)):
                    return
                query = query.join(
                    PracticeMember, col(PracticeMember.doctor_id) == Doctor.id
                ).where(PracticeMember.practice_id == practice_ref)
            subjects = list(lookup.exec(query).all())
        for subject in subjects:
            with Session(bind, expire_on_commit=False) as session:
                token = _session_context.set(session)
                try:
                    activate_scribe(
                        session,
                        issuer=settings.authority.clerk_issuer,
                        subject=subject,
                        trigger=trigger,
                        settings=settings,
                    )
                except (ActivationSetupRequired, ActivationDenied):
                    session.rollback()
                    log.info("scribe activation deferred", trigger=trigger)
                except Exception:
                    session.rollback()
                    # Exception content can carry submitted identity/clinical data.
                    log.warning("scribe activation unavailable", trigger=trigger)
                finally:
                    _session_context.reset(token)
    except Exception:
        log.warning("scribe activation event unavailable", trigger=trigger)
