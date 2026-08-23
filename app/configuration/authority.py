from dataclasses import dataclass
from urllib.parse import urlparse

from decouple import config


@dataclass(frozen=True)
class AuthorityKernelSettings:
    enabled: bool
    token_ttl_seconds: int
    token_refresh_after_seconds: int
    session_absolute_ttl_seconds: int = 28_800
    clerk_issuer: str = ""

    def has_valid_clerk_issuer(self) -> bool:
        parsed = urlparse(self.clerk_issuer)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.params
            and not parsed.query
            and not parsed.fragment
            and self.clerk_issuer == self.clerk_issuer.rstrip("/")
        )

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
