"""Fail-closed configuration for the Visit Prep release-control seam."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from decouple import config
from typeid import TypeID
from typeid.errors import TypeIDException

VISIT_PREP_FLAG_NAME = "ff_visit_prep_202608"
VISIT_PREP_FLAG_ENV = "FF_VISIT_PREP_202608"
VISIT_PREP_PUBLIC_API_ENV = "VISIT_PREP_PUBLIC_API_ENABLED"


@dataclass(frozen=True)
class VisitPrepSettings:
    """Server-side rollout and delivery controls.

    Cohorts contain canonical, fully-qualified TypeID strings. Keeping doctor
    and practice cohorts separate avoids treating a mutable attribute such as
    an email address or domain as release authority.
    """

    enabled: bool = False
    public_api_enabled: bool = False
    auto_synthesis_enabled: bool = False
    email_consent_capture_enabled: bool = False
    enabled_doctor_ids: frozenset[str] = field(default_factory=frozenset)
    enabled_practice_ids: frozenset[str] = field(default_factory=frozenset)
    email_send_enabled: bool = False
    sms_send_enabled: bool = False
    configuration_valid: bool = True


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise ValueError("boolean setting must be text or bool")
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError("invalid boolean setting")


def _parse_typeid_cohort(value: object, *, prefix: str) -> frozenset[str]:
    if not isinstance(value, str):
        raise ValueError("TypeID cohort must be text")
    if not value.strip():
        return frozenset()

    parsed: set[str] = set()
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            raise ValueError("TypeID cohort contains an empty entry")
        type_id = TypeID.from_string(item)
        # ``str`` also makes the canonical lowercase representation explicit.
        if type_id.prefix != prefix or str(type_id) != item:
            raise ValueError(f"expected a canonical {prefix} TypeID")
        parsed.add(item)
    return frozenset(parsed)


def load_visit_prep_settings(
    values: Mapping[str, object] | None = None,
) -> VisitPrepSettings:
    """Load all Visit Prep controls as one fail-closed configuration unit.

    ``values`` is an explicit test/deployment-validation seam. Production uses
    python-decouple so local ``.env`` behavior remains consistent with the rest
    of the application. If any value is malformed, no feature or channel flag
    survives independently.
    """

    def read(name: str, default: str) -> object:
        if values is not None:
            return values.get(name, default)
        return config(name, default=default, cast=str)

    try:
        enabled = _parse_bool(read(VISIT_PREP_FLAG_ENV, "false"))
        public_api_enabled = _parse_bool(read(VISIT_PREP_PUBLIC_API_ENV, "false"))
        auto_synthesis_enabled = _parse_bool(
            read("VISIT_PREP_AUTO_SYNTHESIS_ENABLED", "false")
        )
        email_consent_capture_enabled = _parse_bool(
            read("VISIT_PREP_EMAIL_CONSENT_CAPTURE_ENABLED", "false")
        )
        enabled_doctor_ids = _parse_typeid_cohort(
            read("VISIT_PREP_ENABLED_DOCTOR_IDS", ""),
            prefix="doctor",
        )
        enabled_practice_ids = _parse_typeid_cohort(
            read("VISIT_PREP_ENABLED_PRACTICE_IDS", ""),
            prefix="practice",
        )
        email_send_enabled = _parse_bool(read("VISIT_PREP_EMAIL_SEND_ENABLED", "false"))
        sms_send_enabled = _parse_bool(read("VISIT_PREP_SMS_SEND_ENABLED", "false"))
    except (AttributeError, TypeError, TypeIDException, ValueError):
        # Configuration is authorization input. A typo must make every Visit
        # Prep capability false, not partially preserve a permissive value.
        return VisitPrepSettings(configuration_valid=False)

    return VisitPrepSettings(
        enabled=enabled,
        public_api_enabled=public_api_enabled,
        auto_synthesis_enabled=auto_synthesis_enabled,
        email_consent_capture_enabled=email_consent_capture_enabled,
        enabled_doctor_ids=enabled_doctor_ids,
        enabled_practice_ids=enabled_practice_ids,
        email_send_enabled=email_send_enabled,
        sms_send_enabled=sms_send_enabled,
    )


VISIT_PREP_SETTINGS = load_visit_prep_settings()
