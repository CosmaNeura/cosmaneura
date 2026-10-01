"""Atomic owner transfer on owned PostgreSQL; synthetic identities, no Clerk calls.

Run with --confcutdir=tests/authority_kernel. An outer transaction removes only
this test's synthetic rows, even when the service commits its own savepoint.
"""

import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.practice_owner_identity_contract import OwnerIdentityAbsence
from app.services.practice_owner_transfer import (
    PracticeOwnerTransferError,
    transfer_practice_owner,
)

from activemodel.session_manager import _session_context
from app.models.doctor import Doctor
from app.models.organization_access import ProviderOrganization
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import (
    Practice,
    PracticeMember,
    lock_practice_seats,
    practice_seat_lock_held,
)
from app.models.practice_access_audit import PracticeAccessAudit
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, create_engine, select

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


@pytest.fixture
def case():
    raw = os.environ.get("W8_CORE_CAPTURE_TEST_URL")
    if not raw:
        pytest.skip("Explicit owned owner-transfer PostgreSQL URL required")
    url = make_url(raw)
    assert (url.host, url.port, url.database, url.username) == (
        "127.0.0.1",
        55930,
        "core_ux_01_test",
        "scribe_ux_lab_owner",
    ), "Refusing a database outside the owned owner-transfer test scope"
    engine = create_engine(url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            assert connection.execute(
                text("SELECT current_database(), current_user")
            ).one() == (
                "core_ux_01_test",
                "scribe_ux_lab_owner",
            )
            connection.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            connection.execute(text("SET LOCAL lock_timeout = '1500ms'"))
            with Session(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            ) as session:
                token = _session_context.set(session)
                try:
                    yield _seed(session)
                finally:
                    session.rollback()
                    _session_context.reset(token)
            transaction.rollback()
    finally:
        engine.dispose()


def _seed(session):
    suffix = uuid4().hex
    old = Doctor(
        clerk_id=f"user_O{suffix}",
        email=f"old-{suffix}@synthetic.invalid",
        biography=None,
        last_active_at=NOW,
        onboarding_completed_at=NOW,
    )
    new = Doctor(
        clerk_id=f"user_N{suffix}",
        email=f"new-{suffix}@synthetic.invalid",
        biography=None,
        last_active_at=NOW,
        onboarding_completed_at=NOW,
    )
    practice = Practice(
        legal_name="Invented transfer practice",
        join_code=suffix[:16],
        email_domain="synthetic.invalid",
        is_solo=False,
        baa_version="CN-BAA-CUST-CW v2.2",
        baa_accepted_at=NOW - timedelta(days=180),
        baa_signature_name="Synthetic prior signer",
        baa_signature_title="Synthetic owner",
        billing_exempt=False,
    )
    session.add_all([old, new, practice])
    session.flush()
    practice.baa_accepted_by_doctor_id = old.id
    assert lock_practice_seats(session, practice.id)
    previous = PracticeMember(
        practice_id=practice.id,
        doctor_id=old.id,
        role="owner",
        attested_at=NOW - timedelta(days=100),
        attestation_name="Synthetic old member",
    )
    target = PracticeMember(
        practice_id=practice.id,
        doctor_id=new.id,
        role="workforce",
        attested_at=NOW - timedelta(days=30),
        attestation_name="Synthetic new member",
    )
    session.add_all([practice, previous, target])
    session.commit()
    emails = {old.clerk_id: old.email, new.clerk_id: new.email}
    return SimpleNamespace(
        session=session,
        practice=practice,
        old=old,
        new=new,
        previous=previous,
        target=target,
        emails=emails,
        args=dict(
            practice_id=practice.id,
            expected_owner_subject=old.clerk_id,
            new_owner_subject=new.clerk_id,
            new_owner_email_confirmation=new.email,
            transfer_ref=f"synthetic_transfer_{suffix}",
            operator_ref="synthetic_operator",
            reason="owner_correction",
            verify_primary_email=emails.get,
            now=NOW,
        ),
    )


def call(f, **overrides):
    return transfer_practice_owner(**(f.args | overrides))


def orphan_args(f, proof=None):
    proof = proof or OwnerIdentityAbsence(f.old.clerk_id, "staging", "a" * 64)

    def prove(subject):
        assert subject == f.old.clerk_id
        assert practice_seat_lock_held(f.session, f.practice.id)
        return proof

    return dict(recover_orphaned_owner=True, verify_owner_absence=prove)


def audits(f):
    return list(
        f.session.exec(
            select(PracticeAccessAudit).where(
                PracticeAccessAudit.practice_id == f.practice.id
            )
        ).all()
    )


def snapshot(f):
    models = (f.practice, f.old, f.new, f.previous, f.target)
    for model in models:
        f.session.refresh(model)
    return [model.model_dump(exclude={"updated_at"}) for model in models]


def assert_unchanged(f, before):
    assert snapshot(f) == before
    assert audits(f) == []


def change(f, model, **values):
    assert lock_practice_seats(f.session, f.practice.id)
    for field, value in values.items():
        setattr(model, field, value)
    f.session.add(model)
    f.session.commit()


def test_default_dry_run_has_stable_bound_plan_and_no_writes(case):
    f = case
    before = snapshot(f)
    first, second = call(f), call(f)
    assert first.plan_sha256 == second.plan_sha256
    assert len(first.plan_sha256) == 64 and int(first.plan_sha256, 16) >= 0
    for field, expected in (
        ("practice_id", f.practice.id),
        ("previous_owner_doctor_id", f.old.id),
        ("new_owner_doctor_id", f.new.id),
        ("previous_owner_member_id", f.previous.id),
        ("new_owner_member_id", f.target.id),
    ):
        assert str(getattr(first, field)) == str(expected)
    assert first.already_applied is False and first.audit_id is None
    assert_unchanged(f, before)


@pytest.mark.parametrize("digest", [None, "0" * 64])
def test_apply_requires_the_matching_reviewed_plan(case, digest):
    before = snapshot(case)
    with pytest.raises(PracticeOwnerTransferError):
        call(case, apply=True, confirm_plan_sha256=digest)
    assert_unchanged(case, before)


def test_maximum_valid_subjects_refuse_oversized_audit_without_truncation(case):
    f = case
    change(f, f.old, clerk_id="user_" + "A" * 128)
    change(f, f.new, clerk_id="user_" + "B" * 128)
    f.args.update(
        expected_owner_subject=f.old.clerk_id, new_owner_subject=f.new.clerk_id
    )
    f.emails.update({f.old.clerk_id: f.old.email, f.new.clerk_id: f.new.email})
    metadata = dict(
        old_doctor=str(f.old.id),
        new_doctor=str(f.new.id),
        old_member=str(f.previous.id),
        new_member=str(f.target.id),
        old_subject=f.old.clerk_id,
        new_subject=f.new.clerk_id,
        reason=f.args["reason"],
    )
    assert len(json.dumps(metadata, sort_keys=True, separators=(",", ":"))) > 512
    before = snapshot(f)
    with pytest.raises(PracticeOwnerTransferError, match="metadata exceeds"):
        call(f)
    assert_unchanged(f, before)


def test_apply_swaps_only_roles_and_records_one_bound_metadata_receipt(case):
    f = case
    before = snapshot(f)
    plan = call(f)
    applied = call(f, apply=True, confirm_plan_sha256=plan.plan_sha256)
    expected = [dict(row) for row in before]
    expected[3]["role"], expected[4]["role"] = "workforce", "owner"
    assert (
        snapshot(f) == expected
    )  # BAA, billing, Doctor roles, seats and attestations.
    assert applied.already_applied is False
    [audit] = audits(f)
    assert str(applied.audit_id) == str(audit.id)
    assert audit.action == "practice_ownership_transferred"
    assert audit.operator_ref == f.args["operator_ref"]
    assert audit.document_reference == "owner-transfer:" + f.args["transfer_ref"]
    assert audit.evidence_sha256 == plan.plan_sha256
    assert audit.effective_at == NOW and audit.expires_at is None
    metadata = json.loads(audit.reason)
    assert audit.reason == json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    assert {
        str(f.old.id),
        str(f.new.id),
        str(f.previous.id),
        str(f.target.id),
        "owner_correction",
    } <= set(metadata.values())
    assert f.old.email not in audit.reason and f.new.email not in audit.reason


def test_lost_reply_retry_reuses_the_original_receipt(case):
    f = case
    plan = call(f)
    first = call(f, apply=True, confirm_plan_sha256=plan.plan_sha256)
    after = snapshot(f)
    retry = call(f, apply=True, confirm_plan_sha256=plan.plan_sha256)
    assert retry.already_applied is True
    assert retry.audit_id == first.audit_id and retry.plan_sha256 == plan.plan_sha256
    assert snapshot(f) == after and len(audits(f)) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", "different_reason"),
        ("operator_ref", "different_operator"),
        ("transfer_ref", "different_transfer_ref"),
    ],
)
def test_changed_request_cannot_apply_an_earlier_plan(case, field, value):
    before = snapshot(case)
    plan = call(case)
    with pytest.raises(PracticeOwnerTransferError):
        call(case, **{field: value}, apply=True, confirm_plan_sha256=plan.plan_sha256)
    assert_unchanged(case, before)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", "different_reason"),
        ("operator_ref", "different_operator"),
    ],
)
def test_replay_rejects_changed_audit_request_bindings(case, field, value):
    plan = call(case)
    call(case, apply=True, confirm_plan_sha256=plan.plan_sha256)
    after = snapshot(case)
    with pytest.raises(PracticeOwnerTransferError):
        call(case, **{field: value}, apply=True, confirm_plan_sha256=plan.plan_sha256)
    assert snapshot(case) == after and len(audits(case)) == 1


@pytest.mark.parametrize("already_applied", [False, True])
def test_changed_owner_state_refuses_stale_apply_or_receipt_replay(
    case, already_applied
):
    f = case
    plan = call(f)
    if already_applied:
        call(f, apply=True, confirm_plan_sha256=plan.plan_sha256)
        roles = ("owner", "workforce")
    else:
        roles = ("workforce", "owner")
    change(f, f.previous, role=roles[0])
    change(f, f.target, role=roles[1])
    before = snapshot(f)
    with pytest.raises(PracticeOwnerTransferError):
        call(f, apply=True, confirm_plan_sha256=plan.plan_sha256)
    assert snapshot(f) == before and len(audits(f)) == int(already_applied)


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize(
    "guard",
    [
        "same_identity",
        "missing_owner",
        "missing_target",
        "wrong_practice",
        "duplicate_owner",
    ],
)
def test_exact_practice_and_sole_owner_guards(case, guard, recovery):
    f, overrides = case, orphan_args(case) if recovery else {}
    if guard == "same_identity":
        overrides["new_owner_subject"] = f.old.clerk_id
    elif guard == "missing_owner":
        overrides["expected_owner_subject"] = "user_missingSyntheticOwner"
    elif guard == "missing_target":
        overrides["new_owner_subject"] = "user_missingSyntheticTarget"
    elif guard == "duplicate_owner":
        change(f, f.target, role="owner")
    else:
        other = Practice(
            legal_name="Other invented practice",
            join_code=uuid4().hex[:16],
            email_domain="synthetic.invalid",
        )
        f.session.add(other)
        f.session.flush()
        assert lock_practice_seats(f.session, other.id)
        change(f, f.target, practice_id=other.id)
    before = snapshot(f)
    with pytest.raises(PracticeOwnerTransferError):
        call(f, **overrides)
    assert_unchanged(f, before)


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("side", ["old", "new"])
@pytest.mark.parametrize(
    "denial", ["disabled", "permanent", "ended", "inactive", "ending"]
)
def test_disabled_or_revoked_accounts_and_members_never_gain_owner_authority(
    case, side, denial, recovery
):
    f = case
    doctor = getattr(f, side)
    member = f.previous if side == "old" else f.target
    if denial == "disabled":
        change(f, doctor, deleted_at=NOW)
    elif denial == "permanent":
        change(
            f,
            member,
            access_revoked_at=NOW,
            access_revoked_by_ref="synthetic_operator",
            access_revocation_reason="synthetic_revocation",
            access_records_retain_until=NOW + timedelta(days=366 * 7),
        )
    else:
        end = NOW + timedelta(hours=1) if denial == "ending" else NOW
        change(
            f,
            member,
            seat_state="inactive",
            access_ends_at=None if denial == "inactive" else end,
        )
    before = snapshot(f)
    with pytest.raises(PracticeOwnerTransferError):
        call(f, **(orphan_args(f) if recovery else {}))
    assert_unchanged(f, before)


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("side", ["old", "new"])
def test_revoked_pilot_grant_remains_a_hard_denial(case, side, recovery):
    f = case
    doctor = getattr(f, side)
    f.session.add(
        PilotAccessGrandfatherGrant(
            clerk_id=doctor.clerk_id,
            doctor_id_snapshot=doctor.id,
            practice_id_snapshot=f.practice.id,
            email_snapshot=doctor.email,
            email_sha256=hashlib.sha256(doctor.email.encode()).hexdigest(),
            source_ref="synthetic_fixture",
            source_sha256="a" * 64,
            grant_plan_sha256="b" * 64,
            granted_by_ref="synthetic_operator",
            grant_reason="synthetic_fixture",
            granted_at=NOW - timedelta(days=1),
            revoked_at=NOW,
            revoked_by_ref="synthetic_operator",
            revocation_reason="synthetic_revocation",
            revocation_plan_sha256="c" * 64,
            retain_until=NOW + timedelta(days=366 * 7),
        )
    )
    f.session.commit()
    before = snapshot(f)
    with pytest.raises(PracticeOwnerTransferError):
        call(f, **(orphan_args(f) if recovery else {}))
    assert_unchanged(f, before)


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("managed", ["organization", "negotiated_baa"])
def test_managed_practice_is_not_changed_by_ordinary_transfer(case, managed, recovery):
    f = case
    if managed == "organization":
        domain = uuid4().hex + ".synthetic.invalid"
        org = ProviderOrganization(
            legal_name="Invented organization",
            slug=uuid4().hex,
            email_domain=domain,
            access_code_salt="a" * 32,
            access_code_digest=uuid4().hex * 2,
            access_code_version=1,
            access_code_created_at=NOW,
        )
        f.session.add(org)
        f.session.flush()
        change(f, f.practice, organization_id=org.id, email_domain=domain)
    else:
        change(
            f,
            f.practice,
            negotiated_baa_required=True,
            baa_version=None,
            baa_accepted_at=None,
            baa_accepted_by_doctor_id=None,
            baa_signature_name=None,
            baa_signature_title=None,
            trial_ends_at=None,
        )
    before = snapshot(f)
    with pytest.raises(PracticeOwnerTransferError):
        call(f, **(orphan_args(f) if recovery else {}))
    assert_unchanged(f, before)


@pytest.mark.parametrize(
    "guard",
    [
        "old_unverified",
        "new_unverified",
        "stored_mismatch",
        "confirmation_mismatch",
        "email_collision",
    ],
)
def test_live_primary_email_and_exact_confirmation_fail_closed(case, guard):
    f, overrides = case, {}
    if guard in {"old_unverified", "new_unverified"}:
        f.emails[getattr(f, guard.split("_")[0]).clerk_id] = None
    elif guard == "stored_mismatch":
        f.emails[f.new.clerk_id] = "another@synthetic.invalid"
    elif guard == "confirmation_mismatch":
        overrides["new_owner_email_confirmation"] = "another@synthetic.invalid"
    else:
        change(f, f.old, email=f.new.email.upper())
        f.emails[f.old.clerk_id] = f.new.email
    before = snapshot(f)
    with pytest.raises(PracticeOwnerTransferError):
        call(f, **overrides)
    assert_unchanged(f, before)


def test_apply_rechecks_live_email_while_practice_lock_is_held(case):
    f = case
    plan = call(f)
    calls = []

    def fresh(subject):
        assert practice_seat_lock_held(f.session, f.practice.id)
        calls.append(subject)
        return None if subject == f.new.clerk_id else f.emails[subject]

    before = snapshot(f)
    with pytest.raises(PracticeOwnerTransferError):
        call(
            f,
            verify_primary_email=fresh,
            apply=True,
            confirm_plan_sha256=plan.plan_sha256,
        )
    assert f.new.clerk_id in calls
    assert_unchanged(f, before)


@pytest.mark.parametrize("recovery", [False, True])
def test_failed_commit_rolls_back_roles_and_the_flushed_audit(
    case, monkeypatch, recovery
):
    f = case
    options = orphan_args(f) if recovery else {}
    plan, before = call(f, **options), snapshot(f)

    def fail_commit():
        f.session.flush()
        raise SQLAlchemyError("synthetic_commit_failure")

    monkeypatch.setattr(f.session, "commit", fail_commit)
    with pytest.raises((PracticeOwnerTransferError, SQLAlchemyError)):
        call(f, **options, apply=True, confirm_plan_sha256=plan.plan_sha256)
    assert_unchanged(f, before)


@pytest.mark.parametrize("recovery", [False, True])
def test_audit_insert_failure_rolls_back_both_roles(case, recovery):
    f = case
    options = orphan_args(f) if recovery else {}
    plan, before = call(f, **options), snapshot(f)

    def invalid_audit(_mapper, _connection, audit):
        if audit.practice_id == f.practice.id:
            audit.operator_ref = "x" * 256  # Actual PostgreSQL column rejection.

    event.listen(PracticeAccessAudit, "before_insert", invalid_audit)
    try:
        with pytest.raises((PracticeOwnerTransferError, SQLAlchemyError)):
            call(f, **options, apply=True, confirm_plan_sha256=plan.plan_sha256)
    finally:
        event.remove(PracticeAccessAudit, "before_insert", invalid_audit)
    assert_unchanged(f, before)


def test_recovery_requires_explicit_opt_in_and_never_weakens_normal_verification(case):
    f, calls = case, []
    f.emails[f.old.clerk_id] = None
    before = snapshot(f)
    with pytest.raises(PracticeOwnerTransferError, match="live primary"):
        call(f, verify_owner_absence=lambda subject: calls.append(subject))
    assert calls == []
    with pytest.raises(PracticeOwnerTransferError, match="configured absence verifier"):
        call(f, recover_orphaned_owner=True)
    assert_unchanged(f, before)


def test_recovery_preview_apply_and_replay_preserve_records_and_bind_evidence(case):
    f = case
    options = orphan_args(f)
    seen = []

    def verify_new_only(subject):
        assert subject == f.new.clerk_id
        assert practice_seat_lock_held(f.session, f.practice.id)
        seen.append(subject)
        return f.new.email

    options["verify_primary_email"] = verify_new_only
    before = snapshot(f)
    plan = call(f, **options)
    assert call(f, **options).plan_sha256 == plan.plan_sha256
    assert_unchanged(f, before)
    applied = call(f, **options, apply=True, confirm_plan_sha256=plan.plan_sha256)
    expected = [dict(row) for row in before]
    expected[3]["role"], expected[4]["role"] = "workforce", "owner"
    assert snapshot(f) == expected
    [audit] = audits(f)
    metadata = json.loads(audit.reason)
    assert metadata["old_identity"] == "clerk_404_resource_not_found"
    assert metadata["env"] == "staging" and metadata["issuer_sha256"] == "a" * 64
    assert len(audit.reason) <= 512
    assert f.old.email not in audit.reason and f.new.email not in audit.reason
    replay = call(f, **options, apply=True, confirm_plan_sha256=plan.plan_sha256)
    assert replay.already_applied and replay.audit_id == applied.audit_id
    assert len(seen) == 4 and len(audits(f)) == 1 and snapshot(f) == expected


@pytest.mark.parametrize(
    "proof_kind",
    [
        "none",
        "bool",
        "wrong_subject",
        "wrong_mode",
        "bad_issuer",
        "bad_environment",
        "exception",
    ],
)
def test_recovery_requires_a_typed_exact_bound_absence_proof(case, proof_kind):
    f = case
    proof = OwnerIdentityAbsence(f.old.clerk_id, "staging", "a" * 64)
    if proof_kind == "none":
        proof = None
    elif proof_kind == "bool":
        proof = True
    elif proof_kind == "wrong_subject":
        proof = replace(proof, subject=f.new.clerk_id)
    elif proof_kind == "wrong_mode":
        proof = replace(proof, evidence_mode="unverified")
    elif proof_kind == "bad_issuer":
        proof = replace(proof, issuer_sha256="not-a-hash")
    elif proof_kind == "bad_environment":
        proof = replace(proof, environment="unscoped")

    def absent(_subject):
        if proof_kind == "exception":
            raise RuntimeError("private provider detail")
        return proof

    before = snapshot(f)
    with pytest.raises(PracticeOwnerTransferError, match="absence could not be proven"):
        call(f, recover_orphaned_owner=True, verify_owner_absence=absent)
    assert_unchanged(f, before)


@pytest.mark.parametrize("applied", [False, True])
@pytest.mark.parametrize(
    "drift", ["issuer", "environment", "identity_reappears", "mode", "new_email"]
)
def test_recovery_apply_and_replay_refuse_identity_or_mode_drift(case, applied, drift):
    f = case
    options = orphan_args(f)
    plan = call(f, **options)
    if applied:
        call(f, **options, apply=True, confirm_plan_sha256=plan.plan_sha256)
    before = snapshot(f)
    if drift == "issuer":
        options = orphan_args(
            f, OwnerIdentityAbsence(f.old.clerk_id, "staging", "b" * 64)
        )
    elif drift == "environment":
        options = orphan_args(
            f, OwnerIdentityAbsence(f.old.clerk_id, "production", "a" * 64)
        )
    elif drift == "identity_reappears":
        options["verify_owner_absence"] = lambda _subject: None
    elif drift == "mode":
        options = {}
    else:
        f.emails[f.new.clerk_id] = None
    with pytest.raises(PracticeOwnerTransferError):
        call(f, **options, apply=True, confirm_plan_sha256=plan.plan_sha256)
    assert snapshot(f) == before and len(audits(f)) == int(applied)


@pytest.mark.parametrize("applied", [False, True])
def test_normal_plan_or_receipt_cannot_be_reused_as_orphan_recovery(case, applied):
    f = case
    plan = call(f)
    if applied:
        call(f, apply=True, confirm_plan_sha256=plan.plan_sha256)
    before = snapshot(f)
    with pytest.raises(PracticeOwnerTransferError):
        call(f, **orphan_args(f), apply=True, confirm_plan_sha256=plan.plan_sha256)
    assert snapshot(f) == before and len(audits(f)) == int(applied)


@pytest.mark.parametrize(
    "denial",
    [
        "new_unverified",
        "stored_new_mismatch",
        "confirmation_mismatch",
        "same_stored_email",
        "extra_owner",
    ],
)
def test_recovery_retains_new_owner_identity_and_sole_owner_guards(case, denial):
    f, options = case, orphan_args(case)
    if denial == "new_unverified":
        f.emails[f.new.clerk_id] = None
    elif denial == "stored_new_mismatch":
        f.emails[f.new.clerk_id] = "different@synthetic.invalid"
    elif denial == "confirmation_mismatch":
        options["new_owner_email_confirmation"] = "different@synthetic.invalid"
    elif denial == "same_stored_email":
        change(f, f.old, email=f.new.email.upper())
    else:
        change(f, f.target, role="owner")
    before = snapshot(f)
    with pytest.raises(PracticeOwnerTransferError):
        call(f, **options)
    assert_unchanged(f, before)
