"""Automatic activation against owned synthetic PostgreSQL, with no live I/O."""

import hashlib
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any, TypeVar
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from typeid import TypeID

from app.configuration.scribe_activation import ScribeActivationSettings
from app.services.scribe_activation import (
    ActivationDenied,
    ActivationSetupRequired,
    activate_scribe,
)

from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityAuditEvent,
    AuthorityTenant,
    AuthorityTenantMembership,
)
from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember, lock_practice_seats
from app.models.practice_scribe_tenant import PracticeScribeTenant
from app.models.provider_record_authority import ProviderPracticeAuthority
from app.models.scribe_cancellation import scribe_cancellation_operation
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from tests.authority_kernel.companion_provider_provisioning_test import (
    SETTINGS,
    apply,
    case,
    count,
    plan,
    session_for,
)
from tests.authority_kernel.companion_provider_provisioning_test import (
    engine as companion_engine,
)

engine = companion_engine

MODELS = (
    AuthorityTenant,
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityTenantMembership,
    ProviderPracticeAuthority,
    ScribeCaptureProviderLink,
    PracticeScribeTenant,
    AuthorityAuditEvent,
)


RowT = TypeVar("RowT")


def require_row(session: Session, model: type[RowT], key: Any) -> RowT:
    row = session.get(model, key)
    assert row is not None
    return row


def snapshot(engine):
    return tuple(count(engine, model) for model in MODELS)


def activate(engine, args, *, subject=None, settings=None):
    policy = settings or ScribeActivationSettings(
        enabled=True,
        practice_refs=frozenset({str(args["practice_id"])}),
        authority=SETTINGS,
    )
    with session_for(engine) as session:
        return activate_scribe(
            session,
            issuer=SETTINGS.clerk_issuer,
            subject=subject or args["verified_clerk_subject"],
            trigger="native-client",
            settings=policy,
        )


def grant(session, doctor, practice_id, *, revoked=False):
    now = datetime.now(UTC)
    values = dict(
        id=TypeID(prefix="pilot_access_grant"),
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=practice_id,
        email_snapshot=doctor.email,
        email_sha256=hashlib.sha256(doctor.email.encode()).hexdigest(),
        source_ref="synthetic-activation-fixture",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="synthetic-test",
        grant_reason="Synthetic test grant",
        granted_at=now,
    )
    if revoked:
        values.update(
            revoked_at=now,
            revoked_by_ref="synthetic-test",
            revocation_reason="Synthetic revocation",
            revocation_plan_sha256="c" * 64,
            retain_until=now + timedelta(days=365 * 8),
        )
    session.add(PilotAccessGrandfatherGrant.model_validate(values))


def test_atomic_activation_and_repeat_do_not_duplicate(engine):
    args = case(engine)
    before = snapshot(engine)
    first = activate(engine, args)
    after = snapshot(engine)
    second = activate(engine, args)
    assert first.state == "active" and second.state == "already_active"
    assert first.provider_link_ref == second.provider_link_ref
    assert after == tuple(n + 1 for n in before) == snapshot(engine)
    with session_for(engine) as session:
        audit = session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == first.provider_link_ref
            )
        ).one()
        assert audit.event_type == "scribe.provider.activated"
        assert {r["kind"] for r in audit.platform_refs} == {
            "trigger",
            "doctor",
            "practice",
        }
        assert args["verified_primary_email"] not in str(audit.platform_refs)


@pytest.mark.parametrize(
    "change", ["fresh", "membership", "baa", "entitlement", "attestation"]
)
def test_incomplete_setup_creates_nothing(engine, change):
    args = case(engine)
    subject = None
    with session_for(engine) as session:
        practice = require_row(session, Practice, args["practice_id"])
        member = session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == args["doctor_id"])
        ).one()
        if change == "fresh":
            subject = "user_synthetic_fresh_" + uuid4().hex
        if change == "membership":
            lock_practice_seats(session, practice.id)
            session.delete(member)
        if change == "baa":
            practice.baa_accepted_at = None
        if change == "entitlement":
            practice.billing_exempt = False
        if change == "attestation":
            member.attested_at = None
        session.commit()
    before = snapshot(engine)
    with pytest.raises(ActivationSetupRequired, match="practice"):
        activate(engine, args, subject=subject)
    assert snapshot(engine) == before


@pytest.mark.parametrize(
    "change",
    [
        "deleted",
        "inactive",
        "revoked-member",
        "revoked-grant",
        "cancelled",
        "identity",
        "link",
        "move-after-revocation",
    ],
)
def test_tombstones_never_reactivate(engine, change):
    args = case(engine)
    if change in {"identity", "link", "move-after-revocation"}:
        activate(engine, args)
    with session_for(engine) as session:
        doctor = require_row(session, Doctor, args["doctor_id"])
        member = session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
        ).one()
        now = datetime.now(UTC)
        if change == "deleted":
            doctor.deleted_at = now
        if change in {"inactive", "revoked-member"}:
            lock_practice_seats(session, member.practice_id)
            if change == "inactive":
                member.seat_state = "inactive"
            else:
                member.access_revoked_at = now
                member.access_revoked_by_ref = "synthetic-test"
                member.access_revocation_reason = "Synthetic offboarding"
                member.access_records_retain_until = now + timedelta(days=365 * 8)
        if change == "revoked-grant":
            grant(session, doctor, member.practice_id, revoked=True)
        if change == "identity":
            identity = session.exec(
                select(AuthorityActorExternalIdentity).where(
                    AuthorityActorExternalIdentity.subject == doctor.clerk_id
                )
            ).one()
            identity.revoked_at = now
        if change in {"link", "move-after-revocation"}:
            link = session.exec(
                select(ScribeCaptureProviderLink).where(
                    ScribeCaptureProviderLink.doctor_id == doctor.id
                )
            ).one()
            link.revoked_at = now
            if change == "move-after-revocation":
                destination = Practice(
                    legal_name="Synthetic moved practice",
                    email_domain="synthetic.invalid",
                    join_code=uuid4().hex[:16],
                    billing_exempt=True,
                )
                session.add(destination)
                session.flush()
                lock_practice_seats(session, member.practice_id)
                lock_practice_seats(session, destination.id)
                member.practice_id = destination.id
        if change == "cancelled":
            session.execute(
                scribe_cancellation_operation.insert().values(
                    id=uuid4(),
                    caller_sha256=uuid4().hex * 2,
                    doctor_ref=str(doctor.id),
                    request_key_sha256="a" * 64,
                    preview_sha256="b" * 64,
                    inventory_sha256="c" * 64,
                    inventory=[],
                    policy={
                        "grace_days": 90,
                        "phi_scope": "scribe-processing-copies",
                        "approval_sha256": "d" * 64,
                    },
                    accepted_at=now,
                    export_until=now + timedelta(days=90),
                    phase="read-only",
                )
            )
        session.commit()
    before = snapshot(engine)
    with pytest.raises(ActivationDenied, match="scribe_activation_denied"):
        activate(engine, args)
    assert snapshot(engine) == before


def test_grandfathered_member_keeps_entitlement_without_billing_changes(engine):
    args = case(engine)
    with session_for(engine) as session:
        practice = require_row(session, Practice, args["practice_id"])
        doctor = require_row(session, Doctor, args["doctor_id"])
        practice.billing_exempt = False
        grant(session, doctor, practice.id)
        session.commit()
    assert activate(engine, args).state == "active"
    with session_for(engine) as session:
        assert (
            require_row(session, Practice, args["practice_id"]).billing_exempt is False
        )


def test_second_authorized_member_shares_only_scribe_practice_uuid(engine):
    args = case(engine)
    first = activate(engine, args)
    with session_for(engine) as session:
        doctor = Doctor(
            clerk_id="user_synthetic_second_" + uuid4().hex,
            email=uuid4().hex + "@synthetic.invalid",
            last_active_at=datetime.now(UTC),
            onboarding_completed_at=datetime.now(UTC),
            biography=None,
        )
        session.add(doctor)
        session.flush()
        lock_practice_seats(session, args["practice_id"])
        session.add(
            PracticeMember(
                practice_id=args["practice_id"],
                doctor_id=doctor.id,
                role="workforce",
                attested_at=datetime.now(UTC),
            )
        )
        session.commit()
        subject = doctor.clerk_id
    second = activate(engine, args, subject=subject)
    with session_for(engine) as session:
        links = session.exec(
            select(ScribeCaptureProviderLink).where(
                ScribeCaptureProviderLink.practice_id == args["practice_id"]
            )
        ).all()
        assert len(links) == 2 and first.provider_link_ref != second.provider_link_ref
        assert len({link.scribe_tenant_id for link in links}) == 1
        assert len({link.actor_id for link in links}) == 2


def test_concurrent_activation_creates_one_complete_set(engine):
    args = case(engine)
    before = snapshot(engine)
    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=10)
        return activate(engine, args)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert len({r.provider_link_ref for r in results}) == 1
    assert snapshot(engine) == tuple(n + 1 for n in before)


def test_existing_reviewed_link_keeps_its_namespace_and_provenance(engine):
    args = case(engine)
    reviewed = plan(engine, args)
    existing = apply(engine, args, reviewed.plan_sha256)
    with session_for(engine) as session:
        link = session.exec(
            select(ScribeCaptureProviderLink).where(
                ScribeCaptureProviderLink.doctor_id == args["doctor_id"]
            )
        ).one()
        review_ref = link.review_ref
    result = activate(engine, args)
    assert (
        result.state == "already_active"
        and result.provider_link_ref == existing.provider_link_ref
    )
    with session_for(engine) as session:
        link = session.exec(
            select(ScribeCaptureProviderLink).where(
                ScribeCaptureProviderLink.doctor_id == args["doctor_id"]
            )
        ).one()
        assert link.review_ref == review_ref and link.tenant_id == args["tenant_id"]
        assert (
            session.exec(
                select(PracticeScribeTenant).where(
                    PracticeScribeTenant.practice_id == args["practice_id"]
                )
            )
            .one()
            .scribe_tenant_id
            == args["scribe_tenant_id"]
        )


@pytest.mark.parametrize("off", ["capability", "cohort", "authority"])
def test_disabled_rollout_is_a_denial_and_never_writes(engine, off):
    args = case(engine)
    policy = ScribeActivationSettings(
        enabled=off != "capability",
        practice_refs=frozenset()
        if off == "cohort"
        else frozenset({str(args["practice_id"])}),
        authority=replace(SETTINGS, enabled=off != "authority"),
    )
    before = snapshot(engine)
    with pytest.raises(ActivationDenied):
        activate(engine, args, settings=policy)
    assert snapshot(engine) == before


def test_grant_without_membership_cannot_manufacture_a_practice(engine):
    args = case(engine)
    with session_for(engine) as session:
        doctor = require_row(session, Doctor, args["doctor_id"])
        grant(session, doctor, args["practice_id"])
        lock_practice_seats(session, args["practice_id"])
        member = session.exec(
            select(PracticeMember).where(PracticeMember.doctor_id == doctor.id)
        ).one()
        session.delete(member)
        session.commit()
    before = snapshot(engine)
    with pytest.raises(ActivationSetupRequired):
        activate(engine, args)
    assert snapshot(engine) == before


def test_existing_personal_namespace_is_preserved_for_new_practice_members(engine):
    args = case(engine)
    personal_tenant = TypeID(prefix="authority_tenant")
    with session_for(engine) as session:
        session.add(AuthorityTenant.model_validate({"id": personal_tenant}))
        session.flush()
        session.add(
            ProviderPracticeAuthority.model_validate(
                {
                    "id": TypeID(prefix="provider_practice_authority"),
                    "practice_id": None,
                    "doctor_id": args["doctor_id"],
                    "tenant_id": personal_tenant,
                }
            )
        )
        session.commit()
    first = activate(engine, args)
    with session_for(engine) as session:
        doctor = Doctor(
            clerk_id="user_synthetic_personal_" + uuid4().hex,
            email=uuid4().hex + "@synthetic.invalid",
            last_active_at=datetime.now(UTC),
            onboarding_completed_at=datetime.now(UTC),
            biography=None,
        )
        session.add(doctor)
        session.flush()
        lock_practice_seats(session, args["practice_id"])
        session.add(
            PracticeMember(
                practice_id=args["practice_id"],
                doctor_id=doctor.id,
                role="workforce",
                attested_at=datetime.now(UTC),
            )
        )
        session.commit()
        subject = doctor.clerk_id
    second = activate(engine, args, subject=subject)
    with session_for(engine) as session:
        links = session.exec(
            select(ScribeCaptureProviderLink).where(
                ScribeCaptureProviderLink.practice_id == args["practice_id"]
            )
        ).all()
        assert {str(link.id) for link in links} == {
            first.provider_link_ref,
            second.provider_link_ref,
        }
        assert len({link.scribe_tenant_id for link in links}) == 1
        assert len({link.tenant_id for link in links}) == 2
        original = next(
            link for link in links if str(link.id) == first.provider_link_ref
        )
        assert original.tenant_id == personal_tenant


def test_reused_email_never_adopts_another_subjects_actor(engine):
    old = case(engine)
    activate(engine, old)
    new = case(engine)
    with session_for(engine) as session:
        old_doctor = require_row(session, Doctor, old["doctor_id"])
        new_doctor = require_row(session, Doctor, new["doctor_id"])
        email = old_doctor.email
        old_doctor.email = uuid4().hex + "@synthetic.invalid"
        session.flush()
        new_doctor.email = email
        session.commit()
    activate(engine, new)
    with session_for(engine) as session:
        links = session.exec(
            select(ScribeCaptureProviderLink).where(
                col(ScribeCaptureProviderLink.doctor_id).in_(
                    [old["doctor_id"], new["doctor_id"]]
                )
            )
        ).all()
        assert len({link.actor_id for link in links}) == 2
        assert len({link.tenant_id for link in links}) == 2
        assert len({link.scribe_tenant_id for link in links}) == 2


@pytest.mark.parametrize("conflict", [None, "practice", "shared-uuid", "zero"])
def test_expand_backfill_is_exact_immutable_and_retained_on_recovery(engine, conflict):
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/2026_11_04_e78845cecca6_add_practice_scribe_tenant.py"
    )
    spec = importlib.util.spec_from_file_location(
        "synthetic_scribe_tenant_expand", path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = "scribe_activation_" + uuid4().hex
    # Every object and fixture lives inside this transaction's random schema.
    # Rolling it back removes only this test's local synthetic objects.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            connection.execute(text("CREATE TABLE practice (id uuid PRIMARY KEY)"))
            connection.execute(
                text(
                    "CREATE TABLE scribe_capture_provider_link (practice_id uuid, scribe_tenant_id uuid, revoked_at timestamptz)"
                )
            )
            practice_id, scribe_uuid = uuid4(), uuid4()
            connection.execute(
                text("INSERT INTO practice VALUES (:id)"), {"id": practice_id}
            )
            connection.execute(
                text(
                    "INSERT INTO scribe_capture_provider_link VALUES (:practice, :scribe, NULL), (:practice, :scribe, NULL)"
                ),
                {"practice": practice_id, "scribe": scribe_uuid},
            )
            if conflict == "practice":
                connection.execute(
                    text(
                        "INSERT INTO scribe_capture_provider_link VALUES (:practice, :scribe, NULL)"
                    ),
                    {"practice": practice_id, "scribe": uuid4()},
                )
            if conflict == "shared-uuid":
                connection.execute(
                    text(
                        "INSERT INTO scribe_capture_provider_link VALUES (:practice, :scribe, NULL)"
                    ),
                    {"practice": uuid4(), "scribe": scribe_uuid},
                )
            if conflict == "zero":
                connection.execute(
                    text(
                        "UPDATE scribe_capture_provider_link SET scribe_tenant_id='00000000-0000-0000-0000-000000000000'"
                    )
                )
            with Operations.context(MigrationContext.configure(connection)):
                if conflict:
                    with pytest.raises(
                        IntegrityError, match="scribe_tenant_backfill_conflict"
                    ):
                        with connection.begin_nested():
                            migration.upgrade()
                    assert (
                        connection.execute(
                            text("SELECT to_regclass(:ref)"),
                            {"ref": schema + ".practice_scribe_tenant"},
                        ).scalar()
                        is None
                    )
                else:
                    migration.upgrade()
                    assert connection.execute(
                        text(
                            "SELECT practice_id, scribe_tenant_id FROM practice_scribe_tenant"
                        )
                    ).all() == [(practice_id, scribe_uuid)]
                    with connection.begin_nested():
                        with pytest.raises(
                            IntegrityError, match="practice_scribe_tenant_immutable"
                        ):
                            connection.execute(
                                text(
                                    "UPDATE practice_scribe_tenant SET scribe_tenant_id=:id"
                                ),
                                {"id": uuid4()},
                            )
                        # Roll back the failed SQL before leaving the savepoint.
                        connection.get_nested_transaction().rollback()
                    with pytest.raises(RuntimeError, match="dormant"):
                        migration.downgrade()
                    assert (
                        connection.execute(
                            text("SELECT count(*) FROM practice_scribe_tenant")
                        ).scalar()
                        == 1
                    )
        finally:
            transaction.rollback()


@pytest.mark.parametrize("trigger", ["onboarding", "membership", "entitlement"])
def test_committed_domain_events_use_the_same_idempotent_activation(engine, trigger):
    from app.services.scribe_activation_events import activate_scribe_after_commit

    args = case(engine)
    settings = ScribeActivationSettings(
        enabled=True,
        practice_refs=frozenset({str(args["practice_id"])}),
        authority=SETTINGS,
    )
    before = snapshot(engine)
    scope = (
        {"practice_ref": args["practice_id"]}
        if trigger == "entitlement"
        else {"doctor_ref": args["doctor_id"]}
    )
    activate_scribe_after_commit(
        trigger=trigger, bind=engine, settings=settings, **scope
    )
    assert snapshot(engine) == tuple(n + 1 for n in before)
    activate_scribe_after_commit(
        trigger=trigger, bind=engine, settings=settings, **scope
    )
    assert snapshot(engine) == tuple(n + 1 for n in before)


def test_event_failure_rolls_back_only_activation_and_does_not_escape(
    engine, monkeypatch
):
    from app.services import scribe_activation_events

    args = case(engine)
    settings = ScribeActivationSettings(
        enabled=True,
        practice_refs=frozenset({str(args["practice_id"])}),
        authority=SETTINGS,
    )
    before = snapshot(engine)

    def unavailable(session, **_kwargs):
        session.add(AuthorityTenant())
        session.flush()
        raise RuntimeError("Synthetic unavailable")

    monkeypatch.setattr(scribe_activation_events, "activate_scribe", unavailable)
    scribe_activation_events.activate_scribe_after_commit(
        trigger="onboarding",
        doctor_ref=args["doctor_id"],
        bind=engine,
        settings=settings,
    )
    assert snapshot(engine) == before
    with session_for(engine) as session:
        assert (
            require_row(session, Doctor, args["doctor_id"]).onboarding_completed_at
            is not None
        )


def test_default_off_event_never_opens_a_database_session(monkeypatch):
    from app.services import scribe_activation_events

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Default-off activation must not open a database")

    monkeypatch.setattr(scribe_activation_events, "Session", unexpected)
    scribe_activation_events.activate_scribe_after_commit(
        trigger="onboarding",
        doctor_ref="synthetic",
        settings=ScribeActivationSettings(
            enabled=False, practice_refs=frozenset(), authority=SETTINGS
        ),
    )
