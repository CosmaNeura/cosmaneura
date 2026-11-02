"""Execute the actual service on isolated synthetic transactional objects, no DB.

The query adapter evaluates named source predicates and transaction locks. It
does not replace the product decision functions or contact an application.
"""

import ast
import copy
import hashlib
import hmac
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC)


class Predicate:
    def __init__(self, evaluate):
        self.evaluate = evaluate


class Field:
    def __init__(self, model, name, transform=lambda x: x):
        self.model, self.name, self.transform = model, name, transform

    def __eq__(self, value):
        return Predicate(lambda row: self.transform(getattr(row, self.name)) == value)


class ModelMeta(type):
    def __getattr__(cls, name):
        return Field(cls, name)


class Model(metaclass=ModelMeta):
    count = 0

    def __init__(self, **values):
        type(self).count += 1
        defaults = dict(
            id=f"{type(self).__name__}_{type(self).count}",
            revoked_at=None,
            revoked_by_ref=None,
            revocation_reason=None,
            revocation_plan_sha256=None,
            retain_until=None,
            expires_at=None,
        )
        self.__dict__.update(defaults | values)


Doctor = type("Doctor", (Model,), {})
Practice = type("Practice", (Model,), {})
PracticeMember = type("PracticeMember", (Model,), {})
Grant = type("Grant", (Model,), {})
Binding = type("Binding", (Model,), {})
Receipt = type("Receipt", (Model,), {})
Amendment = type("Amendment", (Model,), {})
IdentityReceipt = type("IdentityReceipt", (Model,), {})


class Query:
    def __init__(self, target):
        self.target, self.predicates, self.lock = target, [], False

    def where(self, *conditions):
        self.predicates.extend(conditions)
        return self

    def execution_options(self, **options):
        assert options == {"populate_existing": True}
        return self

    def with_for_update(self):
        self.lock = True
        return self


class Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def one_or_none(self):
        if len(self.rows) > 1:
            raise DatabaseError("multiple")
        return self.first()


class DatabaseError(Exception):
    pass


class Session:
    def __init__(self, database):
        self.database, self.snapshot, self.locked = database, None, False
        self.new, self.dirty, self.deleted = [], [], []

    def exec(self, query):
        target = query.target.model if isinstance(query.target, Field) else query.target
        if query.lock:
            assert self.locked
            self.database.order.append(target.__name__)
        rows = [
            row
            for row in self.database.rows[target]
            if all(predicate.evaluate(row) for predicate in query.predicates)
        ]
        return Result(
            [getattr(row, query.target.name) for row in rows]
            if isinstance(query.target, Field)
            else rows
        )

    def add(self, row):
        if row not in self.database.rows[type(row)]:
            self.database.rows[type(row)].append(row)

    def flush(self):
        self.database.flushes += 1
        if self.database.fail_flush == self.database.flushes:
            raise DatabaseError("synthetic flush refusal")
        active = [g for g in self.database.rows[Grant] if g.revoked_at is None]
        for field in ("clerk_id", "doctor_id_snapshot", "email_sha256"):
            values = [
                getattr(g, field) for g in active if getattr(g, field) is not None
            ]
            if len(values) != len(set(values)):
                raise DatabaseError("synthetic unique active grant refusal")

    def commit(self):
        self.snapshot = None
        if self.locked:
            self.locked = False
            self.database.mutex.release()

    def rollback(self):
        if self.snapshot is not None:
            self.database.rows = self.snapshot
        self.commit()


def lock_lineage(session):
    session.database.mutex.acquire()
    session.locked = True
    session.snapshot = copy.deepcopy(session.database.rows)
    session.database.order.append("lineage")


def transform(field, function):
    return Field(field.model, field.name, lambda x: function(field.transform(x)))


def source_namespace():
    path = ROOT / "app/services/pilot_practice_snapshot_binding.py"
    tree = ast.parse(path.read_text())
    tree.body = [
        n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    settings = SimpleNamespace(
        has_valid_clerk_issuer=lambda: True,
        clerk_issuer="https://synthetic.example.clerk.accounts.dev",
    )
    namespace = dict(
        __name__=__name__,
        dataclass=dataclass,
        Any=Any,
        cast=cast,
        re=re,
        datetime=datetime,
        UTC=UTC,
        hashlib=hashlib,
        hmac=hmac,
        json=json,
        TypeIDType=str,
        Session=Session,
        Doctor=Doctor,
        Practice=Practice,
        PracticeMember=PracticeMember,
        PilotAccessGrandfatherGrant=Grant,
        PilotAccessPracticeBinding=Binding,
        PracticeAccessAudit=Receipt,
        PilotAccessContinuityAmendmentIdentity=Amendment,
        AuthorityAuditEvent=IdentityReceipt,
        SQLAlchemyError=DatabaseError,
        AUTHORITY_KERNEL_SETTINGS=settings,
        python_environment=lambda: "staging",
        lock_pilot_access_lineage=lock_lineage,
        select=Query,
        col=lambda x: x,
        or_=lambda *conditions: Predicate(
            lambda row: any(p.evaluate(row) for p in conditions)
        ),
        func=SimpleNamespace(
            lower=lambda field: transform(field, lambda x: x.lower() if x else x),
            btrim=lambda field: transform(field, lambda x: x.strip() if x else x),
        ),
        SEAT_HOLDING_ACCESS_STATES={"active"},
        member_access_state=lambda member, at: "active"
        if member.seat_state == "active"
        else "inactive",
        PracticeAccessAction=SimpleNamespace(
            pilot_practice_snapshot_bound=SimpleNamespace(
                value="pilot_practice_snapshot_bound"
            )
        ),
    )
    exec(compile(tree, str(path), "exec"), namespace)

    def assert_closed_cohort(session, environment):
        assert environment == "staging"
        session.database.audit_calls += 1
        if not session.database.cohort_valid or (
            session.database.fail_post_audit and session.database.rows[Binding]
        ):
            raise namespace["PilotPracticeBindingError"](
                "existing closed pilot cohort integrity is invalid"
            )

    namespace["_assert_closed_cohort"] = assert_closed_cohort
    return SimpleNamespace(**namespace)


@pytest.fixture
def fixture():
    service = source_namespace()
    doctor = Doctor(
        id="doctor_synthetic",
        clerk_id="user_synthetic_provider",
        email="provider@example.com",
        deleted_at=None,
        onboarding_completed_at=NOW - timedelta(days=2),
    )
    practice = Practice(
        id="practice_synthetic",
        organization_id=None,
        baa_version="synthetic_v1",
        baa_accepted_at=NOW - timedelta(days=3),
        negotiated_baa_on_file=False,
        negotiated_baa_effective_date=None,
        negotiated_baa_reference=None,
        baa_in_force=lambda at: True,
    )
    member = PracticeMember(
        id="member_synthetic",
        doctor_id=doctor.id,
        practice_id=practice.id,
        access_revoked_at=None,
        access_ends_at=None,
        seat_state="active",
        attested_at=NOW - timedelta(days=2),
    )

    def sha(value):
        return hashlib.sha256(value.encode()).hexdigest()

    grant = Grant(
        id="grant_original",
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=None,
        email_snapshot=doctor.email,
        email_sha256=sha(doctor.email),
        source_ref="synthetic-cohort",
        source_sha256="a" * 64,
        grant_plan_sha256="b" * 64,
        granted_by_ref="synthetic-original",
        grant_reason="Synthetic legacy grant",
        granted_at=NOW - timedelta(days=10),
    )
    rows = {
        Doctor: [doctor],
        Practice: [practice],
        PracticeMember: [member],
        Grant: [grant],
        Binding: [],
        Receipt: [],
        Amendment: [],
        IdentityReceipt: [],
    }
    database = SimpleNamespace(
        rows=rows,
        mutex=threading.RLock(),
        order=[],
        flushes=0,
        fail_flush=-1,
        audit_calls=0,
        cohort_valid=True,
        fail_post_audit=False,
    )
    request = service.BindingRequest(
        doctor.id,
        practice.id,
        grant.id,
        sha(
            json.dumps(
                [doctor.id, doctor.clerk_id, doctor.email], separators=(",", ":")
            )
        ),
        sha(doctor.email),
        service.grant_before_state_sha256(grant),
        "c" * 40,
        NOW,
        "change:synthetic",
        "Bind only the existing authorized synthetic practice",
    )
    return service, database, request


def plan(fixture):
    service, database, request = fixture
    return service.plan_pilot_practice_binding(request, session=Session(database))


def apply(fixture, digest=None):
    service, database, request = fixture
    return service.apply_pilot_practice_binding(
        request,
        confirmed_plan_sha256=digest or plan(fixture).plan_sha256,
        session=Session(database),
    )


def test_preview_has_no_mutation_and_apply_retains_original_and_idempotently_retries(
    fixture,
):
    service, db, request = fixture
    before = copy.deepcopy(db.rows[Grant][0].__dict__)
    preview = plan(fixture)
    assert not preview.already_applied and db.rows[Grant][0].__dict__ == before
    result = apply(fixture, preview.plan_sha256)
    original, successor = db.rows[Grant]
    assert not result.already_applied and original.practice_id_snapshot is None
    for key in (
        "clerk_id",
        "doctor_id_snapshot",
        "email_snapshot",
        "source_ref",
        "source_sha256",
        "granted_at",
        "granted_by_ref",
        "grant_reason",
        "grant_plan_sha256",
    ):
        assert getattr(original, key) == before[key]
    assert (
        successor.clerk_id == original.clerk_id
        and successor.practice_id_snapshot == request.practice_id
    )
    assert original.retain_until >= service._retention(original.revoked_at)
    assert len(db.rows[Binding]) == len(db.rows[Receipt]) == 1
    assert db.order[:4] == ["lineage", "Practice", "Doctor", "PracticeMember"]
    assert apply(fixture, preview.plan_sha256).already_applied
    assert len(db.rows[Grant]) == 2


@pytest.mark.parametrize(
    "target,field,value",
    [
        (Grant, "practice_id_snapshot", "practice_wrong"),
        (Grant, "practice_id_snapshot", "practice_synthetic"),
        (Grant, "doctor_id_snapshot", "doctor_wrong"),
        (Grant, "revoked_at", NOW),
        (Grant, "granted_at", NOW + timedelta(days=1)),
        (Grant, "email_sha256", "d" * 64),
        (Grant, "retain_until", NOW),
        (Grant, "source_sha256", "invalid"),
        (Doctor, "deleted_at", NOW),
        (Doctor, "email", "other@example.com"),
        (Doctor, "onboarding_completed_at", None),
        (Doctor, "onboarding_completed_at", NOW + timedelta(days=1)),
        (Practice, "organization_id", "organization_synthetic"),
        (PracticeMember, "practice_id", "practice_wrong"),
        (PracticeMember, "access_revoked_at", NOW),
        (PracticeMember, "attested_at", None),
        (PracticeMember, "attested_at", NOW + timedelta(days=1)),
        (PracticeMember, "seat_state", "inactive"),
    ],
)
def test_refuses_current_identity_practice_grant_and_access_conflicts(
    fixture, target, field, value
):
    service, db, _ = fixture
    setattr(db.rows[target][0], field, value)
    with pytest.raises(service.PilotPracticeBindingError):
        plan(fixture)
    assert not db.rows[Binding]


def test_current_baa_required_and_linked_lineages_refused(fixture):
    service, db, _ = fixture
    db.rows[Practice][0].baa_in_force = lambda at: False
    with pytest.raises(service.PilotPracticeBindingError):
        plan(fixture)
    db.rows[Practice][0].baa_in_force = lambda at: True
    db.rows[Amendment].append(Amendment(grant_id=db.rows[Grant][0].id))
    with pytest.raises(service.PilotPracticeBindingError):
        plan(fixture)
    db.rows[Amendment].clear()
    db.rows[IdentityReceipt].append(
        IdentityReceipt(
            request_digest="b" * 64, event_type="platform.doctor_identity.rebound"
        )
    )
    with pytest.raises(service.PilotPracticeBindingError):
        plan(fixture)


@pytest.mark.parametrize("target", [Grant, Doctor, PracticeMember])
def test_duplicate_history_or_membership_refuses(fixture, target):
    service, db, _ = fixture
    db.rows[target].append(copy.deepcopy(db.rows[target][0]))
    with pytest.raises(service.PilotPracticeBindingError):
        plan(fixture)


@pytest.mark.parametrize(
    "field,value",
    [
        ("identity_sha256", "d" * 64),
        ("before_state_sha256", "d" * 64),
        ("source_commit_sha", "invalid"),
        ("observed_at", NOW - timedelta(hours=1)),
        ("observed_at", NOW + timedelta(hours=1)),
        ("operator_ref", "  "),
    ],
)
def test_exact_confirmation_and_observation_validation(fixture, field, value):
    service, db, request = fixture
    with pytest.raises(service.PilotPracticeBindingError):
        service.plan_pilot_practice_binding(
            replace(request, **{field: value}), session=Session(db)
        )


def test_state_changes_between_preview_and_apply_fail_cas_without_revocation(fixture):
    service, db, _ = fixture
    digest = plan(fixture).plan_sha256
    db.rows[Grant][0].source_ref = "changed-evidence"
    with pytest.raises(service.PilotPracticeBindingError, match="no longer matches"):
        apply(fixture, digest)
    assert len(db.rows[Grant]) == 1 and db.rows[Grant][0].revoked_at is None


@pytest.mark.parametrize("flush", [1, 2, 3])
def test_transaction_rollback_keeps_legacy_grant_and_no_partial_receipts(
    fixture, flush
):
    service, db, _ = fixture
    digest = plan(fixture).plan_sha256
    db.fail_flush = flush
    with pytest.raises(service.PilotPracticeBindingError):
        apply(fixture, digest)
    assert len(db.rows[Grant]) == 1 and db.rows[Grant][0].revoked_at is None
    assert not db.rows[Receipt] and not db.rows[Binding]


def test_two_concurrent_exact_requests_create_one_successor(fixture):
    digest = plan(fixture).plan_sha256
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: apply(fixture, digest), range(2)))
    _, db, _ = fixture
    assert sorted(result.already_applied for result in results) == [False, True]
    assert len(db.rows[Grant]) == 2 and len(db.rows[Binding]) == 1


def test_revoked_successor_and_changed_practice_never_restore_on_retry(fixture):
    service, db, _ = fixture
    digest = plan(fixture).plan_sha256
    apply(fixture, digest)
    db.rows[Grant][1].revoked_at = datetime.now(UTC)
    with pytest.raises(service.PilotPracticeBindingError):
        apply(fixture, digest)
    assert db.rows[Grant][1].revoked_at is not None
    db.rows[Grant][1].revoked_at = None
    db.rows[PracticeMember][0].practice_id = "practice_new"
    with pytest.raises(service.PilotPracticeBindingError):
        apply(fixture, digest)


@pytest.mark.parametrize(
    "target,field,value",
    [
        (Binding, "payload_json", "{}"),
        (Binding, "plan_sha256", "e" * 64),
        (Receipt, "action", "doctor_identity_rebound"),
        (Receipt, "operator_ref", "changed"),
        (Grant, "source_ref", "changed"),
        (Grant, "retain_until", NOW),
    ],
)
def test_tampered_receipt_plan_original_evidence_and_retention_refuse(
    fixture, target, field, value
):
    service, db, _ = fixture
    digest = plan(fixture).plan_sha256
    apply(fixture, digest)
    setattr(db.rows[target][0], field, value)
    with pytest.raises(service.PilotPracticeBindingError):
        apply(fixture, digest)


def test_unconfirmed_or_wrong_plan_cannot_write(fixture):
    service, db, _ = fixture
    for digest in ("", "d" * 64):
        with pytest.raises(service.PilotPracticeBindingError):
            apply(fixture, digest or "invalid")
    assert len(db.rows[Grant]) == 1 and not db.rows[Binding]


def test_invalid_closed_base_cohort_cannot_be_bound(fixture):
    service, db, _ = fixture
    digest = plan(fixture).plan_sha256
    db.cohort_valid = False
    with pytest.raises(service.PilotPracticeBindingError, match="closed pilot cohort"):
        apply(fixture, digest)
    assert len(db.rows[Grant]) == 1 and db.rows[Grant][0].revoked_at is None


def test_post_binding_audit_failure_rolls_back_entire_successor_and_receipts(fixture):
    service, db, _ = fixture
    digest = plan(fixture).plan_sha256
    db.fail_post_audit = True
    with pytest.raises(service.PilotPracticeBindingError, match="closed pilot cohort"):
        apply(fixture, digest)
    assert len(db.rows[Grant]) == 1 and db.rows[Grant][0].revoked_at is None
    assert not db.rows[Binding] and not db.rows[Receipt]


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
def test_pending_unrelated_session_work_refuses_before_any_lock_or_rollback(
    fixture, pending
):
    service, db, request = fixture
    session = Session(db)
    getattr(session, pending).append("unrelated-work")
    with pytest.raises(service.PilotPracticeBindingError, match="clean dedicated"):
        service.apply_pilot_practice_binding(
            request, confirmed_plan_sha256="e" * 64, session=session
        )
    assert not db.order and getattr(session, pending) == ["unrelated-work"]


def test_observation_expiring_during_grant_lock_wait_refuses_before_tombstone(fixture):
    service, db, request = fixture
    digest = plan(fixture).plan_sha256

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(hours=1)

    class WaitSession(Session):
        def exec(self, query):
            result = super().exec(query)
            if query.target is Grant and query.lock:
                service._evaluate.__globals__["datetime"] = ExpiredClock
            return result

    with pytest.raises(service.PilotPracticeBindingError, match="expired"):
        service.apply_pilot_practice_binding(
            request, confirmed_plan_sha256=digest, session=WaitSession(db)
        )
    assert len(db.rows[Grant]) == 1 and db.rows[Grant][0].revoked_at is None
