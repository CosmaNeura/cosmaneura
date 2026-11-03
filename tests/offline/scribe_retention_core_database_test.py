"""Real SQL receiver on a NEW owned PG16 dataset, never Core's test database.

Run explicitly with --confcutdir=tests/offline and plugin autoload disabled.
The lab/patient/Doctor schemas are loaded from actual model sources, without
app startup, ORM sessions, credentials or clinical data. All values are synthetic.
"""

import ast
import importlib
import importlib.util
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from typeid import TypeID

import sqlalchemy as sa
from sqlmodel import SQLModel

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2027, 2, 1, tzinfo=UTC)
CALLER, CUSTODY = "a" * 64, "c" * 64


def source(monkeypatch, name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sources():
    patch = pytest.MonkeyPatch()
    before = set(SQLModel.metadata.tables)
    for name, path in (
        ("app", ROOT / "app"),
        ("app.models", ROOT / "app/models"),
        ("app.services", ROOT / "app/services"),
    ):
        package = ModuleType(name)
        package.__path__ = [str(path)]
        patch.setitem(sys.modules, name, package)
    source(
        patch,
        "app.services.scribe_retention_catalog",
        "app/services/scribe_retention_catalog.py",
    )
    service = source(
        patch, "app.services.scribe_retention", "app/services/scribe_retention.py"
    )
    adapter = source(
        patch,
        "app.services.scribe_retention_core_database",
        "app/services/scribe_retention_core_database.py",
    )
    # Imports only explicit, audited model sources; app/models/__init__ never runs.
    for name in (
        "doctor",
        "patient",
        "appointment",
        "lab_panel",
        "lab_result",
        "doctor_note_schema",
        "ai_visit_transcript",
        "ai_visit_note",
        "scribe_cancellation",
        "scribe_retention",
    ):
        importlib.import_module(f"app.models.{name}")
    metadata = sa.MetaData()
    for name in sorted(set(SQLModel.metadata.tables) - before):
        SQLModel.metadata.tables[name].to_metadata(metadata)
    yield SimpleNamespace(service=service, adapter=adapter, metadata=metadata)
    for name in set(SQLModel.metadata.tables) - before:
        SQLModel.metadata.remove(SQLModel.metadata.tables[name])
    patch.undo()


class ApprovedCustody:
    """Synthetic stand-in only for separately trusted authorization/fences."""

    def __init__(self, module):
        self.module, self.original = module, None
        self.fence = True
        self.on_fence = None
        self.unresolved = False

    def authorize(self, connection, operation, inventory, at):
        if self.unresolved:
            return None
        if self.original is None:
            self.original = self.module.DatabaseAuthorization(
                operation.scope_sha256,
                inventory.schema_sha256,
                inventory.preservation_sha256,
                tuple(
                    self.module.RowAuthorization(row, CUSTODY, CALLER, False, False)
                    for row in inventory.candidates
                ),
                CUSTODY,
                CUSTODY,
                NOW + timedelta(days=1),
                True,
            )
        return self.original

    def verify_fences(self, connection, operation, authorization, at):
        if self.on_fence:
            self.on_fence(connection)
        return self.fence


def source_sql(relative, needle):
    tree = ast.parse((ROOT / relative).read_text())
    matches = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and needle in node.value
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.fixture
def f(sources):
    socket = os.environ.get("SCRIBE_RETENTION_SYNTHETIC_PG_SOCKET")
    if not socket:
        pytest.skip("explicit new owned synthetic PostgreSQL socket required")
    socket_path = Path(socket).resolve()
    assert (
        str(socket_path).startswith("/private/tmp/core-scribe-retention-core-db-pg16-")
        and socket_path.name == "socket"
    )
    assert socket_path.parent.joinpath("data/PG_VERSION").read_text().strip() == "16"
    engine = sa.create_engine(
        sa.URL.create(
            "postgresql+psycopg",
            username="jw",
            database="postgres",
            query={"host": str(socket_path), "port": "54491"},
        ),
        hide_parameters=True,
        connect_args={"options": "-c client_encoding=utf8"},
    )
    metadata = sa.MetaData()
    for table in sources.metadata.tables.values():
        table.to_metadata(metadata)
    # Synthetic non-PHI document classes supplement actual clinical schemas.
    for name in (
        "practice",
        "practice_member",
        "baa_acceptance",
        "stripe_event_receipt",
    ):
        sa.Table(
            name,
            metadata,
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("doctor_id", sa.String),
            sa.Column("effective_at", sa.DateTime(timezone=True)),
            sa.Column("retain_until", sa.DateTime(timezone=True)),
        )
    with engine.begin() as db:
        assert db.exec_driver_sql("SHOW listen_addresses").scalar_one() == ""
        assert (
            db.exec_driver_sql("SHOW server_version_num").scalar_one().startswith("16")
        )
        assert sa.inspect(db).get_table_names() == []
        metadata.create_all(db)
        # Representative ACTUAL history guard SQL, not unguarded surrogate proof.
        db.execute(
            sa.text(
                source_sql(
                    "migrations/versions/2026_08_20_f777b62b8152_harden_stripe_billing_state.py",
                    "CREATE OR REPLACE FUNCTION cosmas_reject_append_only_mutation",
                )
            )
        )
        db.execute(
            sa.text(
                source_sql(
                    "migrations/versions/2026_08_23_f70935048976_converge_visit_note_versioning_and_add_.py",
                    "CREATE TRIGGER ai_visit_note_reject_delete",
                )
            )
        )
    tables = dict(metadata.tables)
    ids = {
        name: TypeID(name)
        for name in (
            "doctor",
            "patient",
            "appointment",
            "lab_panel",
            "lab_result",
            "doctor_note_schema",
            "ai_visit_transcript",
            "visit_note",
        )
    }
    other = {
        name: TypeID(name) for name in ("doctor", "patient", "lab_panel", "lab_result")
    }
    accepted = NOW - timedelta(days=91)
    policy = {
        "grace_days": 90,
        "phi_scope": "core-and-scribe-clinical-records",
        "approval_sha256": CUSTODY,
    }
    operation_id = uuid4()
    operation_row = dict(
        id=operation_id,
        caller_sha256=CALLER,
        doctor_ref=str(ids["doctor"]),
        request_key_sha256=CUSTODY,
        preview_sha256=CUSTODY,
        inventory_sha256=sources.service.digest([]),
        inventory=[],
        policy=policy,
        accepted_at=accepted,
        export_until=accepted + timedelta(days=90),
        phase="read-only",
    )
    with engine.begin() as db:
        for identity, clerk in (
            (ids, "user_synthetic_primary"),
            (other, "user_synthetic_other"),
        ):
            db.execute(
                tables["doctor"]
                .insert()
                .values(id=identity["doctor"], clerk_id=clerk, biography=None)
            )
            db.execute(
                tables["patient"]
                .insert()
                .values(
                    id=identity["patient"],
                    doctor_id=identity["doctor"],
                    name="synthetic fixture",
                    date_of_birth=None,
                )
            )
            db.execute(
                tables["lab_panel"]
                .insert()
                .values(
                    id=identity["lab_panel"],
                    doctor_id=identity["doctor"],
                    patient_id=identity["patient"],
                    appointment_id=None,
                )
            )
            db.execute(
                tables["lab_result"]
                .insert()
                .values(
                    id=identity["lab_result"],
                    lab_panel_id=identity["lab_panel"],
                    doctor_id=identity["doctor"],
                    patient_id=identity["patient"],
                    biomarker="synthetic-marker",
                    raw_name="synthetic",
                    value=1.0,
                )
            )
        db.execute(
            tables["scribe_cancellation_operation"].insert().values(**operation_row)
        )
        for name in (
            "practice",
            "practice_member",
            "baa_acceptance",
            "stripe_event_receipt",
        ):
            db.execute(
                tables[name]
                .insert()
                .values(
                    id=f"synthetic-{name}",
                    doctor_id=str(ids["doctor"]),
                    effective_at=accepted,
                    retain_until=accepted.replace(
                        year=accepted.year
                        + (
                            7
                            if name in ("practice_member", "stripe_event_receipt")
                            else 6
                        )
                    ),
                )
            )
    operation = sources.service.Operation.from_row(operation_row)
    authority = ApprovedCustody(sources.adapter)
    database = sources.adapter.CoreClinicalDatabase(
        engine,
        tables,
        authority,
        target_tables=("lab_panel", "lab_result"),
        clock=lambda: NOW,
        enabled=True,
    )
    fixture = SimpleNamespace(
        **vars(sources),
        engine=engine,
        tables=tables,
        ids=ids,
        other=other,
        operation=operation,
        authority=authority,
        database=database,
    )
    try:
        yield fixture
    finally:
        with engine.begin() as db:
            metadata.drop_all(db)
            db.exec_driver_sql("DROP FUNCTION cosmas_reject_append_only_mutation()")
        engine.dispose()


def prepare(f):
    scans = {
        key: adapter.scan(f.operation, NOW)
        for key, adapter in f.database.adapters().items()
    }
    # An actual coordinator requires every other store/adapter before preparing.
    # These empty source-contract placeholders test committed SQL binding only;
    # they never qualify external copy closure or customer completion.
    for surface in f.service.BY_KEY:
        scans.setdefault(
            surface,
            f.service.Scan(
                surface, f.operation.scope_sha256, NOW, CUSTODY, CUSTODY, (), True, True
            ),
        )
    prepared: dict[str, Any] = dict(
        stage="prepared",
        manifest_sha256=CUSTODY,
        scans={name: f.service.pack_scan(scan) for name, scan in scans.items()},
        adapter_keys={
            name: f.service.work_key(f.operation, name, scan)
            for name, scan in scans.items()
            if f.service.BY_KEY[name].disposition == "erase"
        },
    )
    with f.engine.begin() as db:
        f.service.LedgerSession(
            db,
            f.tables["scribe_cancellation_operation"],
            f.tables["scribe_retention_event"],
        ).append(f.operation, "erasure-intent", prepared, NOW)
    return scans, prepared


def erase(f, scans, prepared):
    adapter = f.database.adapters()["core.lab_panel"]
    adapter.erase(
        f.operation, scans["core.lab_panel"], prepared["adapter_keys"]["core.lab_panel"]
    )


def count(f, name):
    with f.engine.connect() as db:
        return db.execute(
            sa.select(sa.func.count()).select_from(f.tables[name])
        ).scalar_one()


def test_actual_lab_fk_graph_is_erased_atomically_and_exactly(f):
    scans, prepared = prepare(f)
    assert scans["core.lab_panel"].resources[0].ref_sha256 == f.service.digest(
        ["core-primary-row-v1", "lab_panel", str(f.ids["lab_panel"].uuid)]
    )
    with f.engine.connect() as db:
        before = {
            name: db.execute(sa.select(f.tables[name])).mappings().all()
            for name in (
                "doctor",
                "practice_member",
                "baa_acceptance",
                "stripe_event_receipt",
            )
        }
    erase(f, scans, prepared)
    assert count(f, "lab_panel") == count(f, "lab_result") == 1
    with f.engine.connect() as db:
        for name, rows in before.items():
            assert db.execute(sa.select(f.tables[name])).mappings().all() == rows
        assert (
            db.execute(sa.select(f.tables["lab_result"].c.id)).scalar_one()
            == f.other["lab_result"]
        )
    # A second surface/restart uses the ORIGINAL committed graph and is idempotent.
    for name, adapter in f.database.adapters().items():
        adapter.erase(f.operation, scans[name], prepared["adapter_keys"][name])
        proof = adapter.verify(f.operation, scans[name], NOW)
        assert proof.remaining_count == 0 and proof.complete
        assert adapter.verify_preserved(f.operation, scans[name], NOW).unchanged


@pytest.mark.parametrize(
    "failure",
    (
        "new-owned-row",
        "changed-payload-without-updated-at",
        "custody-revoked",
        "shared",
        "legal-hold",
        "wrong-key",
        "login-drift",
        "retained-doc-drift",
    ),
)
def test_drift_or_incomplete_authority_never_partially_deletes(f, failure):
    scans, prepared = prepare(f)
    if failure == "new-owned-row":
        with f.engine.begin() as db:
            db.execute(
                f.tables["lab_result"]
                .insert()
                .values(
                    id=TypeID("lab_result"),
                    lab_panel_id=f.ids["lab_panel"],
                    doctor_id=f.ids["doctor"],
                    patient_id=f.ids["patient"],
                    biomarker="new-synthetic",
                    raw_name="synthetic",
                    value=2.0,
                )
            )
    elif failure == "changed-payload-without-updated-at":
        with f.engine.begin() as db:
            db.execute(
                f.tables["lab_result"]
                .update()
                .where(f.tables["lab_result"].c.id == f.ids["lab_result"])
                .values(value=3.0)
            )
    elif failure == "custody-revoked":
        f.authority.fence = False
    elif failure in ("shared", "legal-hold"):
        approval = f.authority.original
        assert approval
        items = list(approval.rows)
        items[0] = replace(
            items[0],
            **{"other_custodian" if failure == "shared" else "legal_hold": True},
        )
        f.authority.original = replace(approval, rows=tuple(items))
    elif failure == "wrong-key":
        prepared["adapter_keys"]["core.lab_panel"] = "b" * 64
    else:
        table = f.tables["doctor" if failure == "login-drift" else "baa_acceptance"]
        with f.engine.begin() as db:
            if failure == "login-drift":
                db.execute(
                    table.update()
                    .where(table.c.id == f.ids["doctor"])
                    .values(clerk_id="user_synthetic_changed")
                )
            else:
                db.execute(table.update().values(retain_until=NOW))
    with pytest.raises(f.adapter.DatabaseHold):
        erase(f, scans, prepared)
    assert count(f, "lab_panel") == 2
    assert count(f, "lab_result") == (3 if failure == "new-owned-row" else 2)


def test_post_delete_fence_loss_rolls_back_entire_graph(f):
    scans, prepared = prepare(f)
    calls = 0

    def lose_after_mutation(db):
        nonlocal calls
        calls += 1
        if calls == 2:
            f.authority.fence = False

    f.authority.on_fence = lose_after_mutation
    with pytest.raises(f.adapter.DatabaseHold):
        erase(f, scans, prepared)
    assert count(f, "lab_panel") == count(f, "lab_result") == 2


def test_uncommitted_or_missing_intent_prevents_external_sql_mutation(f):
    adapter = f.database.adapters()["core.lab_panel"]
    scan = adapter.scan(f.operation, NOW)
    with pytest.raises(f.adapter.DatabaseHold):
        adapter.erase(
            f.operation, scan, f.service.work_key(f.operation, scan.surface, scan)
        )
    assert count(f, "lab_panel") == count(f, "lab_result") == 2


def test_unknown_fk_dependent_is_held_and_not_cascaded(f):
    with f.engine.begin() as db:
        db.exec_driver_sql(
            "CREATE TABLE synthetic_unknown_copy (id text PRIMARY KEY, panel_id uuid REFERENCES lab_panel(id) ON DELETE CASCADE)"
        )
        db.execute(
            sa.text(
                "INSERT INTO synthetic_unknown_copy VALUES ('synthetic-unknown', :panel)"
            ),
            {"panel": str(f.ids["lab_panel"].uuid)},
        )
    scans, prepared = prepare(f)
    try:
        with pytest.raises(f.adapter.DatabaseHold):
            erase(f, scans, prepared)
        assert count(f, "lab_panel") == count(f, "lab_result") == 2
        with f.engine.connect() as db:
            assert (
                db.exec_driver_sql(
                    "SELECT count(*) FROM synthetic_unknown_copy"
                ).scalar_one()
                == 1
            )
    finally:
        with f.engine.begin() as db:
            db.exec_driver_sql("DROP TABLE synthetic_unknown_copy")


def test_actual_history_guard_stays_active_and_immutable_table_is_not_allowed(f):
    with pytest.raises(f.adapter.DatabaseHold):
        f.adapter.CoreClinicalDatabase(
            f.engine,
            f.tables,
            f.authority,
            target_tables=("ai_visit_note",),
            enabled=True,
        )
    with f.engine.connect() as db:
        trigger = db.exec_driver_sql(
            "SELECT tgenabled FROM pg_trigger WHERE tgname='ai_visit_note_reject_delete'"
        ).scalar_one()
        assert trigger == "O"
    # Attach the actual unconditional guard to a mutable fixture table to prove
    # the adapter vetoes a guarded graph rather than disabling the trigger.
    with f.engine.begin() as db:
        db.exec_driver_sql(
            "CREATE TRIGGER synthetic_lab_history_guard BEFORE DELETE ON lab_result FOR EACH ROW EXECUTE FUNCTION cosmas_reject_append_only_mutation()"
        )
    scans, prepared = prepare(f)
    with pytest.raises(f.adapter.DatabaseHold):
        erase(f, scans, prepared)
    assert count(f, "lab_panel") == count(f, "lab_result") == 2


def test_before_90_day_expiry_and_missing_custody_are_held(f):
    f.database.clock = lambda: f.operation.export_until - timedelta(microseconds=1)
    with pytest.raises(f.adapter.DatabaseHold):
        f.database.adapters()["core.lab_panel"].scan(f.operation, NOW)
    f.database.clock = lambda: NOW
    f.authority.unresolved = True
    with pytest.raises(f.adapter.DatabaseHold):
        f.database.adapters()["core.lab_panel"].scan(f.operation, NOW)


def test_other_stores_still_block_coordinator_and_no_certificate(f):
    result = f.service.Coordinator(
        adapters=f.database.adapters(), clock=lambda: NOW, enabled=True
    ).advance(
        f.service.Journal(
            f.engine,
            f.tables["scribe_cancellation_operation"],
            f.tables["scribe_retention_event"],
        ),
        operation_id=f.operation.id,
        caller_sha256=CALLER,
        doctor_ref=f.operation.doctor_ref,
    )
    assert (
        result.phase == "expiry-hold"
        and not result.erasure_confirmed
        and not result.certificate_issued
    )
    assert count(f, "lab_panel") == count(f, "lab_result") == 2


def test_receiver_is_default_dormant(f):
    database = f.adapter.CoreClinicalDatabase(
        f.engine, f.tables, f.authority, target_tables=("lab_result",)
    )
    with pytest.raises(f.adapter.DatabaseHold):
        database.adapters()["core.lab_result"].scan(f.operation, NOW)


def test_preservation_loss_inside_erasure_transaction_rolls_back(f):
    scans, prepared = prepare(f)

    def corrupt_login(db):
        db.execute(
            f.tables["doctor"]
            .update()
            .where(f.tables["doctor"].c.id == f.ids["doctor"])
            .values(clerk_id="user_synthetic_drift_in_tx")
        )

    f.authority.on_fence = corrupt_login
    with pytest.raises(f.adapter.DatabaseHold):
        erase(f, scans, prepared)
    assert count(f, "lab_panel") == count(f, "lab_result") == 2
    with f.engine.connect() as db:
        assert (
            db.execute(
                sa.select(f.tables["doctor"].c.clerk_id).where(
                    f.tables["doctor"].c.id == f.ids["doctor"]
                )
            ).scalar_one()
            == "user_synthetic_primary"
        )


def test_real_lock_blocks_concurrent_target_insert(f):
    from concurrent.futures import ThreadPoolExecutor

    scans, prepared = prepare(f)
    attempted = False

    def attempt_insert():
        with pytest.raises(sa.exc.DBAPIError):
            with f.engine.begin() as db:
                db.exec_driver_sql("SET LOCAL lock_timeout='200ms'")
                db.execute(
                    f.tables["lab_result"]
                    .insert()
                    .values(
                        id=TypeID("lab_result"),
                        lab_panel_id=f.ids["lab_panel"],
                        doctor_id=f.ids["doctor"],
                        patient_id=f.ids["patient"],
                        biomarker="synthetic-race",
                        raw_name="synthetic",
                        value=1.0,
                    )
                )

    def check_lock(db):
        nonlocal attempted
        if not attempted:
            attempted = True
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(attempt_insert).result(timeout=3)

    f.authority.on_fence = check_lock
    erase(f, scans, prepared)
    assert attempted and count(f, "lab_panel") == count(f, "lab_result") == 1


def test_metadata_queries_never_project_clinical_payload_or_login_secret(f):
    statements = []

    def record(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    sa.event.listen(f.engine, "before_cursor_execute", record)
    try:
        prepare(f)
    finally:
        sa.event.remove(f.engine, "before_cursor_execute", record)
    selected = [
        statement.split(" FROM ")[0]
        for statement in statements
        if statement.startswith("SELECT public.")
    ]
    assert selected
    assert all(
        column not in projection
        for projection in selected
        for column in (
            ".value",
            ".raw_name",
            ".biomarker",
            ".transcript_data",
            ".note",
            ".name",
            ".clerk_id",
            ".api_key",
            ".email",
        )
    )


def test_omitted_authorized_row_does_not_turn_inventory_into_empty_success(f):
    scans, prepared = prepare(f)
    approval = f.authority.original
    assert approval
    f.authority.original = replace(approval, rows=approval.rows[:1])
    with pytest.raises(f.adapter.DatabaseHold):
        erase(f, scans, prepared)
    assert count(f, "lab_panel") == count(f, "lab_result") == 2


def test_row_security_cannot_hide_shared_cascade_dependents(f):
    with f.engine.begin() as db:
        db.exec_driver_sql("ALTER TABLE lab_result ENABLE ROW LEVEL SECURITY")
    # Even an owner that could bypass RLS cannot treat a hidden graph as closed.
    with pytest.raises(f.adapter.DatabaseHold):
        f.database.adapters()["core.lab_panel"].scan(f.operation, NOW)
    assert count(f, "lab_panel") == count(f, "lab_result") == 2
