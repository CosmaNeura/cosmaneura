"""Owned PostgreSQL only; no real Clerk, provider, patient or global conftest."""

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from typeid import TypeID

from app.configuration.authority import AuthorityKernelSettings
from app.services.companion_provider_provisioning import (
    CompanionProvisioningError,
    provision_companion_provider,
)

from activemodel.session_manager import _session_context
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.baa import BAA_CURRENT_REFERENCE
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.provider_record_authority import ProviderPracticeAuthority
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import func, text
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, create_engine, select

SETTINGS = AuthorityKernelSettings(
    enabled=True,
    token_ttl_seconds=900,
    token_refresh_after_seconds=300,
    clerk_issuer="https://test.clerk.invalid",
)


@pytest.mark.parametrize("value", ["bad", None, "doctor_zzzzzzzzzzzzzzzzzzzzzzzzzz"])
def test_invalid_typeid_has_closed_error(value):
    from app.services.companion_provider_provisioning import _typeid

    with pytest.raises(CompanionProvisioningError, match="invalid_companion_reference"):
        _typeid(value, "doctor")


@pytest.fixture(scope="module")
def engine():
    raw = os.environ.get("COMPANION_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Explicit disposable companion PostgreSQL required")
    url = make_url(raw)
    assert (url.host, url.database, url.username) == (
        "127.0.0.1", "cosmabot_companion_test", "cosmabot_companion_test",
    ) and url.port is not None and 1024 < url.port < 65536
    for source in (Path(__file__).parents[2] / "app/models").glob("*.py"):
        if source.stem != "__init__":
            importlib.import_module("app.models." + source.stem)
    result = create_engine(url)
    with result.connect() as connection:
        assert connection.execute(text("SELECT current_database(), current_user")).one() == (
            "cosmabot_companion_test", "cosmabot_companion_test",
        )
    SQLModel.metadata.create_all(result)
    yield result
    result.dispose()


@contextmanager
def session_for(engine):
    with Session(engine, expire_on_commit=False) as session:
        token = _session_context.set(session)
        try:
            yield session
        finally:
            session.rollback()
            _session_context.reset(token)


def case(engine):
    now = datetime.now(UTC)
    suffix = uuid4().hex
    with session_for(engine) as session:
        doctor = Doctor(
            clerk_id="user_w78_" + suffix,
            email=suffix + "@synthetic.invalid",
            last_active_at=now,
            biography=None,
        )
        practice = Practice(
            legal_name="Invented W78 practice",
            email_domain="synthetic.invalid",
            join_code=suffix[:16],
            baa_accepted_at=now,
            baa_version=BAA_CURRENT_REFERENCE,
            billing_exempt=True,
        )
        session.add_all([doctor, practice])
        session.flush()
        session.add(
            PracticeMember(
                practice_id=practice.id,
                doctor_id=doctor.id,
                role="owner",
                attested_at=now,
            )
        )
        session.commit()
        return {
            "doctor_id": doctor.id,
            "practice_id": practice.id,
            "tenant_id": TypeID(prefix="authority_tenant"),
            "scribe_tenant_id": uuid4(),
            "verified_primary_email": doctor.email,
            "verified_clerk_subject": doctor.clerk_id,
            "email_confirmation": doctor.email,
            "review_ref": "review_w78_" + suffix,
            "operator_ref": "operator_w78_" + suffix,
            "settings": SETTINGS,
            "create_missing_authority": True,
        }


def plan(engine, args):
    with session_for(engine) as session:
        return provision_companion_provider(session, **args)


def apply(engine, args, digest):
    with session_for(engine) as session:
        return provision_companion_provider(
            session, **args, apply=True, confirm_plan_sha256=digest
        )


def count(engine, model):
    with session_for(engine) as session:
        return session.exec(select(func.count()).select_from(model)).one()


def test_plan_writes_nothing_apply_and_lost_reply_retry_reuse_one_link(
    engine, monkeypatch
):
    args = case(engine)
    before = [
        count(engine, model)
        for model in [
            AuthorityTenant,
            AuthorityActor,
            ScribeCaptureProviderLink,
            AuthorityAuditEvent,
        ]
    ]
    reviewed = plan(engine, args)
    assert reviewed.state == "ready_for_review" and set(
        reviewed.authority_records_to_create
    ) == {"tenant", "actor_identity", "provider_membership", "core_practice_authority"}
    assert before == [
        count(engine, model)
        for model in [
            AuthorityTenant,
            AuthorityActor,
            ScribeCaptureProviderLink,
            AuthorityAuditEvent,
        ]
    ]
    first = apply(engine, args, reviewed.plan_sha256)
    second = apply(engine, args, reviewed.plan_sha256)
    assert first.provider_link_ref == second.provider_link_ref
    assert second.state == "already_provisioned"
    assert [n + 1 for n in before] == [
        count(engine, model)
        for model in [
            AuthorityTenant,
            AuthorityActor,
            ScribeCaptureProviderLink,
            AuthorityAuditEvent,
        ]
    ]
    # Existing normal discovery consumes the newly provisioned relationship.
    from app.services.companion_encounters import _provider

    with session_for(engine) as session:
        mapping = session.exec(
            select(ProviderPracticeAuthority).where(
                ProviderPracticeAuthority.practice_id == args["practice_id"]
            )
        ).one()
        assert mapping.tenant_id == args["tenant_id"]
        doctor = session.get(Doctor, args["doctor_id"])
        result = _provider(
            session,
            issuer=SETTINGS.clerk_issuer,
            subject=doctor.clerk_id,
            session_id="sess_w78_invented",
            tenant_ref=args["tenant_id"],
            settings=SETTINGS,
        )
        assert str(result[0].provider_link_ref) == first.provider_link_ref


def test_existing_core_practice_mapping_is_reused_and_cannot_be_retargeted(engine):
    args = case(engine)
    with session_for(engine) as session:
        session.add(AuthorityTenant(id=args["tenant_id"]))
        session.flush()
        mapping = ProviderPracticeAuthority(
            practice_id=args["practice_id"], tenant_id=args["tenant_id"]
        )
        session.add(mapping)
        session.commit()
        mapping_id = mapping.id
    before = count(engine, ProviderPracticeAuthority)
    reviewed = plan(engine, args)
    assert "core_practice_authority" not in reviewed.authority_records_to_create
    apply(engine, args, reviewed.plan_sha256)
    assert count(engine, ProviderPracticeAuthority) == before
    with session_for(engine) as session:
        assert (
            session.get(ProviderPracticeAuthority, mapping_id).tenant_id
            == args["tenant_id"]
        )
    with pytest.raises(
        CompanionProvisioningError,
        match="existing_core_practice_correspondence_required",
    ):
        plan(engine, {**args, "tenant_id": TypeID(prefix="authority_tenant")})


@pytest.mark.parametrize(
    "change",
    [
        {"create_missing_authority": False},
        {"verified_primary_email": None},
        {"email_confirmation": "other@synthetic.invalid"},
        {"scribe_tenant_id": uuid4(), "review_ref": "bad"},
    ],
)
def test_invalid_or_unreviewed_authority_does_not_create_rows(engine, change):
    args = {**case(engine), **change}
    before = count(engine, ScribeCaptureProviderLink)
    with pytest.raises(CompanionProvisioningError):
        plan(engine, args)
    assert count(engine, ScribeCaptureProviderLink) == before


def test_changed_confirmation_and_processing_revocation_are_denied(engine):
    args = case(engine)
    reviewed = plan(engine, args)
    with pytest.raises(CompanionProvisioningError):
        apply(engine, args, "f" * 64)
    with session_for(engine) as session:
        doctor = session.get(Doctor, args["doctor_id"])
        doctor.deleted_at = datetime.now(UTC)
        session.add(doctor)
        session.commit()
    with pytest.raises(CompanionProvisioningError):
        apply(engine, args, reviewed.plan_sha256)


def test_revoked_link_and_identity_cannot_be_reactivated(engine):
    for target in ("link", "identity"):
        args = case(engine)
        reviewed = plan(engine, args)
        apply(engine, args, reviewed.plan_sha256)
        with session_for(engine) as session:
            if target == "link":
                row = session.exec(
                    select(ScribeCaptureProviderLink).where(
                        ScribeCaptureProviderLink.doctor_id == args["doctor_id"]
                    )
                ).one()
            else:
                doctor = session.get(Doctor, args["doctor_id"])
                row = session.exec(
                    select(AuthorityActorExternalIdentity).where(
                        AuthorityActorExternalIdentity.subject == doctor.clerk_id
                    )
                ).one()
            row.revoked_at = datetime.now(UTC)
            session.add(row)
            session.commit()
        with pytest.raises(CompanionProvisioningError):
            apply(engine, args, reviewed.plan_sha256)


def test_practice_and_scribe_tenant_cannot_be_reused_for_different_scope(engine):
    args = case(engine)
    apply(engine, args, plan(engine, args).plan_sha256)
    other = case(engine)
    for change in [
        {"tenant_id": args["tenant_id"]},
        {"scribe_tenant_id": args["scribe_tenant_id"]},
    ]:
        with pytest.raises(CompanionProvisioningError):
            plan(engine, {**other, **change})


def test_concurrent_apply_has_one_link_and_one_audit(engine):
    args = case(engine)
    digest = plan(engine, args).plan_sha256
    before = count(engine, AuthorityAuditEvent)
    barrier = Barrier(2)

    def worker():
        barrier.wait(timeout=10)
        return apply(engine, args, digest)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert len({result.provider_link_ref for result in results}) == 1
    assert {result.state for result in results} == {
        "provisioned",
        "already_provisioned",
    }
    assert count(engine, AuthorityAuditEvent) == before + 1


def test_another_current_clinician_can_join_the_same_reviewed_practice(engine):
    first = case(engine)
    apply(engine, first, plan(engine, first).plan_sha256)
    second = case(engine)
    with session_for(engine) as session:
        member = session.exec(
            select(PracticeMember).where(
                PracticeMember.doctor_id == second["doctor_id"]
            )
        ).one()
        member.practice_id = first["practice_id"]
        session.add(member)
        session.commit()
    second.update(
        practice_id=first["practice_id"],
        tenant_id=first["tenant_id"],
        scribe_tenant_id=first["scribe_tenant_id"],
    )
    result = apply(engine, second, plan(engine, second).plan_sha256)
    assert result.state == "provisioned"


def test_registered_cli_plans_applies_and_hides_identity(engine, monkeypatch):
    import json

    from typer.testing import CliRunner

    from app import cli

    args = case(engine)
    monkeypatch.setattr(cli, "global_session", lambda: session_for(engine))
    monkeypatch.setattr(
        "app.configuration.authority.AUTHORITY_KERNEL_SETTINGS", SETTINGS
    )
    calls = []

    def verified(subject):
        calls.append(subject)
        return args["verified_primary_email"]

    monkeypatch.setattr("app.services.clerk_identity.verified_primary_email", verified)
    command = [
        "provision-companion-provider",
        "--doctor-id",
        str(args["doctor_id"]),
        "--practice-id",
        str(args["practice_id"]),
        "--authority-tenant-id",
        str(args["tenant_id"]),
        "--scribe-tenant-id",
        str(args["scribe_tenant_id"]),
        "--review-ref",
        args["review_ref"],
        "--operator-ref",
        args["operator_ref"],
        "--create-missing-authority",
    ]
    runner = CliRunner()
    missing = runner.invoke(cli.app, [*command, "--apply"])
    assert missing.exit_code != 0 and not calls
    prepared = runner.invoke(cli.app, command, input=args["email_confirmation"] + "\n")
    assert prepared.exit_code == 0, prepared.output
    record = json.loads(
        next(line for line in prepared.output.splitlines() if line.startswith("{"))
    )
    assert record["state"] == "ready_for_review"
    result = runner.invoke(
        cli.app,
        [*command, "--apply", "--confirm-plan-sha256", record["plan_sha256"]],
        input=args["email_confirmation"] + "\n",
    )
    assert result.exit_code == 0, result.output
    assert '"state": "provisioned"' in result.output
    assert (
        args["email_confirmation"] not in result.output
        and calls[-1] not in result.output
    )


def test_existing_authority_tenant_without_links_cannot_cross_practices(engine):
    original = case(engine)
    with session_for(engine) as session:
        doctor = session.get(Doctor, original["doctor_id"])
        actor = AuthorityActor(actor_kind="human")
        tenant = AuthorityTenant(id=original["tenant_id"])
        session.add_all([actor, tenant])
        session.flush()
        session.add_all(
            [
                AuthorityActorExternalIdentity(
                    actor_id=actor.id,
                    issuer=SETTINGS.clerk_issuer,
                    subject=doctor.clerk_id,
                    verified_at=datetime.now(UTC),
                ),
                AuthorityTenantMembership(
                    actor_id=actor.id, tenant_id=tenant.id, membership_kind="provider"
                ),
            ]
        )
        session.commit()
    other = case(engine)
    with pytest.raises(CompanionProvisioningError):
        plan(engine, {**other, "tenant_id": original["tenant_id"]})


def test_verification_must_match_the_current_locked_clerk_subject(engine):
    args = case(engine)
    with pytest.raises(CompanionProvisioningError):
        plan(engine, {**args, "verified_clerk_subject": "user_different_invented"})


def test_cached_doctor_cannot_hide_identity_change_after_verification(engine):
    args = case(engine)
    before = count(engine, ScribeCaptureProviderLink)
    with session_for(engine) as cached_session:
        cached = cached_session.get(Doctor, args["doctor_id"])
        assert cached.clerk_id == args["verified_clerk_subject"]
        with Session(engine) as updater:
            doctor = updater.get(Doctor, args["doctor_id"])
            doctor.clerk_id = "user_w78_rebound_" + uuid4().hex
            updater.add(doctor)
            updater.commit()
        with pytest.raises(CompanionProvisioningError):
            provision_companion_provider(cached_session, **args)
    assert count(engine, ScribeCaptureProviderLink) == before
