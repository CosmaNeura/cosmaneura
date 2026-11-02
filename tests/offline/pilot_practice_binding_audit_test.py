"""Actual audit branch and additive PostgreSQL DDL, without database execution."""

import ast
import hashlib
import io
import json
import runpy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pilot_practice_binding_test import (
    Amendment,
    Binding,
    Grant,
    IdentityReceipt,
    Query,
    Receipt,
    Session,
    apply,
    plan,
)
from pilot_practice_binding_test import fixture as synthetic_fixture

from sqlalchemy import Column, String, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from sqlmodel import SQLModel

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fixture():
    return synthetic_fixture.__wrapped__()


def audit_namespace(service, base_source, base_plan):
    tree = ast.parse((ROOT / "app/services/pilot_access_audit.py").read_text())
    tree.body = [
        node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    ns = dict(
        __name__=__name__,
        dataclass=dataclass,
        hashlib=hashlib,
        hmac=__import__("hmac"),
        cast=lambda kind, value: value,
        Session=Session,
        select=Query,
        PilotAccessGrandfatherGrant=Grant,
        PilotAccessPracticeBinding=Binding,
        PilotAccessContinuityAmendment=type("AmendmentBatch", (), {}),
        PilotAccessContinuityAmendmentIdentity=Amendment,
        PracticeAccessAudit=Receipt,
        AuthorityAuditEvent=IdentityReceipt,
        PracticeAccessAction=SimpleNamespace(
            doctor_identity_rebound=SimpleNamespace(value="doctor_identity_rebound"),
            pilot_practice_snapshot_bound=SimpleNamespace(
                value="pilot_practice_snapshot_bound"
            ),
        ),
        binding_evidence_valid=service.binding_evidence_valid,
        IDENTITY_REBIND_EVENT_TYPE="platform.doctor_identity.rebound",
        IDENTITY_REBIND_RESOURCE_TYPE="doctor_identity_rebind",
        IDENTITY_REBIND_ROUTE="cli:rebind-doctor-clerk-identity",
        PILOT_ACCESS_GRANDFATHER_STAGING_ACCOUNT_COUNT=6,
        PILOT_ACCESS_GRANDFATHER_REQUIRED_ACCOUNT_COUNT=47,
        required_pilot_access_grandfather_account_count=lambda: 6,
        ClerkCohortError=ValueError,
        GrandfatherGrantInput=lambda **kw: SimpleNamespace(**kw),
        ClerkCohortIdentity=lambda **kw: SimpleNamespace(**kw),
        canonical_clerk_email_pair_sha256=lambda entries: base_source,
        pilot_access_grant_plan_sha256=lambda *a, **kw: base_plan,
    )
    exec(compile(tree, "actual-pilot-audit", "exec"), ns)
    return SimpleNamespace(**ns)


def prepare_audit(fixture):
    service, db, request = fixture
    # Existing cohort hashing is an unchanged synthetic trust anchor here;
    # all new binding/successor/base-count validation executes the actual audit.
    original = db.rows[Grant][0]
    base_source, base_plan = original.source_sha256, original.grant_plan_sha256
    ns = audit_namespace(service, base_source, base_plan)
    batch_type = ns.PilotAccessContinuityAmendment
    db.rows[batch_type] = []
    for index in range(5):
        db.rows[Grant].append(
            Grant(
                id=f"grant_base_{index}",
                clerk_id=f"user_base_{index}",
                doctor_id_snapshot=None,
                practice_id_snapshot=None,
                email_snapshot=f"base{index}@example.com",
                email_sha256=hashlib.sha256(
                    f"base{index}@example.com".encode()
                ).hexdigest(),
                source_ref=original.source_ref,
                source_sha256=base_source,
                grant_plan_sha256=base_plan,
                granted_by_ref=original.granted_by_ref,
                grant_reason=original.grant_reason,
                granted_at=original.granted_at,
            )
        )
    return ns, db


def test_audit_preserves_six_base_lineages_with_one_bound_successor(fixture):
    ns, db = prepare_audit(fixture)
    preview = plan(fixture)
    apply(fixture, preview.plan_sha256)
    report = ns._audit_with_session(
        Session(db),
        expected_environment="staging",
        expected_clerk_issuer="https://synthetic.example.clerk.accounts.dev",
    )
    assert report.valid
    assert report.payload["base_count"] == report.payload["lineage_count"] == 6
    assert report.payload["total_count"] == 7 and report.payload["active_count"] == 6
    assert (
        report.payload["practice_binding_count"] == 1
        and report.payload["rebind_count"] == 0
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "missing-binding",
        "missing-receipt",
        "duplicate-binding",
        "tampered-plan",
        "wrong-environment",
        "wrong-issuer",
        "wrong-practice",
        "short-retention",
        "orphan-receipt",
        "amendment-collision",
    ],
)
def test_audit_rejects_missing_or_forged_binding_evidence(fixture, corruption):
    ns, db = prepare_audit(fixture)
    apply(fixture)
    if corruption == "missing-binding":
        db.rows[Binding].clear()
    elif corruption == "missing-receipt":
        db.rows[Receipt].clear()
    elif corruption == "duplicate-binding":
        db.rows[Binding].append(db.rows[Binding][0])
    elif corruption == "tampered-plan":
        db.rows[Binding][0].plan_sha256 = "e" * 64
    elif corruption == "wrong-environment":
        db.rows[Binding][0].environment = "production"
    elif corruption == "wrong-issuer":
        row = db.rows[Binding][0]
        p = json.loads(row.payload_json)
        p["issuer_sha256"] = "e" * 64
        row.payload_json = json.dumps(p, sort_keys=True, separators=(",", ":"))
    elif corruption == "wrong-practice":
        db.rows[Grant][-1].practice_id_snapshot = "practice_wrong"
    elif corruption == "short-retention":
        db.rows[Grant][0].retain_until = db.rows[Grant][0].revoked_at
    elif corruption == "orphan-receipt":
        db.rows[Receipt].append(
            Receipt(id="orphan", action="pilot_practice_snapshot_bound")
        )
    elif corruption == "amendment-collision":
        db.rows[Amendment].append(
            Amendment(grant_id=db.rows[Grant][0].id, amendment_id="missing_batch")
        )
    report = ns._audit_with_session(
        Session(db),
        expected_environment="staging",
        expected_clerk_issuer="https://synthetic.example.clerk.accounts.dev",
    )
    assert not report.valid


def test_generated_migration_is_additive_append_only_and_has_no_destructive_recovery():
    paths = list((ROOT / "migrations/versions").glob("*_6bd4416fe39c_*.py"))
    assert len(paths) == 1
    migration = runpy.run_path(str(paths[0]))
    assert migration["down_revision"] == "312b217ebba4"
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration["upgrade"]()
    sql = output.getvalue()
    assert "CREATE TABLE pilot_access_practice_binding" in sql
    assert "BEFORE UPDATE OR DELETE" in sql and "ERRCODE = '55000'" in sql
    for field in (
        "predecessor_grant_id",
        "successor_grant_id",
        "receipt_id",
        "plan_sha256",
    ):
        assert f"UNIQUE ({field})" in sql
    assert "ON DELETE CASCADE" not in sql and "DROP " not in sql
    assert "UPDATE pilot_access_grandfather_grant" not in sql
    with pytest.raises(RuntimeError, match="keep the additive"):
        migration["downgrade"]()


@pytest.mark.parametrize(
    "environment,issuer",
    [
        ("staging", None),
        (None, None),
        ("development", "https://synthetic.example.clerk.accounts.dev"),
        ("staging", "invalid"),
    ],
)
def test_binding_audit_requires_explicit_runtime_and_issuer_provenance(
    fixture, environment, issuer
):
    ns, db = prepare_audit(fixture)
    apply(fixture)
    report = ns._audit_with_session(
        Session(db), expected_environment=environment, expected_clerk_issuer=issuer
    )
    assert not report.valid


def test_actual_model_compiles_with_retained_foreign_keys_and_unique_bindings():
    for table in ("pilot_access_grandfather_grant", "practice_access_audit"):
        if table not in SQLModel.metadata.tables:
            Table(table, SQLModel.metadata, Column("id", String, primary_key=True))
    ns = runpy.run_path(str(ROOT / "app/models/pilot_access_practice_binding.py"))
    model = ns["PilotAccessPracticeBinding"]
    sql = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
    assert model.__table__.name == "pilot_access_practice_binding"
    for field in (
        "predecessor_grant_id",
        "successor_grant_id",
        "receipt_id",
        "plan_sha256",
    ):
        assert f"UNIQUE ({field})" in sql
    assert "ON DELETE CASCADE" not in sql
    assert "predecessor_grant_id <> successor_grant_id" in sql
