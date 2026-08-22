"""Provider-neutral boundary for desktop capture provisioning.

Business logic depends on these types, not on a capture vendor's API objects.
Adapter-private identifiers and capabilities must be encrypted by the caller
before a transaction containing their durable mapping is committed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class CaptureProviderDisabled(RuntimeError):
    """Real provider creation is disabled by the deployment kill switch."""


class CaptureProviderRejected(RuntimeError):
    """The provider deterministically rejected a provisioning request."""


class CaptureProviderOutcomeUncertain(RuntimeError):
    """The request may have succeeded and must be reconciled, not replayed."""


class CaptureProviderResponseInvalid(CaptureProviderOutcomeUncertain):
    """The provider returned a response that cannot be safely persisted."""


@dataclass(frozen=True)
class CaptureProvisioning:
    """Minimal result needed to bind a provider resource to one segment."""

    capture_container_ref: str
    capture_capability: str
    status: str
    recording_ref: str | None = None


@dataclass(frozen=True)
class CaptureObservation:
    """A provider-side resource found during ambiguity reconciliation."""

    capture_container_ref: str
    capture_capability: str
    status: str
    recording_ref: str | None = None


class CaptureProvider(Protocol):
    """Replaceable native-capture provider adapter."""

    def create_capture(self, *, correlation_ref: str) -> CaptureProvisioning:
        """Create exactly one external capture container."""
        ...

    def find_captures(self, *, correlation_ref: str) -> tuple[CaptureObservation, ...]:
        """Find external resources for a prior, non-PHI correlation reference."""
        ...
