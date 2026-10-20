"""Offline safety and SQL fixture tests; no Core startup or live credentials.

Run with --confcutdir=tests/offline. SQLite tests exercise the real seed body,
not PostgreSQL lock semantics or live vendor/deployed configuration.
"""

import ast
import hashlib
import runpy
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest

from sqlalchemy.orm import registry
from sqlmodel import Field, Session, SQLModel, create_engine, select

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = runpy.run_path(str(ROOT / "scripts/seed_staging_manual_qa.py"))


def load_body(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text())
    selected: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert {node.name for node in selected if isinstance(node, ast.FunctionDef)} == set(
        names
    )
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"),
        namespace,
    )
    return namespace


def valid_env():
    return {
        "PYTHON_ENV": "staging",
        "QA_SYNTHETIC_ONLY": "yes",
        "AUTO_MIGRATE_ON_STARTUP": "false",
        "DATABASE_URL": "postgresql://operator:secret@qa.invalid/synthetic",
        "CLERK_PRIVATE_KEY": "sk_test_synthetic",
        "QA_CLERK_KEY_SHA256": hashlib.sha256(b"sk_test_synthetic").hexdigest(),
        "STRIPE_LIVE_MODE": "false",
        "STRIPE_SECRET_KEY": "sk_test_synthetic",
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("PYTHON_ENV", "production"),
        ("QA_SYNTHETIC_ONLY", ""),
        ("AUTO_MIGRATE_ON_STARTUP", "true"),
        ("AUTO_MIGRATE_ON_STARTUP", ""),
        ("DATABASE_URL", "postgresql://operator:secret@prod.invalid/synthetic"),
        ("DATABASE_URL", "postgresql://operator:secret@qa.invalid/real"),
        (
            "DATABASE_URL",
            "postgresql://operator:secret@qa.invalid/synthetic?host=prod.invalid",
        ),
        ("CLERK_PRIVATE_KEY", "sk_live_synthetic"),
        ("QA_CLERK_KEY_SHA256", "0" * 64),
        ("STRIPE_LIVE_MODE", "true"),
        ("STRIPE_LIVE_MODE", ""),
        ("STRIPE_SECRET_KEY", "sk_live_synthetic"),
    ],
)
def test_target_guards(key, value):
    env = valid_env()
    env[key] = value
    with pytest.raises(ValueError):
        SCRIPT["validate_target"](env, "qa.invalid", "synthetic")


def test_matching_target_and_offline_default(monkeypatch, capsys):
    SCRIPT["validate_target"](valid_env(), "qa.invalid", "synthetic")
    monkeypatch.setenv("PYTHON_ENV", "production")
    assert SCRIPT["main"](["accounts"]) == 0
    assert "DRY RUN" in capsys.readouterr().out


def test_apply_refuses_before_app_import_and_redacts(monkeypatch, capsys):
    monkeypatch.setenv("PYTHON_ENV", "production")
    before = set(sys.modules)
    assert SCRIPT["main"](["accounts", "--apply"]) == 1
    assert not any(
        name == "app" or name.startswith("app.") for name in set(sys.modules) - before
    )
    assert "secret" not in capsys.readouterr().err


def identity(email, marker="cosmaneura-staging-manual-qa-v1"):
    return SimpleNamespace(
        id=f"user_{email}",
        banned=False,
        locked=False,
        private_metadata={"manual_qa_seed": marker},
        primary_email_address_id="primary",
        email_addresses=[
            SimpleNamespace(
                id="primary",
                email_address=email,
                verification=SimpleNamespace(status="verified"),
            )
        ],
    )


@pytest.fixture
def adapter(monkeypatch):
    environments = ModuleType("app.environments")
    setattr(environments, "is_staging", lambda: True)
    monkeypatch.setitem(sys.modules, "app.environments", environments)
    rows, calls = {}, []

    def create(**kwargs):
        email = kwargs["email_address"][0]
        calls.append(kwargs)
        rows[email] = [identity(email)]
        return rows[email][0]

    ns = load_body(
        "app/configuration/clerk.py",
        ["provision_manual_qa_users"],
        {
            "CLERK_PRIVATE_KEY": "sk_test_synthetic",
            "Mapping": Mapping,
            "MANUAL_QA_SEED": "cosmaneura-staging-manual-qa-v1",
            "MANUAL_QA_EMAILS": SCRIPT["EMAILS"],
            "clerk": SimpleNamespace(
                users=SimpleNamespace(
                    list=lambda request: rows.get(request["email_address"][0], []),
                    create=create,
                )
            ),
        },
    )
    return ns, rows, calls


def test_identity_creation_and_rerun_never_resets_password(adapter):
    ns, rows, calls = adapter
    passwords = {email: "fictional-passphrase-123" for email in SCRIPT["EMAILS"]}
    first = ns["provision_manual_qa_users"](passwords)
    assert ns["provision_manual_qa_users"](passwords) == first
    assert len(calls) == 2
    assert all(
        "skip_password_checks" not in call and "legal_accepted_at" not in call
        for call in calls
    )


@pytest.mark.parametrize(
    "problem", ["unmarked", "duplicate", "locked", "unverified", "missing_metadata"]
)
def test_all_accounts_preflight_before_first_write(adapter, problem):
    ns, rows, calls = adapter
    email = "doctor@cosmaneura.com"
    user = identity(email, "unowned" if problem == "unmarked" else ns["MANUAL_QA_SEED"])
    user.locked = problem == "locked"
    if problem == "unverified":
        user.email_addresses[0].verification.status = "unverified"
    if problem == "missing_metadata":
        user.private_metadata = None
    rows[email] = [user, user] if problem == "duplicate" else [user]
    with pytest.raises(ValueError):
        ns["provision_manual_qa_users"](
            {email: "fictional-passphrase-123" for email in SCRIPT["EMAILS"]}
        )
    assert calls == []


class SeedModel(SQLModel, registry=registry()):
    pass


class SeedPractice(SeedModel, table=True):
    id: str = Field(primary_key=True)
    legal_name: str
    billing_exempt: bool = False
    stripe_subscription_id: str = "sub_synthetic"
    subscription_status: str = "active"


class SeedDoctor(SeedModel, table=True):
    id: str = Field(primary_key=True)
    email: str
    clerk_id: str
    onboarding_completed_at: datetime | None = datetime(2026, 10, 3)


class SeedMember(SeedModel, table=True):
    doctor_id: str = Field(primary_key=True)
    practice_id: str
    role: str


class SeedPatient(SeedModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    doctor_id: str
    name: str


class SeedAppointment(SeedModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    doctor_id: str
    patient_id: str
    external_id: str
    created_by_system: str
    appointment_date: datetime
    appointment_name: str
    appointment_notes_preview: str


@pytest.fixture
def fixtures():
    engine = create_engine("sqlite://")
    SeedModel.metadata.create_all(engine)

    @contextmanager
    def get_session():
        with Session(engine) as session:
            yield session

    roles = {"biller@cosmaneura.com": "owner", "doctor@cosmaneura.com": "workforce"}
    with get_session() as session:
        session.add(
            SeedPractice(
                id="practice_qa", legal_name="Cosmaneura Manual QA - Synthetic Practice"
            )
        )
        for email, role in roles.items():
            session.add(SeedDoctor(id=email, email=email, clerk_id="user_" + email))
            session.add(
                SeedMember(doctor_id=email, practice_id="practice_qa", role=role)
            )
        session.commit()
    ns = load_body(
        "app/services/manual_qa_seed.py",
        ["seed_manual_qa_fixtures"],
        {
            "datetime": datetime,
            "timedelta": timedelta,
            "select": select,
            "get_session": get_session,
            "is_staging": lambda: True,
            "Practice": SeedPractice,
            "Doctor": SeedDoctor,
            "PracticeMember": SeedMember,
            "Patient": SeedPatient,
            "Appointment": SeedAppointment,
            "QA_PRACTICE_NAME": "Cosmaneura Manual QA - Synthetic Practice",
            "QA_ROLES": roles,
            "QA_PATIENT_NAME": "SYNTHETIC QA - Alex Example",
            "QA_VISIT_KEY": "cosmaneura-manual-qa-v1",
            "manual_qa_identity_owned": lambda clerk_id: True,
            "verified_primary_email": lambda clerk_id: clerk_id.removeprefix("user_"),
            "resolve_doctor_access": lambda doctor: SimpleNamespace(
                allowed=True, baa_in_force=True, entitlement_in_force=True
            ),
        },
    )
    yield ns, get_session
    engine.dispose()


def test_fixtures_are_scoped_and_idempotent(fixtures):
    ns, sessions = fixtures
    ns["seed_manual_qa_fixtures"]("practice_qa")
    with sessions() as session:
        before = [
            (v.id, v.appointment_date)
            for v in session.exec(select(SeedAppointment)).all()
        ]
    ns["seed_manual_qa_fixtures"]("practice_qa")
    with sessions() as session:
        patients = session.exec(select(SeedPatient)).all()
        visits = session.exec(select(SeedAppointment)).all()
        assert len(patients) == 2 and len(visits) == 4
        assert [(v.id, v.appointment_date) for v in visits] == before
        owners = {p.id: p.doctor_id for p in patients}
        assert all(owners[v.patient_id] == v.doctor_id for v in visits)


@pytest.mark.parametrize(
    "failure", ["denied", "unowned", "email", "wrong_practice", "exempt"]
)
def test_preflight_denial_has_no_fixture_writes(fixtures, failure):
    ns, sessions = fixtures
    if failure == "denied":
        ns["resolve_doctor_access"] = lambda doctor: SimpleNamespace(
            allowed=False, baa_in_force=True, entitlement_in_force=True
        )
    elif failure == "unowned":
        ns["manual_qa_identity_owned"] = lambda clerk_id: False
    elif failure == "email":
        ns["verified_primary_email"] = lambda clerk_id: "other@cosmaneura.com"
    else:
        with sessions() as session:
            if failure == "exempt":
                row = session.get(SeedPractice, "practice_qa")
                row.billing_exempt = True
            else:
                row = session.get(SeedMember, "doctor@cosmaneura.com")
                row.practice_id = "practice_other"
            session.add(row)
            session.commit()
    with pytest.raises(ValueError):
        ns["seed_manual_qa_fixtures"]("practice_qa")
    with sessions() as session:
        assert session.exec(select(SeedPatient)).all() == []
        assert session.exec(select(SeedAppointment)).all() == []


def test_late_scope_collision_rolls_back_all_new_fixtures(fixtures):
    ns, sessions = fixtures
    with sessions() as session:
        session.add(
            SeedAppointment(
                doctor_id="doctor@cosmaneura.com",
                patient_id="unrelated_patient",
                external_id="cosmaneura-manual-qa-v1:-7",
                created_by_system="gui",
                appointment_date=datetime(2026, 10, 3),
                appointment_name="Existing visit",
                appointment_notes_preview="Synthetic existing data",
            )
        )
        session.commit()
    with pytest.raises(ValueError):
        ns["seed_manual_qa_fixtures"]("practice_qa")
    with sessions() as session:
        assert session.exec(select(SeedPatient)).all() == []
        visits = session.exec(select(SeedAppointment)).all()
        assert len(visits) == 1 and visits[0].created_by_system == "gui"


def test_partial_clerk_creation_is_recoverable(adapter):
    ns, rows, calls = adapter
    original = ns["clerk"].users.create

    def fail_second(**kwargs):
        if kwargs["email_address"] == ["doctor@cosmaneura.com"]:
            raise RuntimeError("Synthetic provider outage")
        return original(**kwargs)

    ns["clerk"].users.create = fail_second
    passwords = {email: "fictional-passphrase-123" for email in SCRIPT["EMAILS"]}
    with pytest.raises(RuntimeError):
        ns["provision_manual_qa_users"](passwords)
    assert list(rows) == ["biller@cosmaneura.com"]
    ns["clerk"].users.create = original
    assert len(ns["provision_manual_qa_users"](passwords)) == 2
    assert len(calls) == 2
