"""Dormant PostgreSQL primary-row receiver; no route, job or authority installed.

Doctor/FK links discover candidates, never grant destruction custody. A trusted
receiver must pin exact metadata and authorization independently. Blob copies,
PITR, devices, subprocessors and immutable clinical history remain separate holds.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Mapping, Protocol
from uuid import UUID

from typeid import TypeID

from app.services.scribe_retention import (
    AbsenceProof,
    LedgerSession,
    Operation,
    PreservationProof,
    Resource,
    Scan,
    checked_digest,
    digest,
    restore_scans,
    utc,
    work_key,
)

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

# Audited mutable source tables. Runtime DELETE triggers still veto a graph.
# ai_visit_note and append-only histories deliberately have no bypass here.
MUTABLE_TABLES = frozenset(
    {
        "patient",
        "appointment",
        "ai_visit_transcript",
        "visit_audio_file",
        "doctor_note_file",
        "doctor_note_example",
        "doctor_note_schema",
        "note_type_customization",
        "intake",
        "intake_summary",
        "patient_medication",
        "lab_panel",
        "lab_result",
        "engagement_role",
        "cps_self_report",
        "scribe_private_note",
        "session_prep_note_finalization",
    }
)
PROTECTED_TABLES = frozenset(
    {
        "doctor",
        "authority_actor",
        "authority_actor_external_identity",
        "authority_tenant",
        "authority_person",
        "authority_person_binding",
        "authority_tenant_membership",
        "authority_person_origin",
        "mount_context_session",
        "mount_context_token",
        "practice",
        "practice_member",
        "provider_organization",
        "organization_roster_approval",
        "baa_version",
        "baa_acceptance",
        "practice_access_audit",
        "authority_audit_event",
        "ai_visit_note_version_audit",
        "stripe_event_inbox",
        "stripe_event_receipt",
        "pilot_access_grandfather_grant",
        "pilot_access_continuity_amendment",
        "pilot_access_continuity_amendment_identity",
        "scribe_cancellation_operation",
    }
)
# Actual source relationship deliberately lacks a database FK.
NON_FK_EDGES = (
    ("external_webhook_operation", "result_transcript_id", "ai_visit_transcript", "id"),
)
MAX_TABLES, MAX_ROWS = 200, 10_000


class DatabaseHold(RuntimeError):
    """Bounded non-PHI code; never expose DBAPI values/statements to callers."""


@dataclass(frozen=True, repr=False)
class MetadataRow:
    table: str
    identifier: str
    doctor_ref: str | None
    links: tuple[tuple[str, str | None], ...]
    metadata_sha256: str

    @property
    def ref_sha256(self) -> str:
        # Physical UUID identity is shared with the separately privileged
        # history receiver; source TypeID prefixes remain query type adapters.
        try:
            identifier = (
                str(TypeID.from_string(self.identifier).uuid)
                if "_" in self.identifier
                else str(UUID(self.identifier))
            )
        except Exception:
            identifier = self.identifier  # Source non-UUID retained-document ID.
        return digest(["core-primary-row-v1", self.table, identifier])


@dataclass(frozen=True, repr=False)
class DatabaseInventory:
    schema_sha256: str
    preservation_sha256: str
    candidates: tuple[MetadataRow, ...]


@dataclass(frozen=True, repr=False)
class RowAuthorization:
    row: MetadataRow
    custody_sha256: str
    caller_sha256: str
    other_custodian: bool | None = None
    legal_hold: bool | None = None


@dataclass(frozen=True, repr=False)
class DatabaseAuthorization:
    scope_sha256: str
    schema_sha256: str
    preservation_sha256: str
    rows: tuple[RowAuthorization, ...]
    evidence_sha256: str
    fence_sha256: str
    valid_until: datetime
    primary_inventory_complete: bool = False


class DatabaseCustodyAuthority(Protocol):
    """Actual approved custody/drain service; absent implementation is a hold.

    Persist the ORIGINAL exact authorization independently of this connection;
    retries must not approve a new set after deletion. Metadata/doctor links do
    not establish custody. verify_fences must check actual current writes AND
    restore/drain authority, including revoked grants/holds, inside this tx.
    """

    def authorize(
        self,
        connection: Connection,
        operation: Operation,
        inventory: DatabaseInventory,
        at: datetime,
    ) -> DatabaseAuthorization | None: ...
    def verify_fences(
        self,
        connection: Connection,
        operation: Operation,
        authorization: DatabaseAuthorization,
        at: datetime,
    ) -> bool: ...


class CoreClinicalDatabase:
    """One atomic graph shared by per-table coordinator adapters.

    tables must be the application's actual SQLAlchemy model metadata, including
    referencing tables. No caller clinical Connection, ORM load, cascade, hidden
    autocommit, schema/role mutation or public self-erasure endpoint is used.
    """

    def __init__(
        self,
        engine: Engine,
        tables: Mapping[str, sa.Table],
        authority: DatabaseCustodyAuthority | None = None,
        *,
        target_tables: tuple[str, ...] = tuple(sorted(MUTABLE_TABLES)),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        enabled: bool = False,
    ):
        if not isinstance(engine, Engine) or engine.dialect.name != "postgresql":
            raise DatabaseHold("dedicated-postgresql-engine-required")
        if engine.hide_parameters is not True or engine.echo:
            raise DatabaseHold("redacted-nonlogging-engine-required")
        if (
            not target_tables
            or len(set(target_tables)) != len(target_tables)
            or not set(target_tables) <= MUTABLE_TABLES
        ):
            raise DatabaseHold("unsupported-target-table")
        if any(
            name not in tables or tables[name].schema not in (None, "public")
            for name in target_tables
        ):
            raise DatabaseHold("source-table-contract-missing")
        self.engine, self.tables, self.authority = engine, dict(tables), authority
        self.targets, self.clock, self.enabled = (
            tuple(sorted(target_tables)),
            clock,
            enabled,
        )

    def adapters(self) -> dict[str, "CoreTableAdapter"]:
        return {f"core.{name}": CoreTableAdapter(self, name) for name in self.targets}

    def _require_enabled(self):
        if not self.enabled or self.authority is None:
            raise DatabaseHold("database-receiver-disabled-or-custody-unresolved")

    def _schema(self, connection: Connection):
        inspector = sa.inspect(connection)
        names = sorted(inspector.get_table_names(schema="public"))
        if len(names) > MAX_TABLES or not set(self.targets) <= set(names):
            raise DatabaseHold("database-schema-incomplete-or-unbounded")
        edges, definition = [], []
        for name in names:
            columns = inspector.get_columns(name, schema="public")
            pk = inspector.get_pk_constraint(name, schema="public")[
                "constrained_columns"
            ]
            foreign = inspector.get_foreign_keys(name, schema="public")
            definition.append(
                [
                    name,
                    sorted((c["name"], str(c["type"]), c["nullable"]) for c in columns),
                    pk,
                    foreign,
                ]
            )
            source = self.tables.get(name)
            if source is not None and (
                set(source.c.keys()) != {c["name"] for c in columns}
                or list(source.primary_key.columns.keys()) != pk
            ):
                raise DatabaseHold("source-schema-drift")
            if source is not None:
                actual = {
                    (
                        tuple(fk["constrained_columns"]),
                        fk["referred_table"],
                        tuple(fk["referred_columns"]),
                    )
                    for fk in foreign
                }
                expected = {
                    (
                        tuple(element.parent.name for element in constraint.elements),
                        constraint.elements[0].target_fullname.split(".")[-2],
                        tuple(
                            element.target_fullname.split(".")[-1]
                            for element in constraint.elements
                        ),
                    )
                    for constraint in source.foreign_key_constraints
                }
                if actual != expected:
                    raise DatabaseHold("source-foreign-key-drift")
            for fk in foreign:
                if fk.get("referred_schema") not in (None, "public"):
                    raise DatabaseHold("cross-schema-custody-unresolved")
                edges.append(
                    (
                        name,
                        tuple(fk["constrained_columns"]),
                        fk["referred_table"],
                        tuple(fk["referred_columns"]),
                    )
                )
        for child, column, parent, parent_column in NON_FK_EDGES:
            if child in names:
                edges.append((child, (column,), parent, (parent_column,)))
        triggers = connection.execute(
            sa.text(
                "SELECT c.relname, t.tgname, t.tgtype, t.tgenabled, pg_get_triggerdef(t.oid) "
                "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal AND n.nspname='public' "
                "ORDER BY c.relname,t.tgname"
            )
        ).all()
        rules = connection.execute(
            sa.text(
                "SELECT schemaname,tablename,rulename,definition FROM pg_rules WHERE schemaname='public' ORDER BY tablename,rulename"
            )
        ).all()
        rls = (
            connection.execute(
                sa.text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relrowsecurity ORDER BY c.relname"
                )
            )
            .scalars()
            .all()
        )
        if any(row[1] in self.targets for row in rules) or rls:
            raise DatabaseHold("rewrite-or-row-security-scope-unqualified")
        definition.extend(
            ([list(row) for row in triggers], [list(row) for row in rules], list(rls))
        )
        return names, edges, triggers, digest(definition)

    def _tables(self, connection: Connection, names):
        metadata = sa.MetaData()
        # Reflection reads schema only. Restore actual source type adapters:
        # Core TypeIDs persist UUIDs without prefixes; treating reflected UUIDs
        # as application IDs would silently produce an empty account inventory.
        tables = {
            name: sa.Table(name, metadata, schema="public", autoload_with=connection)
            for name in names
        }
        for name, table in tables.items():
            source = self.tables.get(name)
            if source is not None:
                for column in table.c:
                    column.type = source.c[column.name].type
        return tables

    def _row_metadata(self, connection: Connection, table: sa.Table):
        if list(table.primary_key.columns.keys()) != ["id"]:
            raise DatabaseHold("unsupported-record-key")
        columns = {"id"} | {fk.parent.name for fk in table.foreign_keys}
        columns |= {
            name
            for name in (
                "doctor_id",
                "result_transcript_id",
                "created_at",
                "updated_at",
                "deleted_at",
                "superseded_at",
            )
            if name in table.c
        }
        statement = sa.select(
            *(table.c[name] for name in sorted(columns)),
            sa.literal_column("xmin::text").label("_row_version"),
        ).limit(MAX_ROWS + 1)
        rows = connection.execute(statement).mappings().all()
        if len(rows) > MAX_ROWS:
            raise DatabaseHold("metadata-inventory-unbounded")
        result = []
        for row in rows:
            values = {
                key: value.isoformat()
                if isinstance(value, datetime)
                else str(value)
                if value is not None
                else None
                for key, value in row.items()
            }
            links = tuple(
                sorted(
                    (name, values[name])
                    for name in columns - {"id"}
                    if name.endswith("_id")
                )
            )
            result.append(
                MetadataRow(
                    table.name,
                    str(row["id"]),
                    values.get("doctor_id"),
                    links,
                    digest(values),
                )
            )
        return result

    def _inventory(self, connection: Connection, operation: Operation, targets=()):
        names, edges, triggers, schema_sha = self._schema(connection)
        tables = self._tables(connection, names)
        records = {
            name: self._row_metadata(connection, tables[name]) for name in self.targets
        }
        selected = {
            (row.table, row.identifier)
            for rows in records.values()
            for row in rows
            if row.doctor_ref == operation.doctor_ref
        }
        selected.update((item.row.table, item.row.identifier) for item in targets)
        # Indirect owner links discover schema customization/summary candidates.
        while True:
            added = {
                (row.table, row.identifier)
                for rows in records.values()
                for row in rows
                for child, columns, parent, parent_columns in edges
                if child == row.table
                and len(columns) == 1
                and parent_columns == ("id",)
                and (parent, dict(row.links).get(columns[0])) in selected
            }
            if added <= selected:
                break
            selected.update(added)
        candidates = tuple(
            sorted(
                (
                    row
                    for rows in records.values()
                    for row in rows
                    if (row.table, row.identifier) in selected
                ),
                key=lambda row: (row.table, row.identifier),
            )
        )
        if len(candidates) > MAX_ROWS:
            raise DatabaseHold("target-inventory-unbounded")
        preserved = [
            row.ref_sha256 + row.metadata_sha256
            for rows in records.values()
            for row in rows
            if (row.table, row.identifier) not in selected
        ]
        for name in sorted(PROTECTED_TABLES & set(names)):
            # IDs/FKs/timestamps/xmin only; Clerk subject/email/clinical payloads
            # never leave the DB. Any modification changes xmin even if writers
            # forget their updated_at field. No retained record is deleted.
            preserved.extend(
                row.ref_sha256 + row.metadata_sha256
                for row in self._row_metadata(connection, tables[name])
            )
        if len(preserved) > MAX_ROWS:
            raise DatabaseHold("preservation-inventory-unbounded")
        return (
            DatabaseInventory(schema_sha, digest(sorted(preserved)), candidates),
            tables,
            edges,
            triggers,
        )

    def _operation(self, connection: Connection, operation: Operation):
        row = (
            connection.execute(
                sa.select(self.tables["scribe_cancellation_operation"]).where(
                    self.tables["scribe_cancellation_operation"].c.id == operation.id
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            row is None
            or Operation.from_row(dict(row)) != operation
            or utc(self.clock()) < operation.export_until
        ):
            raise DatabaseHold("current-self-operation-or-expiry-drift")

    def _authorization(self, connection, operation, *, prior=None):
        connection.execute(sa.text("SET LOCAL statement_timeout = '15s'"))
        self._require_enabled()
        self._operation(connection, operation)
        inventory, tables, edges, triggers = self._inventory(
            connection, operation, prior.rows if prior else ()
        )
        at = utc(self.clock())
        authority = self.authority
        assert authority is not None
        approval = authority.authorize(connection, operation, inventory, at)
        if (
            type(approval) is not DatabaseAuthorization
            or approval.primary_inventory_complete is not True
            or approval.scope_sha256 != operation.scope_sha256
            or approval.schema_sha256 != inventory.schema_sha256
            or approval.preservation_sha256 != inventory.preservation_sha256
            or at > utc(approval.valid_until)
        ):
            raise DatabaseHold("custody-inventory-or-preservation-unresolved")
        for value in (approval.evidence_sha256, approval.fence_sha256):
            checked_digest(value)
        expected = {}
        for item in approval.rows:
            if (
                type(item) is not RowAuthorization
                or type(item.row) is not MetadataRow
                or item.row.table not in self.targets
                or item.caller_sha256 != operation.caller_sha256
                or item.other_custodian is not False
                or item.legal_hold is not False
                or item.row.doctor_ref not in (None, operation.doctor_ref)
            ):
                raise DatabaseHold("shared-held-or-foreign-row")
            checked_digest(item.custody_sha256)
            checked_digest(item.row.metadata_sha256)
            key = (item.row.table, item.row.identifier)
            if key in expected:
                raise DatabaseHold("duplicate-authorized-row")
            expected[key] = item.row
        current = {(row.table, row.identifier): row for row in inventory.candidates}
        if not set(current) <= set(expected) or any(
            current[key] != expected[key] for key in current
        ):
            raise DatabaseHold("exact-target-set-or-row-drift")
        if prior is not None and approval != prior:
            raise DatabaseHold("original-custody-authorization-drift")
        if authority.verify_fences(connection, operation, approval, at) is not True:
            raise DatabaseHold("actual-write-restore-drain-fence-unresolved")
        return approval, inventory, tables, edges, triggers

    def _scan(self, operation, table_name):
        try:
            with self.engine.begin() as connection:
                approval, _, _, _, _ = self._authorization(connection, operation)
                return self._approval_scan(operation, table_name, approval)
        except DatabaseHold:
            raise
        except Exception:
            raise DatabaseHold("database-metadata-enumeration-failed") from None

    def _approval_scan(self, operation, name, approval):
        items = tuple(item for item in approval.rows if item.row.table == name)
        return Scan(
            f"core.{name}",
            operation.scope_sha256,
            utc(self.clock()),
            digest(
                [
                    approval.evidence_sha256,
                    name,
                    [item.row.metadata_sha256 for item in items],
                    approval.preservation_sha256,
                ]
            ),
            digest(
                [
                    "primary-postgresql-only",
                    approval.schema_sha256,
                    approval.fence_sha256,
                ]
            ),
            tuple(
                Resource(
                    item.row.ref_sha256,
                    item.caller_sha256,
                    item.custody_sha256,
                    item.other_custodian,
                    item.legal_hold,
                )
                for item in items
            ),
            True,
            True,
        )

    def _committed(self, connection, operation):
        events = self.tables["scribe_retention_event"]
        ledger = LedgerSession(
            connection, self.tables["scribe_cancellation_operation"], events
        )
        prepared = ledger.prepared(operation)
        scans = restore_scans(prepared, operation)
        if prepared is None:
            raise DatabaseHold("committed-exact-intent-required")
        record = connection.execute(
            sa.select(events.c.event_key_sha256).where(
                events.c.operation_id == operation.id,
                events.c.kind == "erasure-intent",
                events.c.payload == prepared,
            )
        ).scalar_one_or_none()
        if record != digest(["erasure-intent", prepared]):
            raise DatabaseHold("committed-intent-evidence-drift")
        return scans, prepared["adapter_keys"]

    def _bound(self, operation, scan, approval, scans):
        original = scans.get(scan.surface)
        expected = self._approval_scan(
            operation, scan.surface.removeprefix("core."), approval
        )
        if (
            original != scan
            or expected.resource_set_sha256 != scan.resource_set_sha256
            or expected.evidence_sha256 != scan.evidence_sha256
            or expected.copy_inventory_sha256 != scan.copy_inventory_sha256
            or expected.resources != scan.resources
        ):
            raise DatabaseHold("committed-manifest-binding-drift")
        for name in self.targets:
            candidate = scans.get(f"core.{name}")
            if candidate is None:
                raise DatabaseHold("atomic-graph-manifest-incomplete")
            expected = self._approval_scan(operation, name, approval)
            if (
                candidate.resources,
                candidate.evidence_sha256,
                candidate.copy_inventory_sha256,
            ) != (
                expected.resources,
                expected.evidence_sha256,
                expected.copy_inventory_sha256,
            ):
                raise DatabaseHold("atomic-graph-target-drift")

    def _closed_graph(self, connection, approval, tables, edges, triggers):
        targets = {(item.row.table, item.row.identifier) for item in approval.rows}
        by_table = {
            name: [
                item.row.identifier for item in approval.rows if item.row.table == name
            ]
            for name in self.targets
        }
        for table, _, kind, enabled, _ in triggers:
            if by_table.get(table) and int(kind) & 8:
                # Even a disabled trigger requires review; never silently bypass.
                raise DatabaseHold("delete-trigger-history-or-custom-code-unqualified")
        order = {name: set() for name in self.targets}
        for child, columns, parent, parent_columns in edges:
            ids = by_table.get(parent)
            if not ids:
                continue
            if len(columns) != 1 or parent_columns != ("id",):
                raise DatabaseHold("composite-or-cyclic-custody-unresolved")
            table = tables[child]
            if (
                list(table.primary_key.columns.keys()) != ["id"]
                or columns[0] not in table.c
            ):
                raise DatabaseHold("unknown-inbound-record-key")
            found = (
                connection.execute(
                    sa.select(table.c.id)
                    .where(table.c[columns[0]].in_(ids))
                    .limit(MAX_ROWS + 1)
                )
                .scalars()
                .all()
            )
            if len(found) > MAX_ROWS or any(
                (child, str(value)) not in targets for value in found
            ):
                raise DatabaseHold("unapproved-shared-immutable-or-unknown-dependent")
            if child in order and child != parent and found:
                order[parent].add(child)
            if child == parent and found:
                raise DatabaseHold("self-reference-custody-unresolved")
        result = []
        while order:
            leaves = sorted(
                name for name, dependencies in order.items() if not dependencies
            )
            if not leaves:
                raise DatabaseHold("cyclic-graph-custody-unresolved")
            result.extend(leaves)
            for leaf in leaves:
                del order[leaf]
            for dependencies in order.values():
                dependencies.difference_update(leaves)
        return result, by_table

    def erase(self, operation, scan, key):
        try:
            with self.engine.begin() as connection:
                connection.execute(sa.text("SET LOCAL lock_timeout = '3s'"))
                connection.execute(sa.text("SET LOCAL statement_timeout = '15s'"))
                # Deterministic locks block row/FK drift and DDL on all observed
                # graph/control tables until validation, deletion and proof commit.
                names, _, _, _ = self._schema(connection)
                quote = connection.dialect.identifier_preparer.quote
                for name in names:
                    connection.execute(
                        sa.text(
                            f"LOCK TABLE public.{quote(name)} IN SHARE ROW EXCLUSIVE MODE"
                        )
                    )
                self._operation(connection, operation)
                scans, keys = self._committed(connection, operation)
                if (
                    keys.get(scan.surface) != key
                    or work_key(operation, scan.surface, scan) != key
                ):
                    raise DatabaseHold("stable-adapter-key-drift")
                approval, _, tables, edges, triggers = self._authorization(
                    connection, operation
                )
                self._bound(operation, scan, approval, scans)
                order, identifiers = self._closed_graph(
                    connection, approval, tables, edges, triggers
                )
                for name in order:
                    if identifiers[name]:
                        connection.execute(
                            tables[name]
                            .delete()
                            .where(tables[name].c.id.in_(identifiers[name]))
                        )
                # Any drift or preservation loss aborts this complete graph tx.
                after, inventory, _, _, _ = self._authorization(
                    connection, operation, prior=approval
                )
                if inventory.candidates or after != approval:
                    raise DatabaseHold("transactional-erasure-or-preservation-drift")
        except DatabaseHold:
            raise
        except Exception:
            raise DatabaseHold("database-transaction-failed") from None

    def revalidate(self, operation, scan):
        try:
            with self.engine.begin() as connection:
                approval, _, _, _, _ = self._authorization(connection, operation)
                scans, _ = self._committed(connection, operation)
                self._bound(operation, scan, approval, scans)
            return True
        except Exception:
            return False

    def verify(self, operation, scan):
        try:
            with self.engine.begin() as connection:
                approval, inventory, tables, edges, triggers = self._authorization(
                    connection, operation
                )
                scans, _ = self._committed(connection, operation)
                self._bound(operation, scan, approval, scans)
                self._closed_graph(connection, approval, tables, edges, triggers)
                count = sum(
                    row.table == scan.surface.removeprefix("core.")
                    for row in inventory.candidates
                )
                return AbsenceProof(
                    operation.scope_sha256,
                    scan.copy_inventory_sha256,
                    scan.resource_set_sha256,
                    utc(self.clock()),
                    digest(
                        [
                            "fresh-primary-absence",
                            scan.evidence_sha256,
                            inventory.preservation_sha256,
                            count,
                        ]
                    ),
                    count,
                    True,
                    True,
                )
        except Exception:
            raise DatabaseHold("primary-absence-or-preservation-unverified") from None

    def verify_preserved(self, operation, scan):
        # Exposed for integration verification; document/classification surfaces
        # still require their own original scans, dates and actual non-PHI proof.
        self.verify(operation, scan)
        return PreservationProof(
            operation.scope_sha256,
            scan.resource_set_sha256,
            scan.evidence_sha256,
            utc(self.clock()),
            digest(["primary-original-identities-and-documents", scan.evidence_sha256]),
            True,
            True,
        )


class CoreTableAdapter:
    def __init__(self, database: CoreClinicalDatabase, table: str):
        self.database, self.table = database, table

    def scan(self, operation: Operation, at: datetime) -> Scan:
        return self.database._scan(operation, self.table)

    def revalidate(self, operation: Operation, scan: Scan, at: datetime) -> bool:
        return self.database.revalidate(operation, scan)

    def erase(self, operation: Operation, scan: Scan, idempotency_key: str) -> None:
        self.database.erase(operation, scan, idempotency_key)

    def verify(self, operation: Operation, scan: Scan, at: datetime) -> AbsenceProof:
        return self.database.verify(operation, scan)

    def verify_preserved(
        self, operation: Operation, scan: Scan, at: datetime
    ) -> PreservationProof:
        return self.database.verify_preserved(operation, scan)
