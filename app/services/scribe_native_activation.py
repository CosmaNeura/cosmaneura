"""One deadline-bounded native activation, under the caller's verified identity."""

from dataclasses import replace
from typing import cast
from uuid import UUID

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_activation import ScribeActivationSettings
from app.services.companion_records import (
    RecordBudget,
    check_deadline,
    transaction_budget,
)
from app.services.scribe_activation import ActivationDenied, activate_scribe
from app.services.scribe_activation_contract import (
    ActivationRequest,
    ActivationResponse,
)

from activemodel.session_manager import global_session
from sqlmodel import Session


def execute_activation(
    *,
    issuer: str,
    subject: str,
    session_id: str,
    request: ActivationRequest,
    request_key: UUID,
    settings: ScribeActivationSettings,
    authority_settings: AuthorityKernelSettings,
    deadline: RecordBudget,
) -> ActivationResponse:
    if not session_id or not isinstance(request_key, UUID) or not request_key.int:
        raise ActivationDenied("scribe_activation_denied")
    check_deadline(deadline)

    def before_commit() -> None:
        check_deadline(deadline)

    with global_session() as managed:
        session = cast(Session, managed)
        try:
            with transaction_budget(session, deadline):
                activate_scribe(
                    session,
                    issuer=issuer,
                    subject=subject,
                    trigger="native-client",
                    settings=replace(settings, authority=authority_settings),
                    request_key=request_key,
                    before_commit=before_commit,
                )
                check_deadline(deadline)
                return ActivationResponse(request_nonce=request.request_nonce)
        finally:
            session.rollback()
