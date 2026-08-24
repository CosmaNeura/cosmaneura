"""PostgreSQL lock regression for authority issuance versus account closure."""

import os
import secrets
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime

import psycopg
import pytest

from app.configuration.authority import AuthorityKernelSettings
from app.services.authority import exchange_context_token

from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityTenant,
    AuthorityTenantMembership,
)


def test_exchange_locks_identity_until_token_session_is_committed():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("requires the PostgreSQL test database URL")
    issuer = "https://closure-lock-test.clerk.invalid"
    suffix = secrets.token_hex(5)
    subject = f"user_close_lock_{suffix}"
    binding_ref = f"provider-record-{suffix}"
    tenant = AuthorityTenant(status="active").save()
    actor = AuthorityActor(actor_kind="human", status="active").save()
    identity = AuthorityActorExternalIdentity(
        actor_id=actor.id,
        issuer=issuer,
        subject=subject,
        verified_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
    ).save()
    AuthorityTenantMembership(
        tenant_id=tenant.id,
        actor_id=actor.id,
        membership_kind="provider",
        status="active",
    ).save()
    person = AuthorityPerson(lifecycle_state="patient", status="active").save()
    AuthorityPersonBinding(
        person_id=person.id,
        tenant_id=tenant.id,
        actor_id=actor.id,
        binding_ref=binding_ref,
        binding_kind="provider_record",
        status="active",
        verified_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
    ).save()
    settings = AuthorityKernelSettings(
        enabled=True,
        token_ttl_seconds=900,
        token_refresh_after_seconds=300,
        clerk_issuer=issuer,
    )

    with psycopg.connect(database_url) as blocker:
        locked = blocker.execute(
            "SELECT id FROM authority_actor_external_identity WHERE id = %s FOR UPDATE",
            (identity.id.uuid,),
        ).fetchone()
        assert locked is not None
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                exchange_context_token,
                clerk_issuer=issuer,
                clerk_subject=subject,
                requested_surface="provider_assistant",
                record_ref=binding_ref,
                settings=settings,
            )
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
            blocker.commit()
            issued = future.result(timeout=5)

    assert issued.context_token
