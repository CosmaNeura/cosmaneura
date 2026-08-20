"""Isolated synthetic fixtures for the authority kernel.

This test subtree is run with ``--confcutdir=tests/authority_kernel`` so the
repository-wide session hook cannot delete Clerk users or truncate PostgreSQL.
"""

import os
import sys
from collections.abc import Iterator
from types import ModuleType

import pytest

_TEST_ENVIRONMENT = {
    "PYTHON_ENV": "test",
    "TZ": "UTC",
    "INTAKE_CHAT_DOMAIN": "intake.test.invalid",
    "TEST_DATABASE_URL": "postgresql://synthetic:synthetic@localhost/synthetic",
    "AZURE_OPENAI_API_KEY": "synthetic",
    "AZURE_OPENAI_ENDPOINT": "https://synthetic.openai.azure.com",
    "SMTP_URL": "smtp://localhost:1025",
    "EMAIL_FROM_ADDRESS": "noreply@test.invalid",
    "AZURE_UPLOADS_ACCOUNT_NAME": "synthetic",
    "AZURE_UPLOADS_ACCOUNT_KEY": "synthetic",
    "AZURE_UPLOADS_ACCOUNT_URL": "https://synthetic.blob.core.windows.net",
    "AZURE_UPLOADS_CONTAINER_NAME": "synthetic",
    "AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME": "synthetic-notes",
    "AZURE_DEID_TENANT_ID": "synthetic",
    "AZURE_DEID_CLIENT_ID": "synthetic",
    "AZURE_DEID_CLIENT_SECRET": "synthetic",
    "AZURE_DEID_ENDPOINT": "https://synthetic.api.cognitive.microsoft.com",
    "ASSEMBLY_API_KEY": "synthetic",
    "CLERK_PRIVATE_KEY": "synthetic",
    "ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    "POSTHOG_SECRET_KEY": "synthetic",
    "POSTHOG_HOST": "https://example.invalid",
    "RECALL_API_KEY": "synthetic",
    "RECALL_API_BASE": "https://example.invalid",
    "SESSION_SECRET_KEY": "synthetic-session-secret",
    "ALLOWED_HOST_LIST": "testserver",
    "API_BASE_URL": "https://api.test.invalid",
    "ZOOM_CLIENT_ID": "synthetic",
    "ZOOM_CLIENT_SECRET": "synthetic",
    "ZOOM_SECRET_TOKEN": "synthetic",
    "ZOOM_REDIRECT_URI": "https://api.test.invalid/callback",
    "ZOOM_SUCCESS_REDIRECT": "https://app.test.invalid",
    "TEST_REDIS_URL": "redis://localhost:6379/15",
    "COOKIE_DOMAIN": "testserver",
}
for name, value in _TEST_ENVIRONMENT.items():
    os.environ.setdefault(name, value)

# ``app`` normally checks live PostgreSQL and Redis versions during import.
# These unit tests provide their own in-memory SQLAlchemy session and must not
# touch either service.
versions_stub = ModuleType("app.configuration.versions")
versions_stub.check_service_versions = lambda: None  # type: ignore[attr-defined]
sys.modules[versions_stub.__name__] = versions_stub

from app.models.authority import (  # noqa: E402
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityIdempotencyRecord,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityPersonOrigin,
    AuthorityTenant,
    AuthorityTenantMembership,
    InteractionLineage,
    MountContextSession,
    MountContextToken,
)
from app.models.authority_conversation import (  # noqa: E402
    CanonicalConversationFinalPhase,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from app.models.authority_usage import AgentModelUsageEvent  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

_AUTHORITY_TABLES = [
    model.__table__  # type: ignore[attr-defined]
    for model in (
        AuthorityTenant,
        AuthorityActor,
        AuthorityPerson,
        AuthorityActorExternalIdentity,
        AuthorityTenantMembership,
        AuthorityPersonOrigin,
        AuthorityPersonBinding,
        MountContextSession,
        MountContextToken,
        InteractionLineage,
        CanonicalConversationTurn,
        CanonicalConversationUserPhase,
        CanonicalConversationFinalPhase,
        AgentModelUsageEvent,
        AuthorityIdempotencyRecord,
        AuthorityAuditEvent,
    )
]


def pytest_configure(config) -> None:
    config.option.enable_pretty_traceback = False
    config.option.enable_pretty_traceback_local_stack_only = True


@pytest.fixture
def authority_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine, tables=_AUTHORITY_TABLES)
    with Session(engine, expire_on_commit=False) as session:
        yield session
