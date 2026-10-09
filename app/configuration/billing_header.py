"""Empty-by-default exact synthetic/deidentified source admission, not transport auth."""

import json
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from decouple import config
from pydantic import field_validator, model_validator

from app.services.billing_header_contract import Closed, Digest, Ref


class BillingSourceAdmission(Closed):
    admissionId: Ref
    practiceId: Ref
    doctorId: Ref
    appointmentId: Ref
    patientId: Ref
    noteId: Ref
    contentSha256: Digest
    dataClass: Literal["synthetic", "deidentified"]
    reviewRef: Ref
    expiresAt: str

    @field_validator("expiresAt")
    @classmethod
    def instant(cls, value):
        if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("invalid_billing_admission")
        return value


class BillingSourcePolicy(Closed):
    schemaVersion: Literal["core.billing-source-policy.v1"] = (
        "core.billing-source-policy.v1"
    )
    enabled: bool = False
    clerkIssuer: str = ""
    admissions: tuple[BillingSourceAdmission, ...] = ()

    @model_validator(mode="after")
    def valid(self):
        u = urlsplit(self.clerkIssuer)
        if self.enabled and (
            u.scheme != "https"
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
            or u.path
        ):
            raise ValueError("invalid_billing_source_policy")
        if len(self.admissions) > 128:
            raise ValueError("too_many_billing_source_admissions")
        if len({a.noteId for a in self.admissions}) != len(self.admissions):
            raise ValueError("duplicate_billing_source_admission")
        return self


def parse_policy(raw: str) -> BillingSourcePolicy:
    if not raw:
        return BillingSourcePolicy()

    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError("duplicate_policy_key")
            result[k] = v
        return result

    try:
        if len(raw.encode()) > 131072:
            raise ValueError
        value = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise ValueError
        value["admissions"] = tuple(
            BillingSourceAdmission.model_validate(a)
            for a in value.get("admissions", [])
        )
        return BillingSourcePolicy.model_validate(value)
    except (ValueError, TypeError, KeyError):
        raise ValueError("invalid_billing_source_policy") from None


BILLING_SOURCE_POLICY = parse_policy(
    config("BILLING_SOURCE_POLICY_JSON", default="", cast=str)
)
