import re
from dataclasses import dataclass

from decouple import config

CANONICAL_CLERK_ISSUER_PATTERN = (
    r"^https://[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_CANONICAL_CLERK_ISSUER = re.compile(CANONICAL_CLERK_ISSUER_PATTERN)


@dataclass(frozen=True)
class AuthorityKernelSettings:
    enabled: bool
    token_ttl_seconds: int
    token_refresh_after_seconds: int
    session_absolute_ttl_seconds: int = 28_800
    clerk_issuer: str = ""

    def has_valid_clerk_issuer(self) -> bool:
        """Require one canonical HTTPS DNS origin, never a URL variant."""

        return _CANONICAL_CLERK_ISSUER.fullmatch(self.clerk_issuer) is not None

    def is_complete(self) -> bool:
        return (
            self.token_ttl_seconds >= 300
            and self.token_refresh_after_seconds >= 60
            and self.token_refresh_after_seconds < self.token_ttl_seconds
            and self.session_absolute_ttl_seconds >= self.token_ttl_seconds
            and self.has_valid_clerk_issuer()
        )


AUTHORITY_KERNEL_SETTINGS = AuthorityKernelSettings(
    enabled=config("AUTHORITY_KERNEL_ENABLED", default=False, cast=bool),
    token_ttl_seconds=config(
        "MOUNT_CONTEXT_TOKEN_TTL_SECONDS",
        default=900,
        cast=int,
    ),
    token_refresh_after_seconds=config(
        "MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS",
        default=300,
        cast=int,
    ),
    session_absolute_ttl_seconds=config(
        "MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS",
        default=28_800,
        cast=int,
    ),
    clerk_issuer=config("AUTHORITY_CLERK_ISSUER", default="", cast=str),
)
