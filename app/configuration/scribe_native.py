"""Default-off workload and authority settings for the native Scribe facade only."""

from dataclasses import replace
from uuid import UUID

from decouple import config

from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.configuration.service_auth import (
    AGENT_API_AUTH_SETTINGS,
    SCRIBE_CAPTURE_AUTH_SETTINGS,
    SOVEREIGN_TOOL_AUTH_SETTINGS,
    EntraAgentAuthSettings,
)


def native_workload_settings(
    *, enabled: bool, tenant_id: str, audience: str, client_id: str
) -> EntraAgentAuthSettings:
    settings = EntraAgentAuthSettings(
        enabled=enabled,
        tenant_id=tenant_id,
        audience=audience,
        sovereign_client_ids=frozenset({client_id}) if client_id else frozenset(),
    )
    if enabled:
        try:
            identifiers = [UUID(value) for value in (tenant_id, audience, client_id)]
        except (ValueError, TypeError, AttributeError):
            raise ValueError("scribe_native_workload_configuration_invalid") from None
        if (
            any(not value.int for value in identifiers)
            or [str(value) for value in identifiers] != [tenant_id, audience, client_id]
            or client_id in SOVEREIGN_TOOL_AUTH_SETTINGS.allowed_client_ids
            or not AUTHORITY_KERNEL_SETTINGS.is_complete()
        ):
            raise ValueError("scribe_native_workload_configuration_invalid")
    return settings


SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS = native_workload_settings(
    enabled=config("SCRIBE_NATIVE_WORKLOAD_ENABLED", default=False, cast=bool),
    tenant_id=config("ENTRA_TENANT_ID", default="", cast=str),
    audience=config("SCRIBE_NATIVE_WORKLOAD_AUDIENCE", default="", cast=str),
    client_id=config("SCRIBE_NATIVE_WORKLOAD_CLIENT_ID", default="", cast=str),
)

# Retain the existing Agent API/capture policies exactly while this seam is off.
# The native facade alone selects its singleton caller when explicitly enabled.
SCRIBE_NATIVE_AGENT_AUTH_SETTINGS = (
    SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS
    if SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS.enabled
    else AGENT_API_AUTH_SETTINGS
)
SCRIBE_NATIVE_CAPTURE_AUTH_SETTINGS = (
    SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS
    if SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS.enabled
    else SCRIBE_CAPTURE_AUTH_SETTINGS
)

SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED = config(
    "SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED", default=False, cast=bool
)
if (
    SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED
    and not SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS.enabled
):
    raise ValueError("scribe_native_provider_context_requires_native_workload")


def native_authority_settings(
    settings: AuthorityKernelSettings,
) -> AuthorityKernelSettings:
    # Enable authority only inside the native facade. Session Prep and other
    # consumers retain their original global Authority Kernel setting.
    return (
        replace(settings, enabled=True)
        if SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS.enabled
        else settings
    )
