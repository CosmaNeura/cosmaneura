"""Shared current-session guard for ordinary and composed provider exchange."""

import app.configuration.session_prep as session_prep_configuration
from app.configuration.authority import AuthorityKernelSettings
from app.services.provider_record_authority import lock_provider_record_owner
from app.services.superadmin_capability import (
    configured_superadmin_capability_environment,
)
from app.services.view_as import context_token_exchange_blocked_by_view_as

from activemodel.session_manager import get_session


def provider_exchange_blocked(
    *,
    issuer: str,
    subject: str,
    clerk_session_id: str,
    requested_surface: str | None,
    record_ref: str | None,
    settings: AuthorityKernelSettings,
) -> bool:
    """Preserve the exchange route's view-as and Practice-before-Doctor locks."""
    if not settings.enabled:
        return False
    environment = configured_superadmin_capability_environment()
    with get_session() as session:
        if (
            requested_surface == "provider_assistant"
            and record_ref is not None
            and session_prep_configuration.SESSION_PREP_PROVIDER_SETTINGS.allows(
                subject
            )
        ):
            lock_provider_record_owner(session=session, clerk_subject=subject)
        return context_token_exchange_blocked_by_view_as(
            session,
            issuer=issuer,
            subject=subject,
            clerk_session_id=clerk_session_id,
            environment=environment,
        )
