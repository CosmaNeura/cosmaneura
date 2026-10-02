"""Dormant SMS sender routing; no credentials, transport or activation gate."""

from collections.abc import Mapping
from dataclasses import dataclass

from decouple import config


@dataclass(frozen=True, repr=False)
class VisitPrepSMSRouting:
    adapter_namespace: str = ""
    account_reference: str = ""
    sender_number: str = ""


def load_sms_routing(values: Mapping[str, object] | None = None) -> VisitPrepSMSRouting:
    def read(name: str) -> str:
        value = values.get(name, "") if values is not None else config(name, default="")
        if not isinstance(value, str):
            raise ValueError("SMS routing is unavailable.")
        return value

    return VisitPrepSMSRouting(
        read("VISIT_PREP_SMS_ADAPTER_NAMESPACE"),
        read("VISIT_PREP_SMS_ACCOUNT_REFERENCE"),
        read("VISIT_PREP_SMS_SENDER_NUMBER"),
    )


ROUTING = load_sms_routing()
