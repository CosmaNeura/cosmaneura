"""Dormant deployment-owned patient analysis transport; no credential fallback."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from azure.identity import ManagedIdentityCredential
from decouple import config


@dataclass(frozen=True)
class PatientAnalysisSettings:
    enabled: bool
    endpoint: str
    audience: str
    client_id: str
    model_profile: str

    def token(self) -> str:
        url = urlsplit(self.endpoint)
        if (
            not self.enabled
            or url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path != "/service/v1/visit-prep/clarification"
            or not UUID(self.client_id).int
            or not UUID(self.audience).int
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", self.model_profile)
            is None
        ):
            raise ValueError("patient_analysis_configuration_unavailable")
        with ManagedIdentityCredential(
            client_id=self.client_id,
            retry_total=0,
            connection_timeout=3,
            read_timeout=3,
        ) as credential:
            return credential.get_token(f"{self.audience}/.default").token


SETTINGS = PatientAnalysisSettings(
    enabled=config("VISIT_PREP_PATIENT_AI_ENABLED", default=False, cast=bool),
    endpoint=config("VISIT_PREP_CLARIFICATION_ENDPOINT", default="", cast=str),
    audience=config("VISIT_PREP_CLARIFICATION_AUDIENCE", default="", cast=str),
    client_id=config("VISIT_PREP_CLARIFICATION_CLIENT_ID", default="", cast=str),
    model_profile=config(
        "VISIT_PREP_CLARIFICATION_MODEL_PROFILE", default="", cast=str
    ),
)
