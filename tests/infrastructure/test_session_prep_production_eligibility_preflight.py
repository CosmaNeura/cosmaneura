from __future__ import annotations

import hashlib
import json
import secrets
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.services.organization_access import (
    RosterApprovalInput,
    claim_roster_access,
    configure_provider_organization,
    import_roster_approvals,
)
from app.services.session_prep_provider_access import (
    resolve_session_prep_provider_access,
)
from scripts import session_prep_production_eligibility_preflight as preflight

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember

from tests.factories import save_practice_member
from tests.routes.utils import provision_practice_in_force


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None


class _AggregateConnection:
    def __init__(
        self,
        classifications: list[tuple[Any, ...]],
        *,
        integrity_overrides: dict[str, int] | None = None,
        migration_heads: list[str] | None = None,
    ) -> None:
        integrity = dict.fromkeys(preflight.INTEGRITY_CHECKS, 0)
        integrity.update(integrity_overrides or {})
        self.integrity = [(label, count) for label, count in integrity.items()]
        self.classifications = classifications
        self.migration_heads = migration_heads or ["current_head"]

    def execute(
        self,
        query: str,
        params: tuple[Any, ...] = (),
    ) -> _Rows:
        if query == preflight.INTEGRITY_QUERY:
            assert params == ()
            return _Rows(self.integrity)
        if query == preflight.MIGRATION_HEAD_QUERY:
            assert params == ()
            return _Rows([(head,) for head in self.migration_heads])
        if query == preflight.ELIGIBILITY_QUERY:
            assert params[1] == preflight.BAA_CURRENT_REFERENCE
            return _Rows(self.classifications)
        raise AssertionError("unexpected query")


def _contract(head: str = "current_head") -> preflight.RepositoryMigrationContract:
    return preflight.RepositoryMigrationContract(
        head=head,
        ancestors=frozenset({head, *preflight.REQUIRED_SESSION_PREP_ANCESTORS}),
    )


def test_report_is_fixed_aggregate_only_and_deterministically_digested() -> None:
    observed_at = datetime(2026, 10, 28, 18, 30, tzinfo=UTC)
    connection = _AggregateConnection([("eligible", 3), ("baa_not_in_force", 2)])

    first = preflight.collect_inventory(
        connection,
        observed_at=observed_at,
        rollout_enabled=False,
        migration_contract=_contract(),
    )
    second = preflight.collect_inventory(
        connection,
        observed_at=observed_at,
        rollout_enabled=False,
        migration_contract=_contract(),
    )

    assert first == second
    assert first == {
        "denial_reason_counts": {
            "provider_unavailable": 0,
            "pilot_grant_revoked": 0,
            "onboarding_incomplete": 0,
            "practice_membership_required": 0,
            "practice_membership_inactive": 0,
            "practice_access_not_authorized": 0,
            "baa_not_in_force": 2,
            "entitlement_not_in_force": 0,
        },
        "eligible": 3,
        "environment": "production",
        "evidence_sha256": first["evidence_sha256"],
        "migration_head": "current_head",
        "observed_at": "2026-10-28T18:30:00.000000Z",
        "rollout_enabled": False,
        "schema_version": 1,
        "total": 5,
    }
    unsigned = {key: value for key, value in first.items() if key != "evidence_sha256"}
    assert (
        first["evidence_sha256"]
        == hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    serialized = json.dumps(first)
    for forbidden in (
        "user_",
        "@example.com",
        "doctor_",
        "patient_",
        "appointment_",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("integrity_overrides", "migration_heads", "contract"),
    [
        ({"unknown_practice_subscription_status": 1}, None, _contract()),
        ({"duplicate_practice_membership": 1}, None, _contract()),
        ({}, ["head_a", "head_b"], _contract("head_a")),
        ({}, ["stale_head"], _contract()),
        (
            {},
            ["current_head"],
            preflight.RepositoryMigrationContract(
                head="current_head",
                ancestors=frozenset({"current_head"}),
            ),
        ),
    ],
)
def test_inventory_refuses_unknown_ambiguous_or_incompatible_state(
    integrity_overrides: dict[str, int],
    migration_heads: list[str] | None,
    contract: preflight.RepositoryMigrationContract,
) -> None:
    connection = _AggregateConnection(
        [("eligible", 1)],
        integrity_overrides=integrity_overrides,
        migration_heads=migration_heads,
    )

    with pytest.raises(preflight.InventoryFailure):
        preflight.collect_inventory(
            connection,
            observed_at=datetime(2026, 10, 28, tzinfo=UTC),
            rollout_enabled=False,
            migration_contract=contract,
        )


def test_command_refuses_before_database_access_outside_production(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PYTHON_ENV", "staging")
    monkeypatch.setattr(
        preflight.psycopg,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("database access must not occur")
        ),
    )

    assert preflight.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Session Prep production eligibility preflight refused.\n"


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://user:password@localhost:5432/production?sslmode=require",
        "postgresql://user:password@cosmaneura-prod-postgres.postgres.database.azure.com:5433/production?sslmode=require",
        "postgresql://user:password@cosmaneura-prod-postgres.postgres.database.azure.com:5432/staging?sslmode=require",
        "postgresql://user:password@cosmaneura-prod-postgres.postgres.database.azure.com:5432/production",
        "postgresql://user:password@cosmaneura-prod-postgres.postgres.database.azure.com:5432/production?sslmode=require&application_name=other",
    ],
)
def test_production_database_url_rejects_wrong_endpoint_before_connect(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connected = False

    def refuse_connect(*args: Any, **kwargs: Any) -> None:
        nonlocal connected
        connected = True

    monkeypatch.setenv("PYTHON_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(preflight.psycopg, "connect", refuse_connect)

    assert preflight.main() == 2
    assert connected is False
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Session Prep production eligibility preflight failed.\n"


class _MainConnection:
    def __init__(self) -> None:
        self.rolled_back = False

    def __enter__(self) -> _MainConnection:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _Rows:
        assert params == ()
        if query == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY":
            return _Rows([])
        if query == preflight.SERVER_IDENTITY_QUERY:
            return _Rows([("production", "public", "on")])
        raise AssertionError("unexpected query")

    def rollback(self) -> None:
        self.rolled_back = True


def test_command_binds_canonical_endpoint_and_read_only_transaction(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_url = (
        "postgresql://user:password@"
        "cosmaneura-prod-postgres.postgres.database.azure.com:5432/"
        "production?sslmode=require"
    )
    connection = _MainConnection()
    observed_connect: dict[str, Any] = {}

    def connect(value: str, **kwargs: Any) -> _MainConnection:
        observed_connect.update({"database_url": value, **kwargs})
        return connection

    report = {
        "environment": "production",
        "eligible": 0,
        "total": 0,
    }
    monkeypatch.setenv("PYTHON_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("SESSION_PREP_PROVIDER_ENABLED", "false")
    monkeypatch.setattr(preflight.psycopg, "connect", connect)
    monkeypatch.setattr(
        preflight,
        "collect_inventory",
        lambda connection, **kwargs: report,
    )

    assert preflight.main() == 0
    assert observed_connect == {
        "database_url": database_url,
        "connect_timeout": preflight.CONNECT_TIMEOUT_SECONDS,
        "application_name": "session_prep_production_eligibility_preflight",
        "options": (
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={preflight.STATEMENT_TIMEOUT_MILLISECONDS} "
            "-c timezone=UTC"
        ),
    }
    assert connection.rolled_back is True
    captured = capsys.readouterr()
    assert json.loads(captured.out) == report
    assert captured.err == ""


def _write_revision(path: Path, revision: str, down_revision: object) -> None:
    path.write_text(
        f"revision = {revision!r}\ndown_revision = {down_revision!r}\n",
        encoding="utf-8",
    )


def test_repository_migration_contract_rejects_disconnected_cycle(
    tmp_path: Path,
) -> None:
    versions = tmp_path / "migrations" / "versions"
    versions.mkdir(parents=True)
    _write_revision(versions / "base.py", "base", None)
    _write_revision(versions / "head.py", "head", "base")
    _write_revision(versions / "cycle_a.py", "cycle_a", "cycle_b")
    _write_revision(versions / "cycle_b.py", "cycle_b", "cycle_a")

    with pytest.raises(preflight.InventoryFailure):
        preflight.repository_migration_contract(tmp_path)


def test_repository_migration_contract_rejects_reachable_cycle(
    tmp_path: Path,
) -> None:
    versions = tmp_path / "migrations" / "versions"
    versions.mkdir(parents=True)
    _write_revision(versions / "base.py", "base", None)
    _write_revision(versions / "cycle_a.py", "cycle_a", "cycle_b")
    _write_revision(versions / "cycle_b.py", "cycle_b", "cycle_a")
    _write_revision(versions / "head.py", "head", ("cycle_a", "base"))

    with pytest.raises(
        preflight.InventoryFailure,
        match="migration graph contains a cycle",
    ):
        preflight.repository_migration_contract(tmp_path)


def _doctor(label: str, *, onboarded: bool = True) -> Doctor:
    nonce = secrets.token_hex(4)
    return Doctor(
        clerk_id=f"user_inventory_{label}_{nonce}",
        email=f"inventory-{label}-{nonce}@example.com",
        biography="",
        onboarding_completed_at=(datetime.now(UTC) if onboarded else None),
    ).save()


def _pilot_grant(
    doctor: Doctor,
    practice: Practice,
    *,
    observed_at: datetime,
    revoked: bool,
    label: str,
) -> PilotAccessGrandfatherGrant:
    assert doctor.email is not None
    email = doctor.email.lower()
    revoked_at = observed_at - timedelta(days=1) if revoked else None
    return PilotAccessGrandfatherGrant(
        clerk_id=doctor.clerk_id,
        doctor_id_snapshot=doctor.id,
        practice_id_snapshot=practice.id,
        email_snapshot=email,
        email_sha256=hashlib.sha256(email.encode()).hexdigest(),
        source_ref=f"test:eligibility-inventory:{label}",
        source_sha256=hashlib.sha256(f"source:{label}".encode()).hexdigest(),
        grant_plan_sha256=hashlib.sha256(f"plan:{label}".encode()).hexdigest(),
        granted_by_ref="test-suite",
        grant_reason="Verify aggregate Session Prep eligibility precedence",
        granted_at=observed_at - timedelta(days=2),
        revoked_at=revoked_at,
        revoked_by_ref="test-suite" if revoked else None,
        revocation_reason="Test revocation" if revoked else None,
        revocation_plan_sha256=(
            hashlib.sha256(f"revoke:{label}".encode()).hexdigest() if revoked else None
        ),
        retain_until=(revoked_at + timedelta(days=2922) if revoked_at else None),
    ).save()


def _rostered_provider(label: str, *, observed_at: datetime) -> Doctor:
    nonce = secrets.token_hex(4)
    domain = f"inventory-{label}-{nonce}.example.com"
    doctor = Doctor(
        clerk_id=f"user_inventory_roster_{label}_{nonce}",
        email=f"provider@{domain}",
        biography="",
        onboarding_completed_at=observed_at - timedelta(minutes=1),
    ).save()
    practice = Practice(
        legal_name=f"Inventory {label} MCD Practice",
        join_code=f"I{secrets.token_hex(4).upper()[:7]}",
        email_domain=domain,
        origin_channel="mcd",
        is_solo=False,
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=observed_at - timedelta(days=1),
        negotiated_baa_reference=f"executed-baa:{domain}",
        billing_exempt=True,
    ).save()
    _, access_code, _ = configure_provider_organization(
        practice,
        legal_name=f"Inventory {label} MCD Organization",
        slug=f"inventory-{label}-{secrets.token_hex(5)}",
        email_domain=domain,
        operator_ref="test-eligibility-inventory",
        reason="Verify aggregate MCD roster eligibility",
        now=observed_at,
    )
    import_roster_approvals(
        practice,
        [RosterApprovalInput(email=doctor.email or "")],
        operator_ref="test-eligibility-inventory",
        reason="Authorize a synthetic MCD provider",
        source_ref=f"test://eligibility-inventory/{label}",
        source_sha256=hashlib.sha256(label.encode()).hexdigest(),
        now=observed_at,
    )
    claim_roster_access(
        doctor,
        supplied_code=access_code,
        now=observed_at + timedelta(seconds=1),
    )
    return doctor


def _collect_from_test_database(
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    with get_session() as session:
        driver_connection = session.connection().connection.driver_connection
        return preflight.collect_inventory(
            driver_connection,
            observed_at=observed_at,
            rollout_enabled=False,
        )


def _classification_counts(report: dict[str, Any]) -> Counter[str]:
    return Counter(
        {
            **report["denial_reason_counts"],
            "eligible": report["eligible"],
        }
    )


def test_aggregate_query_matches_the_live_strict_provider_predicate() -> None:
    observed_at = datetime.now(UTC)
    before = _classification_counts(
        _collect_from_test_database(observed_at=observed_at)
    )
    providers: list[Doctor] = []

    allowed = _doctor("allowed")
    provision_practice_in_force(allowed)
    providers.append(allowed)

    providers.append(_rostered_provider("rostered", observed_at=observed_at))

    deleted = _doctor("deleted")
    deleted.deleted_at = observed_at - timedelta(seconds=1)
    deleted.save()
    providers.append(deleted)

    revoked_pilot = _doctor("revoked-pilot")
    revoked_pilot_practice = provision_practice_in_force(revoked_pilot)
    _pilot_grant(
        revoked_pilot,
        revoked_pilot_practice,
        observed_at=observed_at,
        revoked=True,
        label="revoked-only",
    )
    providers.append(revoked_pilot)

    replacement_pilot = _doctor("replacement-pilot")
    replacement_practice = provision_practice_in_force(replacement_pilot)
    _pilot_grant(
        replacement_pilot,
        replacement_practice,
        observed_at=observed_at,
        revoked=True,
        label="revoked-predecessor",
    )
    _pilot_grant(
        replacement_pilot,
        replacement_practice,
        observed_at=observed_at,
        revoked=False,
        label="active-replacement",
    )
    providers.append(replacement_pilot)

    incomplete = _doctor("incomplete", onboarded=False)
    provision_practice_in_force(incomplete)
    providers.append(incomplete)

    no_membership = _doctor("no-membership")
    providers.append(no_membership)

    inactive = _doctor("inactive")
    provision_practice_in_force(inactive)
    inactive_member = PracticeMember.one(doctor_id=inactive.id)
    inactive_member.seat_state = "inactive"
    inactive_member.access_ends_at = None
    save_practice_member(inactive_member)
    providers.append(inactive)

    unauthorized = _doctor("unauthorized")
    provision_practice_in_force(unauthorized)
    unauthorized_member = PracticeMember.one(doctor_id=unauthorized.id)
    unauthorized_member.attested_at = None
    unauthorized_member.attestation_name = None
    save_practice_member(unauthorized_member)
    providers.append(unauthorized)

    no_baa = _doctor("no-baa")
    no_baa_practice = provision_practice_in_force(no_baa)
    no_baa_practice.baa_version = None
    no_baa_practice.baa_accepted_at = None
    no_baa_practice.baa_accepted_by_doctor_id = None
    no_baa_practice.baa_signature_name = None
    no_baa_practice.baa_signature_title = None
    no_baa_practice.save()
    providers.append(no_baa)

    unentitled = _doctor("unentitled")
    unentitled_practice = provision_practice_in_force(unentitled)
    unentitled_practice.billing_exempt = False
    unentitled_practice.billing_exempt_until = None
    unentitled_practice.trial_ends_at = None
    unentitled_practice.subscription_status = None
    unentitled_practice.save()
    providers.append(unentitled)

    expected: Counter[str] = Counter()
    for provider in providers:
        decision = resolve_session_prep_provider_access(provider, at=observed_at)
        classification = "eligible" if decision.allowed else decision.denial_reason
        assert classification is not None
        expected[classification] += 1

    after_report = _collect_from_test_database(observed_at=observed_at)
    after = _classification_counts(after_report)

    assert after - before == expected
    assert after_report["total"] - sum(before.values()) == len(providers)
    assert (
        after_report["migration_head"] == preflight.repository_migration_contract().head
    )
