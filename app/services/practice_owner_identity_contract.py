"""Normalized, read-only identity evidence for explicit owner recovery."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class OwnerIdentityAbsence:
    """A current exact-subject not-found result from the configured adapter.

    A missing/invalid identity or a failed request is not absence evidence.
    The adapter binds the result to its runtime and configured issuer.
    """

    subject: str
    environment: str
    issuer_sha256: str
    evidence_mode: Literal["clerk_404_resource_not_found"] = (
        "clerk_404_resource_not_found"
    )
