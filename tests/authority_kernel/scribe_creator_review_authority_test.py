"""Real current Core authority on a fresh, explicitly selected synthetic database."""

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services import authority, view_as
from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as protocol
from app.services.scribe_creator_review_contract import CreatorReviewView

from app.models.authority import AuthorityActorExternalIdentity
from app.models.doctor import Doctor
from app.models.scribe_capture_binding import ScribeCaptureBinding
from app.models.superadmin_capability import (
    SuperadminCapabilityEnvironment,
    SuperadminCapabilityGrant,
    SuperadminCapabilityKind,
)
from app.models.view_as import ViewAsSession
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, create_engine

from tests.authority_kernel.scribe_capture_admission_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_admission_test import call_arguments
from tests.authority_kernel.scribe_capture_scope_test import (
    REAL_VIEW_AS_GUARD,
    make_scope_case,
)
from tests.authority_kernel.scribe_creator_review_test import view_document

admission_case = _admission_case


@pytest.fixture(scope="module")
def review_engine():
    raw = os.getenv("SCRIBE_CORE_REVIEW_TEST_URL")
    if not raw:
        pytest.skip("Explicit fresh Core creator-review test database required")
    url = make_url(raw)
    private_scope = (url.database == "scribe_core_creator_review_test"
            and url.username == "scribe_core_review_owner" and url.host is None
            and url.query.get("host", "").startswith("/private/tmp/scribe-core-review.")
            and url.query.get("port") == "55457")
    operational_beta_scope = url.database in {"scribe_core_creator_review_test", "staging_release_review_test"} and (url.host, url.port, url.username) == (
        "127.0.0.1", 32768, "beta_local"
    )
    if not (private_scope or operational_beta_scope):
        pytest.fail("Refusing database outside the private synthetic review scope")
    from activemodel.session_manager import _serialize_pydantic_model

    engine = create_engine(url, json_serializer=_serialize_pydantic_model)
    # Local model schema only. No shared migrations or live application bootstrap.
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def capture_engine(review_engine, monkeypatch):
    monkeypatch.setattr(view_as, "mount_context_session_blocked_by_view_as", REAL_VIEW_AS_GUARD)
    return review_engine


async def admitted(f):
    await admission.admit_capture(**call_arguments(f))
    return admission.authorize_admitted_capture(f.caller, f.binding.binding_ref, f.binding.workspace_id)


def arguments(f, remote):
    return dict(caller=f.caller, binding_ref=f.binding.binding_ref, workspace_id=f.binding.workspace_id,
        operation=protocol.ReadOperation(kind="creator_review"), client_request_key=uuid4().hex, client=remote)


def install_view_as(f):
    now = datetime.now(UTC)
    target = Doctor(clerk_id="user_synthetic_support_" + uuid4().hex, biography=None)
    f.session.add(target)
    f.session.flush()
    issuer = hashlib.sha256(f.case.identity.issuer.encode()).hexdigest()
    subject = hashlib.sha256(f.case.identity.subject.encode()).hexdigest()
    grant = SuperadminCapabilityGrant(
        environment=SuperadminCapabilityEnvironment.staging,
        capability_kind=SuperadminCapabilityKind.view_as,
        clerk_issuer=f.case.identity.issuer, clerk_subject=f.case.identity.subject,
        doctor_id=f.case.doctor.id, issuer_sha256=issuer, subject_sha256=subject,
        verified_primary_email_sha256="a" * 64, source_ref="synthetic:reviewed-capability",
        source_sha256="b" * 64, grant_plan_sha256="c" * 64,
        granted_by_ref="synthetic:reviewer", grant_reason_code="synthetic_fixture", granted_at=now,
    )
    f.session.add(grant)
    f.session.flush()
    f.session.add(ViewAsSession(
        environment="staging", capability_grant_id=grant.id,
        actor_doctor_id=f.case.doctor.id, target_doctor_id=target.id,
        actor_issuer_sha256=issuer, actor_subject_sha256=subject,
        clerk_session_sha256="d" * 64, browser_nonce_sha256="e" * 64,
        started_at=now, absolute_expires_at=now + timedelta(minutes=10),
    ))
    f.session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", ["other_creator", "tenant_changed", "revoked", "view_as", "revoked_during"])
async def test_real_authority_rejects_foreign_or_revoked_review(admission_case, rejection):
    f = admission_case
    scope = await admitted(f)
    assert scope.creator_actor_id == str(f.case.actor.id)
    assert scope.creator_actor_id != scope.session_request.practitioner_id
    calls = []

    def revoke():
        with Session(f.engine) as session:
            row = session.get(AuthorityActorExternalIdentity, f.case.identity.id)
            row.revoked_at = datetime.now(UTC)
            session.add(row)
            session.commit()

    class Remote:
        async def capture_operation(self, tenant_id, session_id, operation, **kwargs):
            assert kwargs["creator_actor_id"] == scope.creator_actor_id
            calls.append(True)
            if rejection == "revoked_during":
                revoke()
            return CreatorReviewView.model_validate(view_document(session_id))

    args = arguments(f, Remote())
    if rejection == "other_creator":
        other = make_scope_case(f.session, existing_tenant=f.case.tenant, existing_practice=f.case.practice)
        args["caller"] = admission.CaptureCaller(**{
            k: v for k, v in other.arguments.items() if k in admission.CaptureCaller.__dataclass_fields__})
    elif rejection == "tenant_changed":
        row = f.session.get(ScribeCaptureBinding, f.binding.binding_ref)
        row.scribe_tenant_id = uuid4()
        f.session.add(row)
        f.session.commit()
    elif rejection == "revoked":
        revoke()
    elif rejection == "view_as":
        install_view_as(f)
    with pytest.raises(authority.AuthorityKernelError):
        await gateway.execute_capture_operation(**args)
    assert len(calls) == (1 if rejection == "revoked_during" else 0)
