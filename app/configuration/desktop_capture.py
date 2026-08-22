"""Fail-closed configuration for the provider-neutral desktop capture API."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from decouple import config

MAX_LAUNCH_TICKET_TTL_SECONDS = 2 * 60
MAX_AUTHORIZATION_CODE_TTL_SECONDS = 5 * 60
MAX_DESKTOP_TOKEN_TTL_SECONDS = 6 * 60 * 60


def _redirect_uris(value: str) -> tuple[str, ...]:
    uris = tuple(item.strip() for item in value.split(",") if item.strip())
    if len(set(uris)) != len(uris):
        raise ValueError("desktop capture redirect URIs must be unique")
    for uri in uris:
        parsed = urlsplit(uri)
        if (
            not parsed.scheme
            or parsed.scheme.lower() in {"data", "file", "javascript"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.query
        ):
            raise ValueError("invalid desktop capture redirect URI")
    return uris


@dataclass(frozen=True)
class DesktopCaptureSettings:
    """Capture launch settings; every behavior-changing switch is false by default."""

    enabled: bool
    provider_create_enabled: bool
    recovery_enabled: bool
    redirect_uris: tuple[str, ...]
    launch_ticket_ttl_seconds: int = MAX_LAUNCH_TICKET_TTL_SECONDS
    authorization_code_ttl_seconds: int = MAX_AUTHORIZATION_CODE_TTL_SECONDS
    desktop_token_ttl_seconds: int = MAX_DESKTOP_TOKEN_TTL_SECONDS

    def __post_init__(self) -> None:
        lifetimes = (
            (
                "launch ticket",
                self.launch_ticket_ttl_seconds,
                MAX_LAUNCH_TICKET_TTL_SECONDS,
            ),
            (
                "authorization code",
                self.authorization_code_ttl_seconds,
                MAX_AUTHORIZATION_CODE_TTL_SECONDS,
            ),
            (
                "desktop token",
                self.desktop_token_ttl_seconds,
                MAX_DESKTOP_TOKEN_TTL_SECONDS,
            ),
        )
        for label, value, maximum in lifetimes:
            if not 1 <= value <= maximum:
                raise ValueError(f"{label} lifetime must be between 1 and {maximum}")
        _redirect_uris(",".join(self.redirect_uris))

    def launch_is_configured(self) -> bool:
        return self.enabled and bool(self.redirect_uris)


DESKTOP_CAPTURE_SETTINGS = DesktopCaptureSettings(
    enabled=config("DESKTOP_CAPTURE_ENABLED", default=False, cast=bool),
    provider_create_enabled=config(
        "DESKTOP_CAPTURE_PROVIDER_CREATE_ENABLED", default=False, cast=bool
    ),
    recovery_enabled=config(
        "DESKTOP_CAPTURE_RECOVERY_ENABLED", default=False, cast=bool
    ),
    redirect_uris=_redirect_uris(
        config("DESKTOP_CAPTURE_REDIRECT_URIS", default="", cast=str)
    ),
    launch_ticket_ttl_seconds=config(
        "DESKTOP_CAPTURE_LAUNCH_TICKET_TTL_SECONDS",
        default=MAX_LAUNCH_TICKET_TTL_SECONDS,
        cast=int,
    ),
    authorization_code_ttl_seconds=config(
        "DESKTOP_CAPTURE_AUTHORIZATION_CODE_TTL_SECONDS",
        default=MAX_AUTHORIZATION_CODE_TTL_SECONDS,
        cast=int,
    ),
    desktop_token_ttl_seconds=config(
        "DESKTOP_CAPTURE_TOKEN_TTL_SECONDS",
        default=MAX_DESKTOP_TOKEN_TTL_SECONDS,
        cast=int,
    ),
)


def get_desktop_capture_settings() -> DesktopCaptureSettings:
    return DESKTOP_CAPTURE_SETTINGS
