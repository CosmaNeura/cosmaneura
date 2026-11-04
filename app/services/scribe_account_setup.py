"""Closed setup state; never turns a tombstone or broken mapping into onboarding."""

from dataclasses import replace
from typing import Literal

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_activation import SCRIBE_ACTIVATION_SETTINGS
from app.services import authority
from app.services.scribe_activation import (
    ActivationDenied,
    ActivationSetupRequired,
    activation_provider,
)

from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor import Doctor
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import and_, or_
from sqlmodel import Session, col, select


class ScribeAccountSetupRequired(ValueError):
    def __init__(self, setup: Literal["practice", "activation"]):
        self.setup = setup
        super().__init__("scribe_account_setup_required")


def require_account_setup(
    session: Session, *, issuer: str, subject: str, auth: AuthorityKernelSettings
) -> None:
    # This runs only after ordinary discovery fails. Any historical link means
    # the account needs denial/repair, not automatic replacement or adoption.
    links = session.exec(
        select(ScribeCaptureProviderLink)
        .join(Doctor, col(Doctor.id) == col(ScribeCaptureProviderLink.doctor_id))
        .outerjoin(
            AuthorityActorExternalIdentity,
            col(AuthorityActorExternalIdentity.id)
            == col(ScribeCaptureProviderLink.source_identity_id),
        )
        .where(
            or_(
                col(Doctor.clerk_id) == subject,
                and_(
                    col(AuthorityActorExternalIdentity.issuer) == issuer,
                    col(AuthorityActorExternalIdentity.subject) == subject,
                ),
            )
        )
    ).all()
    if links:
        raise authority.AuthorityRequestDeniedError
    try:
        activation_provider(
            session,
            issuer=issuer,
            subject=subject,
            settings=replace(SCRIBE_ACTIVATION_SETTINGS, authority=auth),
            lock=True,
        )
    except ActivationSetupRequired:
        raise ScribeAccountSetupRequired("practice") from None
    except ActivationDenied:
        raise authority.AuthorityRequestDeniedError from None
    raise ScribeAccountSetupRequired("activation")
