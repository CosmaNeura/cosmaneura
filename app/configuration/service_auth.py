from dataclasses import dataclass

from decouple import config

SCRIBE_TRANSCRIPT_WRITE_ROLE = "Scribe.Transcript.Write"


@dataclass(frozen=True)
class EntraServiceAuthSettings:
    enabled: bool
    tenant_id: str
    audience: str
    scribe_client_id: str

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    def is_complete(self) -> bool:
        return bool(self.tenant_id and self.audience and self.scribe_client_id)


SERVICE_AUTH_SETTINGS = EntraServiceAuthSettings(
    enabled=config("SCRIBE_HANDOFF_ENABLED", default=False, cast=bool),
    tenant_id=config("ENTRA_TENANT_ID", default="", cast=str),
    audience=config("SCRIBE_API_AUDIENCE", default="", cast=str),
    scribe_client_id=config("SCRIBE_CALLER_CLIENT_ID", default="", cast=str),
)
