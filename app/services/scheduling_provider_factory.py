"""Builds the right SchedulingProvider adapter for a doctor + source.

Shared by sync_scheduling_provider.py and backfill_scheduling_roster.py.
Lives in services/, not scheduling_provider.py itself, because it must
import both concrete adapters — scheduling_provider.py stays adapter-free
so the adapters can import it without a circular dependency.
"""

from app.services.scheduling_acuity import (
    AcuityIntegrationInvalid,
    AcuitySchedulingProvider,
    get_acuity_integration,
)
from app.services.scheduling_google_calendar import (
    GoogleCalendarIntegrationInvalid,
    GoogleCalendarSchedulingProvider,
    get_google_calendar_integration,
)
from app.services.scheduling_provider import SchedulingProvider

from app.models.doctor import Doctor
from app.models.patient_external_identity import SchedulingProviderSource

SCHEDULING_INTEGRATION_INVALID_EXCEPTIONS = (
    AcuityIntegrationInvalid,
    GoogleCalendarIntegrationInvalid,
)


def build_scheduling_provider(
    doctor: Doctor, source: SchedulingProviderSource
) -> SchedulingProvider | None:
    """Return a connected adapter for this doctor, or None if not connected."""
    if source == SchedulingProviderSource.acuity:
        if get_acuity_integration(doctor) is None:
            return None
        return AcuitySchedulingProvider(doctor)
    if source == SchedulingProviderSource.google_calendar:
        if get_google_calendar_integration(doctor) is None:
            return None
        return GoogleCalendarSchedulingProvider(doctor)
    return None
