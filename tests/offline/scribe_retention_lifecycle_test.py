"""Actual coordinator/journal on an owned synthetic SQLite database.

Run with --confcutdir=tests/offline; never load Core's live bootstrap/conftest.
Adapters here are synthetic and do not qualify Azure, Core or Scribe erasure.
"""

import importlib.util
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest

import sqlalchemy as sa
from sqlalchemy.schema import CreateTable

ROOT = Path(__file__).resolve().parents[2]
ACCEPTED = datetime(2026, 11, 2, tzinfo=UTC)
CALLER = "a" * 64
OTHER = "b" * 64
APPROVAL = "c" * 64


def load(monkeypatch, name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class SyntheticAdapter:
    def __init__(self, service, surface, world):
        self.service, self.surface, self.world = service, surface, world
        self.scan_changes, self.proof_changes = {}, {}
        self.erasures, self.keys = 0, []
        self.fail_once = False
        self.on_erase = None
        self.items = []
        self.snapshot = "synthetic-initial-state"

    def scan(self, operation, at):
        evidence = self.service.digest([self.surface, self.snapshot])
        scan = self.service.Scan(
            self.surface,
            operation.scope_sha256,
            at,
            evidence,
            self.service.digest([self.surface, "all-copies"]),
            tuple(self.items),
            True,
            True,
            APPROVAL,
            APPROVAL,
        )
        return replace(scan, **self.scan_changes)

    def erase(self, operation, scan, idempotency_key):
        self.erasures += 1
        self.keys.append(idempotency_key)
        assert all(
            item.caller_sha256 == CALLER and not item.other_custodian
            for item in scan.resources
        )
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("synthetic-private-exception-must-not-be-recorded")
        if self.on_erase is not None:
            self.on_erase()
        # Idempotent exact-set erasure; the other member/world is untouched.
        for item in scan.resources:
            self.world["owned"].discard(item.ref_sha256)

    def revalidate(self, operation, scan, at):
        return True

    def verify(self, operation, scan, at):
        proof = self.service.AbsenceProof(
            operation.scope_sha256,
            scan.copy_inventory_sha256,
            scan.resource_set_sha256,
            at,
            APPROVAL,
            sum(item.ref_sha256 in self.world["owned"] for item in scan.resources),
            True,
            True,
        )
        return replace(proof, **self.proof_changes)

    def verify_preserved(self, operation, scan, at):
        unchanged = scan.evidence_sha256 == self.service.digest(
            [self.surface, self.snapshot]
        )
        proof = self.service.PreservationProof(
            operation.scope_sha256,
            scan.resource_set_sha256,
            scan.evidence_sha256,
            at,
            APPROVAL,
            True,
            unchanged,
        )
        return replace(proof, **self.proof_changes)


@pytest.fixture
def f(tmp_path, monkeypatch):
    for name in ("app", "app.services", "app.models"):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    catalog = load(
        monkeypatch,
        "app.services.scribe_retention_catalog",
        "app/services/scribe_retention_catalog.py",
    )
    service = load(
        monkeypatch, "app.services.scribe_retention", "app/services/scribe_retention.py"
    )
    operations_source = load(
        monkeypatch,
        "app.models.scribe_cancellation",
        "app/models/scribe_cancellation.py",
    )
    events_source = load(
        monkeypatch, "app.models.scribe_retention", "app/models/scribe_retention.py"
    )
    metadata = sa.MetaData()
    operations = operations_source.scribe_cancellation_operation.to_metadata(metadata)
    events = events_source.scribe_retention_event.to_metadata(metadata)
    # SQLAlchemy to_metadata() drops conditional-DDL flags. Restore only the
    # source's PostgreSQL-only constraints on these synthetic table copies.
    for copied in (operations, events):
        for constraint in copied.constraints:
            if constraint.name in {
                "ck_scribe_cancellation_digests",
                "ck_scribe_cancellation_window",
                "ck_scribe_cancellation_policy",
                "ck_scribe_retention_payload",
            }:
                constraint.ddl_if(dialect="postgresql")
    # Remove only this test's registered model tables from shared SQLModel metadata.
    events_source.scribe_retention_event.metadata.remove(
        events_source.scribe_retention_event
    )
    operations_source.scribe_cancellation_operation.metadata.remove(
        operations_source.scribe_cancellation_operation
    )
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'synthetic-retention.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        metadata.create_all(connection)
        for table in (operations.name, events.name):
            for action in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    f"CREATE TRIGGER immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable synthetic evidence'); END"
                )
    operation_id = uuid4()
    policy = {
        "grace_days": 90,
        "phi_scope": "core-and-scribe-clinical-records",
        "approval_sha256": APPROVAL,
    }
    inventory = [
        [
            "synthetic-link",
            "synthetic-binding",
            "synthetic-admission",
            "synthetic-tenant",
            "synthetic-session",
        ]
    ]
    row = dict(
        id=operation_id,
        caller_sha256=CALLER,
        doctor_ref="doctor_synthetic_a",
        request_key_sha256=APPROVAL,
        preview_sha256=APPROVAL,
        inventory_sha256=service.digest(inventory),
        inventory=inventory,
        policy=policy,
        accepted_at=ACCEPTED,
        export_until=ACCEPTED + timedelta(days=90),
        phase="read-only",
    )
    with engine.begin() as connection:
        connection.execute(operations.insert().values(**row))
    world = {
        "owned": {service.digest("synthetic-phi")},
        "shared_login": True,
        "practice": "synthetic-practice",
        "other_member": OTHER,
        "other_clinical_record": service.digest("synthetic-other-clinician-record"),
    }
    adapters = {key: SyntheticAdapter(service, key, world) for key in catalog.BY_KEY}
    adapters["core.ai_visit_note"].items = [
        service.Resource(
            service.digest("synthetic-phi"),
            CALLER,
            APPROVAL,
            other_custodian=False,
            legal_hold=False,
        )
    ]
    for key in ("documents.compliance", "documents.finance-contracts"):
        adapters[key].items = [
            service.Resource(
                service.digest(key),
                CALLER,
                created_at=datetime(2024, 2, 29, tzinfo=UTC),
                last_effective_at=ACCEPTED,
            )
        ]

    class SyntheticDiscovery:
        changes = {}

        def resolve(self, operation, at):
            return replace(
                service.DiscoveryClosure(
                    operation.scope_sha256,
                    operation.inventory_sha256,
                    operation.policy_sha256,
                    catalog.CATALOG_VERSION,
                    at,
                    APPROVAL,
                    True,
                ),
                **self.changes,
            )

    discovery = SyntheticDiscovery()

    class SyntheticClock:
        value = ACCEPTED + timedelta(days=90)

        def __call__(self):
            self.value += timedelta(microseconds=1)
            return self.value

    clock = SyntheticClock()
    coordinator = service.Coordinator(
        adapters, enabled=True, discovery_authority=discovery, clock=clock
    )

    def advance(
        at=ACCEPTED + timedelta(days=90),
        *,
        subject=CALLER,
        doctor="doctor_synthetic_a",
        runner=None,
    ):
        clock.value = at - timedelta(microseconds=1)
        actual = runner or coordinator
        actual.clock = clock
        return actual.advance(
            service.Journal(engine, operations, events),
            operation_id=operation_id,
            caller_sha256=subject,
            doctor_ref=doctor,
        )

    def payloads(kind=None):
        with engine.connect() as connection:
            statement = sa.select(events.c.kind, events.c.payload).order_by(
                events.c.sequence
            )
            if kind:
                statement = statement.where(events.c.kind == kind)
            return connection.execute(statement).all()

    result = SimpleNamespace(**locals())
    yield result
    engine.dispose()


def test_exact_ninety_day_boundary_and_default_dormancy(f):
    assert f.advance(runner=f.service.Coordinator()).phase == "disabled"
    assert (
        f.advance(ACCEPTED + timedelta(days=90, microseconds=-1)).phase
        == "read-only-export"
    )
    assert not f.payloads()
    result = f.advance()
    assert result.phase == "certificate-ready" and result.erasure_confirmed
    assert not result.certificate_issued
    assert f.world["owned"] == set()


@pytest.mark.parametrize("days", [30, 45, 89, 90])
def test_pinned_contract_window_is_exact_and_immutable(f, days):
    expiry = ACCEPTED + timedelta(days=days)
    row = dict(
        f.row,
        policy=dict(f.policy, grace_days=days),
        export_until=expiry,
    )
    operation = f.service.Operation.from_row(row)
    assert operation.grace_days == days
    assert operation.export_until == ACCEPTED + timedelta(days=days)
    with pytest.raises(ValueError, match="drift"):
        f.service.Operation.from_row(
            dict(row, export_until=expiry + timedelta(seconds=1))
        )


@pytest.mark.parametrize(
    "change",
    [
        {"grace_days": 29},
        {"grace_days": 91},
        {"grace_days": True},
        {"phi_scope": "scribe-processing-copies"},
    ],
)
def test_wrong_policy_cannot_create_erasure_authority(f, change):
    with pytest.raises(ValueError):
        f.service.Operation.from_row(dict(f.row, policy=dict(f.policy, **change)))


@pytest.mark.parametrize(
    "subject,doctor", [(OTHER, "doctor_synthetic_a"), (CALLER, "doctor_synthetic_b")]
)
def test_self_owned_operation_cannot_select_another_account(f, subject, doctor):
    with pytest.raises(PermissionError):
        f.advance(subject=subject, doctor=doctor)
    assert not f.payloads() and not any(
        adapter.erasures for adapter in f.adapters.values()
    )


def test_missing_discovery_or_adapters_persists_hold_without_erasure(f):
    runner = f.service.Coordinator(enabled=True)
    result = f.advance(runner=runner)
    assert result.phase == "expiry-hold" and not result.erasure_confirmed
    assert "discovery-and-custody-closure-unresolved" in result.blockers
    assert any(
        "copies.database-backups-replicas:adapter-missing" == code
        for code in result.blockers
    )
    assert len(f.payloads("expiry-hold")) == 1
    f.advance(runner=runner)
    assert len(f.payloads("expiry-hold")) == 1  # durable deduplicated retry


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"complete": False}, "incomplete-copies-or-fence"),
        ({"write_and_restore_fence_verified": False}, "incomplete-copies-or-fence"),
        ({"observed_at": ACCEPTED}, "scope-or-time"),
        ({"scope_sha256": OTHER}, "scope-or-time"),
        ({"copy_inventory_sha256": None}, "invalid-evidence"),
    ],
)
def test_incomplete_or_wrong_scope_inventory_blocks_all_mutations(f, changes, code):
    f.adapters["copies.database-backups-replicas"].scan_changes = changes
    result = f.advance()
    assert f"copies.database-backups-replicas:{code}" in result.blockers
    assert not any(adapter.erasures for adapter in f.adapters.values())
    assert not f.payloads("certificate-ready")


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"custody_authorization_sha256": None}, "custody-unresolved"),
        ({"other_custodian": True}, "shared-custody"),
        ({"legal_hold": True}, "legal-hold"),
        ({"caller_sha256": OTHER}, "different-caller"),
    ],
)
def test_shared_custody_and_legal_holds_never_authorize_practice_purge(
    f, changes, code
):
    adapter = f.adapters["core.ai_visit_note"]
    adapter.items[0] = replace(adapter.items[0], **changes)
    result = f.advance()
    assert f"core.ai_visit_note:{code}" in result.blockers
    assert not any(item.erasures for item in f.adapters.values())


@pytest.mark.parametrize(
    "changes",
    [
        {"remaining_count": 1},
        {"remaining_count": False},
        {"complete": False},
        {"write_and_restore_fence_verified": False},
        {"scope_sha256": OTHER},
        {"copy_inventory_sha256": OTHER},
        {"resource_set_sha256": OTHER},
        {"observed_at": ACCEPTED},
    ],
)
def test_primary_delete_success_cannot_certify_retained_backups_or_stale_proof(
    f, changes
):
    f.adapters["copies.database-backups-replicas"].proof_changes = changes
    result = f.advance()
    assert "copies.database-backups-replicas:absence-not-verified" in result.blockers
    assert not result.erasure_confirmed and not f.payloads("verified-receipt")


def test_retry_is_durable_and_adapter_keys_are_stable(f):
    adapter = f.adapters["core.ai_visit_note"]
    adapter.fail_once = True
    failed = f.advance()
    assert not failed.erasure_confirmed
    assert "core.ai_visit_note:erasure-failed" in failed.blockers
    assert "synthetic-private-exception" not in json.dumps(f.payloads(), default=str)
    success = f.advance(ACCEPTED + timedelta(days=90, seconds=1))
    assert success.erasure_confirmed and adapter.keys[0] == adapter.keys[1]
    before = len(f.payloads())
    repeated = f.advance(ACCEPTED + timedelta(days=91))
    assert (
        repeated.receipt_sha256 == success.receipt_sha256
        and len(f.payloads()) == before
    )


@pytest.mark.parametrize(
    "surface",
    [
        "protected.shared-account-practice",
        "documents.compliance",
        "documents.finance-contracts",
        "core.ai_visit_transcript_non_phi",
    ],
)
def test_post_erasure_verification_detects_damage_to_protected_retained_copies(
    f, surface
):
    f.adapters["core.ai_visit_note"].on_erase = lambda: setattr(
        f.adapters[surface], "snapshot", "synthetic-damaged-state"
    )
    result = f.advance()
    assert f"{surface}:preservation-not-verified" in result.blockers
    assert not result.erasure_confirmed and not f.payloads("certificate-ready")


def test_shared_login_other_clinician_and_six_seven_year_non_phi_documents_survive(f):
    prior = dict(f.world)
    f.advance()
    for key in ("shared_login", "practice", "other_member", "other_clinical_record"):
        assert f.world[key] == prior[key]
    receipt = f.payloads("verified-receipt")[0][1]
    retained = receipt["retained_non_phi"]
    assert retained["documents.compliance"]["minimum_retention"][0][
        "minimum_retain_until"
    ].startswith("2032-11-02")
    assert retained["documents.finance-contracts"]["minimum_retention"][0][
        "minimum_retain_until"
    ].startswith("2033-11-02")
    assert not f.adapters["documents.compliance"].erasures
    assert not f.adapters["documents.finance-contracts"].erasures
    assert (
        receipt["preservation_proofs"]["protected.shared-account-practice"] == APPROVAL
    )
    assert f.service.anniversary(datetime(2024, 2, 29, tzinfo=UTC), 7) == datetime(
        2031, 2, 28, tzinfo=UTC
    )


def test_deidentified_retention_requires_actual_baa_and_classification(f):
    f.adapters["core.ai_visit_transcript_non_phi"].scan_changes = {
        "baa_permission_sha256": None
    }
    assert "core.ai_visit_transcript_non_phi:invalid-evidence" in f.advance().blockers
    assert not any(adapter.erasures for adapter in f.adapters.values())


@pytest.mark.parametrize(
    "changes",
    [
        {"scope_sha256": OTHER},
        {"inventory_sha256": OTHER},
        {"policy_sha256": OTHER},
        {"catalog_version": "unknown-catalog"},
        {"observed_at": ACCEPTED},
        {"all_stores_and_custodians_resolved": False},
    ],
)
def test_discovery_closure_is_bound_to_self_operation_and_actual_pinned_policy(
    f, changes
):
    f.discovery.changes = changes
    result = f.advance()
    assert "discovery-and-custody-closure-unresolved" in result.blockers
    assert not any(adapter.erasures for adapter in f.adapters.values())


def test_missing_legal_hold_or_shared_custody_classification_is_not_permission(f):
    adapter = f.adapters["core.ai_visit_note"]
    adapter.items[0] = replace(adapter.items[0], other_custodian=None, legal_hold=None)
    assert {
        "core.ai_visit_note:shared-custody",
        "core.ai_visit_note:legal-hold",
    } <= set(f.advance().blockers)
    assert not any(item.erasures for item in f.adapters.values())


def test_certificate_is_separate_from_verified_receipt_and_needs_real_ack(f):
    def issue(issuer=None):
        return f.coordinator.issue_certificate(
            f.service.Journal(f.engine, f.operations, f.events),
            operation_id=f.operation_id,
            caller_sha256=CALLER,
            doctor_ref="doctor_synthetic_a",
            issuer=issuer,
        )

    assert not issue().erasure_confirmed
    f.advance()
    assert issue().phase == "certificate-ready" and not issue().certificate_issued

    class SyntheticIssuer:
        calls = 0

        def issue(self, certificate, certificate_sha256):
            self.calls += 1
            assert f.service.digest(certificate) == certificate_sha256
            return APPROVAL

    issuer = SyntheticIssuer()
    assert issue(issuer).certificate_issued
    assert issue(issuer).certificate_issued and issuer.calls == 1
    assert len(f.payloads("certificate-issued")) == 1


def test_append_only_evidence_never_deletes_audit_early(f):
    f.advance()
    for table in (f.operations, f.events):
        for statement in (table.delete(), table.update().values(id=uuid4())):
            with (
                f.engine.begin() as connection,
                pytest.raises(sa.exc.IntegrityError, match="immutable"),
            ):
                connection.execute(statement)


def test_caller_transaction_is_rejected_without_committing_it(f):
    with f.engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(TypeError, match="caller Connection forbidden"):
            f.service.Journal(connection, f.operations, f.events)
        assert transaction.is_active
        transaction.rollback()


@pytest.mark.parametrize("failure_boundary", ["intent", "after-erasure"])
def test_commit_failure_never_erases_without_durable_original_intent(
    f, monkeypatch, failure_boundary
):
    original_commit = f.service.LedgerSession.commit

    def failing_commit(session):
        if failure_boundary == "intent" or not f.world["owned"]:
            raise RuntimeError("synthetic receiver commit failed")
        original_commit(session)

    with monkeypatch.context() as patch:
        patch.setattr(f.service.LedgerSession, "commit", failing_commit)
        with pytest.raises(RuntimeError, match="commit failed"):
            f.advance()
    prepared = [
        payload
        for _, payload in f.payloads("erasure-intent")
        if payload["stage"] == "prepared"
    ]
    if failure_boundary == "intent":
        assert not prepared and not any(
            adapter.erasures for adapter in f.adapters.values()
        )
    else:
        assert len(prepared) == 1 and not f.world["owned"]
        adapter = f.adapters["core.ai_visit_note"]
        original_key = adapter.keys[0]
        adapter.items = []
        assert f.advance(ACCEPTED + timedelta(days=91)).erasure_confirmed
        assert (
            adapter.keys[-1]
            == original_key
            == prepared[0]["adapter_keys"]["core.ai_visit_note"]
        )
        assert prepared[0]["scans"]["core.ai_visit_note"]["resources"][0][
            "ref_sha256"
        ] == f.service.digest("synthetic-phi")


def test_honest_advancing_clock_accepts_post_mutation_proofs_and_records_completion(f):
    adapter = f.adapters["core.ai_visit_note"]
    original = adapter.verify
    adapter.verify = lambda operation, scan, boundary: replace(
        original(operation, scan, boundary),
        observed_at=boundary + timedelta(microseconds=1),
    )
    assert f.advance().erasure_confirmed
    receipt = f.payloads("verified-receipt")[0][1]
    assert (
        datetime.fromisoformat(receipt["verified_at"])
        > datetime.fromisoformat(receipt["mutation_finished_at"])
        > ACCEPTED + timedelta(days=90)
    )


def test_cached_pre_mutation_preservation_cannot_certify_later_login_damage(f):
    adapter = f.adapters["protected.shared-account-practice"]
    original = adapter.scan

    def cache(operation, at):
        scan = original(operation, at)
        adapter.cached = f.service.PreservationProof(
            operation.scope_sha256,
            scan.resource_set_sha256,
            scan.evidence_sha256,
            scan.observed_at,
            APPROVAL,
            True,
            True,
        )
        return scan

    adapter.scan = cache
    adapter.verify_preserved = lambda operation, scan, boundary: adapter.cached
    f.adapters["core.ai_visit_note"].on_erase = lambda: f.world.update(
        shared_login=False
    )
    result = f.advance()
    assert (
        "protected.shared-account-practice:preservation-not-verified" in result.blockers
    )
    assert not result.erasure_confirmed and not f.payloads("certificate-ready")


def test_changed_custody_on_persisted_intent_holds_retry_before_erasure(f):
    adapter = f.adapters["core.ai_visit_note"]
    adapter.fail_once = True
    assert not f.advance().erasure_confirmed
    count = adapter.erasures
    adapter.revalidate = lambda operation, scan, at: False
    result = f.advance(ACCEPTED + timedelta(days=91))
    assert "core.ai_visit_note:intent-no-longer-authorized" in result.blockers
    assert adapter.erasures == count and not result.erasure_confirmed


@pytest.fixture
def manifest_source(monkeypatch):
    for name in ("app", "app.services"):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    load(
        monkeypatch,
        "app.services.scribe_retention_catalog",
        "app/services/scribe_retention_catalog.py",
    )
    service = load(
        monkeypatch, "app.services.scribe_retention", "app/services/scribe_retention.py"
    )
    operation = SimpleNamespace(
        scope_sha256=APPROVAL, caller_sha256=CALLER, export_until=ACCEPTED
    )
    resource = service.Resource(
        APPROVAL, CALLER, APPROVAL, False, False, ACCEPTED, ACCEPTED
    )
    scan = service.Scan(
        "core.ai_visit_note",
        APPROVAL,
        ACCEPTED,
        APPROVAL,
        APPROVAL,
        (resource,),
        True,
        True,
        APPROVAL,
        APPROVAL,
    )
    return SimpleNamespace(service=service, operation=operation, scan=scan)


@pytest.mark.parametrize(
    "surface", ["core.ai_visit_note", "protected.shared-account-practice"]
)
@pytest.mark.parametrize(
    "field,value",
    [
        ("caller_sha256", "synthetic-unvalidated-text"),
        ("custody_authorization_sha256", "synthetic-unvalidated-text"),
        ("other_custodian", "synthetic-unvalidated-text"),
        ("legal_hold", 0),
        ("created_at", "synthetic-unvalidated-text"),
        ("last_effective_at", datetime(2026, 11, 2)),
        ("non_phi_classification_sha256", "synthetic-unvalidated-text"),
        ("baa_permission_sha256", "synthetic-unvalidated-text"),
        ("caller_sha256", ["a"] * 64),
    ],
)
def test_manifest_boundary_rejects_every_unused_field_before_pack_and_recovery(
    manifest_source, surface, field, value
):
    m = manifest_source
    valid = replace(m.scan, surface=surface)
    payload = m.service.pack_scan(valid)
    if field in {"non_phi_classification_sha256", "baa_permission_sha256"}:
        invalid = replace(valid, **{field: value})
        payload[field] = value
    else:
        invalid = replace(
            valid, resources=(replace(valid.resources[0], **{field: value}),)
        )
        payload["resources"][0][field] = (
            value.isoformat() if isinstance(value, datetime) else value
        )
    assert m.service.scan_blockers(m.operation, invalid, surface, ACCEPTED)
    with pytest.raises(ValueError):
        m.service.pack_scan(invalid)
    with pytest.raises((ValueError, TypeError)):
        m.service.unpack_scan(payload, surface, m.operation)


def test_canonical_manifest_roundtrip_keeps_exact_targets_and_hold_decisions(
    manifest_source,
):
    m = manifest_source
    recovered = m.service.unpack_scan(
        m.service.pack_scan(m.scan), m.scan.surface, m.operation
    )
    assert recovered == m.scan
    payload = m.service.pack_scan(m.scan)
    payload["unexpected"] = "synthetic-unvalidated-text"
    with pytest.raises(ValueError, match="Unrecognized"):
        m.service.unpack_scan(payload, m.scan.surface, m.operation)


def test_additive_postgres_ddl_and_catalog_cover_history_and_retained_copies(f):
    from sqlalchemy.dialects.postgresql import dialect

    ddl = str(CreateTable(f.events).compile(dialect=dialect()))
    assert "FOREIGN KEY(operation_id) REFERENCES scribe_cancellation_operation" in ddl
    assert "uq_scribe_retention_retry" in ddl and "ck_scribe_retention_kind" in ddl
    migration = (
        ROOT
        / "migrations/versions/2026_11_02_708af99c7ac0_add_dormant_scribe_retention_lifecycle.py"
    ).read_text()
    assert "BEFORE UPDATE OR DELETE" in migration and "raise RuntimeError" in migration
    assert {
        "core.ai_visit_transcript",
        "core.ai_visit_note",
        "core.llm_response",
        "core.session_prep_note_finalization",
        "scribe.normalized_working_segments",
        "scribe.encrypted_translation_jobs",
        "copies.blob-versions-soft-delete",
        "copies.database-backups-replicas",
    } <= set(f.catalog.BY_KEY)
