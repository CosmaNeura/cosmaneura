"""Actual authority/scope SQL against controlled synthetic transaction rows.

The adapter records requested row locks; it is not a PostgreSQL concurrency test.
No database, service, account, or clinical payload is accessed.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.configuration.authority import AuthorityKernelSettings
from app.services import authority, provider_record_authority, view_as
from app.services import scribe_capture_scope as capture

from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityActor,
    AuthorityActorExternalIdentity,
    AuthorityPerson,
    AuthorityPersonBinding,
    AuthorityTenant,
    AuthorityTenantMembership,
    InteractionLineage,
    MountContextSession,
    MountContextToken,
)
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice, PracticeMember
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from app.models.view_as import ViewAsSession
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, BooleanClauseList, Null

REAL_VIEW_AS_GUARD = view_as.mount_context_session_blocked_by_view_as
TOKEN = "mctx_synthetic_lock_order_00000001"
SETTINGS = AuthorityKernelSettings(
    enabled=True,
    token_ttl_seconds=900,
    token_refresh_after_seconds=300,
    clerk_issuer="https://test.clerk.invalid",
)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def one_or_none(self):
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


def matches(expression, row):
    if isinstance(expression, BooleanClauseList):
        assert expression.operator is operators.and_
        return all(matches(clause, row) for clause in expression.clauses)
    assert expression.operator in (operators.eq, operators.is_)
    right = expression.right
    assert isinstance(right, (BindParameter, Null))
    value = right.value if isinstance(right, BindParameter) else None
    return getattr(row, expression.left.name) == value


class Transaction:
    def __init__(self, rows):
        self.rows = rows
        self.locks = []
        self.on_lock = lambda model, row: None

    def get(self, model, identifier, **_):
        return next(
            (row for row in self.rows.get(model, []) if row.id == identifier), None
        )

    def exec(self, statement):
        model = statement.column_descriptions[0]["entity"]
        assert model in self.rows, f"unmodelled table: {model.__name__}"
        rows = [
            row
            for row in self.rows[model]
            if all(matches(clause, row) for clause in statement._where_criteria)
        ]
        if statement._for_update_arg is not None:
            for row in rows:
                resource = (model.__name__, row.id)
                if resource not in self.locks:
                    self.locks.append(resource)
                    self.on_lock(model, row)
        return Rows(rows)


@pytest.fixture
def case(monkeypatch):
    now = datetime.now(UTC)
    rows = {}

    def row(model, identifier, **values):
        result = SimpleNamespace(id=identifier, **values)
        rows[model] = [result]
        return result

    doctor = row(Doctor, "doctor_synthetic", clerk_id="user_synthetic", deleted_at=None)
    practice = row(Practice, "practice_synthetic")
    member = row(
        PracticeMember,
        "member_synthetic",
        doctor_id=doctor.id,
        practice_id=practice.id,
        access_revoked_at=None,
        seat_state="active",
    )
    actor = row(AuthorityActor, "actor_synthetic", actor_kind="human", status="active")
    identity = row(
        AuthorityActorExternalIdentity,
        "identity_synthetic",
        actor_id=actor.id,
        issuer=SETTINGS.clerk_issuer,
        subject=doctor.clerk_id,
        revoked_at=None,
    )
    tenant = row(AuthorityTenant, "tenant_synthetic", status="active")
    person = row(AuthorityPerson, "person_synthetic", status="active")
    patient = row(Patient, "patient_synthetic", doctor_id=doctor.id)
    appointment = row(
        Appointment, "appointment_synthetic", doctor_id=doctor.id, patient_id=patient.id
    )
    binding = row(
        AuthorityPersonBinding,
        "binding_synthetic",
        actor_id=actor.id,
        tenant_id=tenant.id,
        person_id=person.id,
        binding_kind="provider_record",
        binding_ref=appointment.id,
        status="active",
        verified_at=now,
        revoked_at=None,
    )
    row(
        AuthorityTenantMembership,
        "membership_synthetic",
        actor_id=actor.id,
        tenant_id=tenant.id,
        membership_kind="provider",
        status="active",
    )
    mount = row(
        MountContextSession,
        "mount_synthetic",
        actor_id=actor.id,
        tenant_id=tenant.id,
        person_binding_id=binding.id,
        source_identity_id=identity.id,
        session_kind="authenticated",
        surface_ceiling="provider_assistant",
        auth_tier_ceiling="provider",
        status="active",
        revoked_at=None,
        absolute_expires_at=now + timedelta(minutes=10),
    )
    row(
        MountContextToken,
        "token_synthetic",
        session_id=mount.id,
        revoked_at=None,
        expires_at=now + timedelta(minutes=5),
        token_hash=hashlib.sha256(TOKEN.encode()).hexdigest(),
    )
    row(
        ScribeCaptureProviderLink,
        "link_synthetic",
        actor_id=actor.id,
        tenant_id=tenant.id,
        source_identity_id=identity.id,
        doctor_id=doctor.id,
        practice_id=practice.id,
        scribe_tenant_id=uuid4(),
        reviewed_at=now,
        review_ref="synthetic_review_00001",
        revoked_at=None,
    )
    rows[ViewAsSession] = []
    transaction = Transaction(rows)
    monkeypatch.setattr(
        view_as, "mount_context_session_blocked_by_view_as", REAL_VIEW_AS_GUARD
    )
    monkeypatch.setattr(capture, "doctor_access_in_force", lambda _: True)
    monkeypatch.setattr(
        provider_record_authority, "doctor_access_in_force", lambda _: True
    )
    monkeypatch.setattr(
        provider_record_authority.configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SimpleNamespace(allows=lambda _: True),
    )
    context = authority._context_for_session(
        transaction, mount, clerk_issuer=SETTINGS.clerk_issuer
    )
    turn = uuid4()
    row(
        InteractionLineage,
        "lineage_synthetic",
        session_id=mount.id,
        interaction_ref="ix_synthetic_lock_order",
        workload_client_id="synthetic_workload",
        client_turn_id=turn,
        binding_fingerprint=authority._binding_fingerprint(context),
    )
    arguments = dict(
        session=transaction,
        workload_client_id="synthetic_workload",
        mount_context_token=TOKEN,
        interaction_ref="ix_synthetic_lock_order",
        client_turn_id=turn,
        appointment_ref=appointment.id,
        settings=SETTINGS,
    )
    return SimpleNamespace(
        transaction=transaction,
        arguments=arguments,
        doctor=doctor,
        practice=practice,
        member=member,
        identity=identity,
        mount=mount,
    )


def test_capture_and_exchange_use_the_same_owner_lock_order(case):
    exchange = Transaction(case.transaction.rows)
    provider_record_authority.lock_provider_record_owner(
        session=exchange, clerk_subject=case.doctor.clerk_id
    )
    result = capture._authorized_scope(**case.arguments)
    assert result["doctor_id"] == case.doctor.id
    assert result["practice_id"] == case.practice.id
    assert exchange.locks == [
        ("Practice", case.practice.id),
        ("Doctor", case.doctor.id),
    ]
    assert case.transaction.locks[:2] == exchange.locks


def test_invalid_token_never_chooses_a_doctor_from_request_input(case):
    with pytest.raises(authority.ContextTokenInvalidError):
        capture._authorized_scope(
            **{
                **case.arguments,
                "mount_context_token": TOKEN + "invalid",
                "appointment_ref": "appointment_foreign",
            }
        )
    assert case.transaction.locks == []


@pytest.mark.parametrize("change", ["revoked", "subject"])
def test_identity_changed_while_waiting_for_its_lock_is_denied(case, change):
    def change_identity(model, row):
        if model is AuthorityActorExternalIdentity:
            if change == "revoked":
                row.revoked_at = datetime.now(UTC)
            else:
                row.subject = "user_foreign"

    case.transaction.on_lock = change_identity
    with pytest.raises(authority.AuthorityKernelError):
        capture._authorized_scope(**case.arguments)
    assert not any(
        name == "ScribeCaptureProviderLink" for name, _ in case.transaction.locks
    )


def test_membership_move_during_practice_lock_does_not_lock_second_practice(case):
    def move(model, _):
        if model is Practice:
            case.member.practice_id = "practice_foreign"

    case.transaction.on_lock = move
    with pytest.raises(authority.AuthorityKernelError):
        capture._authorized_scope(**case.arguments)
    assert [
        identifier for name, identifier in case.transaction.locks if name == "Practice"
    ] == [case.practice.id]


def test_view_as_still_denies_capture_after_owner_locks(case, monkeypatch):
    view = SimpleNamespace(
        id="view_synthetic",
        actor_doctor_id=case.doctor.id,
        ended_at=None,
        actor_issuer_sha256=hashlib.sha256(SETTINGS.clerk_issuer.encode()).hexdigest(),
        actor_subject_sha256=hashlib.sha256(case.doctor.clerk_id.encode()).hexdigest(),
        environment="staging",
        capability_grant_id="capability_synthetic",
        absolute_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    case.transaction.rows[ViewAsSession] = [view]
    monkeypatch.setattr(
        view_as,
        "get_active_superadmin_capability",
        lambda *_args, **_kwargs: SimpleNamespace(id=view.capability_grant_id),
    )
    with pytest.raises(authority.AuthorityRequestDeniedError):
        capture._authorized_scope(**case.arguments)
    assert not any(
        name == "ScribeCaptureProviderLink" for name, _ in case.transaction.locks
    )


@pytest.mark.parametrize("target", [MountContextToken, MountContextSession])
def test_token_or_mount_revoked_during_owner_lock_is_still_denied(case, target):
    def revoke(model, _):
        if model is Practice:
            case.transaction.rows[target][0].revoked_at = datetime.now(UTC)

    case.transaction.on_lock = revoke
    with pytest.raises(authority.ContextTokenInvalidError):
        capture._authorized_scope(**case.arguments)
    assert not any(
        name == "ScribeCaptureProviderLink" for name, _ in case.transaction.locks
    )


@pytest.mark.parametrize("field", ["doctor_id", "practice_id"])
def test_changed_capture_link_cannot_choose_another_owner_to_lock(case, field):
    case.transaction.rows[ScribeCaptureProviderLink][0].__dict__[field] = "foreign"
    case.transaction.rows[Doctor].append(
        SimpleNamespace(id="foreign", clerk_id="user_foreign", deleted_at=None)
    )
    case.transaction.rows[Practice].append(SimpleNamespace(id="foreign"))
    with pytest.raises(capture.CaptureScopeDenied):
        capture._authorized_scope(**case.arguments)
    assert all(identifier != "foreign" for _, identifier in case.transaction.locks)


def test_capture_does_not_add_the_separate_session_prep_allowlist(case, monkeypatch):
    monkeypatch.setattr(
        provider_record_authority.configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SimpleNamespace(allows=lambda _: False),
    )
    assert capture._authorized_scope(**case.arguments)["doctor_id"] == case.doctor.id


def test_mount_identity_changed_during_first_lock_is_denied_before_view_as(
    case, monkeypatch
):
    def change_mount(model, _):
        if model is Practice:
            case.mount.source_identity_id = "identity_foreign"

    case.transaction.on_lock = change_mount
    monkeypatch.setattr(
        view_as,
        "mount_context_session_blocked_by_view_as",
        lambda *_args, **_kwargs: pytest.fail(
            "view-as reached with a changed lock owner"
        ),
    )
    with pytest.raises(capture.CaptureScopeDenied):
        capture._authorized_scope(**case.arguments)
