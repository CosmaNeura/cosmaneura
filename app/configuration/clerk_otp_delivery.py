"""Dormant, exact-instance OTP delivery settings; never a new identity issuer."""

import base64
import binascii
import re
from dataclasses import dataclass, field
from email.utils import getaddresses

from decouple import config

from app.environments import python_environment

STAGING_ISSUER = "https://resolved-emu-53.clerk.accounts.dev"
STAGING_INSTANCE_ID = "ins_2onn3Q7NDfrwC5oVXgzGe6vA1u4"
TEMPLATE_SLUG = "verification_code"
CONTRACT_VERSION = "verification_code:six-digits:v1"


def valid_recipient(value: object) -> bool:
    if not isinstance(value, str) or not value.isascii() or len(value) > 254:
        return False
    local, separator, domain = value.rpartition("@")
    return bool(
        separator
        and re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}", local)
        and "." in domain
        and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in domain.split(".")
        )
    )


@dataclass(frozen=True)
class OtpDeliverySettings:
    instance_id: str
    signing_key: bytes = field(repr=False)


def load_settings() -> OtpDeliverySettings | None:
    # No credential/configuration reads while dormant, including production.
    if config("CLERK_OTP_DELIVERY_ENABLED", default="false", cast=str) != "true":
        return None
    if (
        python_environment() != "staging"
        or config("AUTHORITY_CLERK_ISSUER", default="", cast=str) != STAGING_ISSUER
        or config("CLERK_OTP_CONTRACT_VERSION", default="", cast=str)
        != CONTRACT_VERSION
    ):
        raise ValueError("clerk_otp_delivery_configuration_invalid")
    instance_id = config("CLERK_OTP_INSTANCE_ID", default="", cast=str)
    sender = config("EMAIL_FROM_ADDRESS", default="", cast=str)
    addresses = getaddresses([sender])
    if (
        instance_id != STAGING_INSTANCE_ID
        or any(ord(c) < 32 for c in sender)
        or len(addresses) != 1
        or not valid_recipient(addresses[0][1])
        or addresses[0][1].rpartition("@")[2].lower() != "cosmaneura.com"
    ):
        raise ValueError("clerk_otp_delivery_configuration_invalid")
    secret = config("CLERK_OTP_WEBHOOK_SECRET", default="", cast=str)
    try:
        if not secret.startswith("whsec_"):
            raise ValueError
        key = base64.b64decode(secret[6:], validate=True)
        # Svix's standard HMAC signing key is 24 random bytes (192 bits).
        if len(key) < 24:
            raise ValueError
    except (ValueError, binascii.Error):
        raise ValueError("clerk_otp_delivery_configuration_invalid") from None
    return OtpDeliverySettings(instance_id, key)


SETTINGS = load_settings()
