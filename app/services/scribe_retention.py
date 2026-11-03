"""Dormant durable expiry/verification/certificate coordinator.

No route, scheduler, cloud client, erasure adapter or issuer is registered here.
An activated receiver must supply trusted, separately qualified custody and copy
enumeration adapters. Missing adapters always produce a persisted hold. Hashes
are evidence references, never inferred BAA permission or custody authority.
"""

import hashlib
import hmac
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Callable, Mapping, Protocol
from uuid import UUID, uuid4

from app.services.scribe_retention_catalog import BY_KEY, CATALOG_VERSION

from sqlalchemy import func, select
from sqlalchemy import text as sa_text
from sqlalchemy.engine import Connection, Engine

MAX_RESOURCE_ROWS = 10_000


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def checked_digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("Invalid evidence digest")
    return value


def utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise ValueError("UTC-aware evidence time required")
    return value.astimezone(UTC)


def anniversary(value: datetime, years: int) -> datetime:
    value = utc(value)
    try:
        return value.replace(year=value.year + years)
    except ValueError:  # February 29 has a February 28 anniversary.
        return value.replace(year=value.year + years, day=28)


@dataclass(frozen=True, repr=False)
class Operation:
    id: UUID
    caller_sha256: str
    doctor_ref: str
    inventory_sha256: str
    policy_sha256: str
    accepted_at: datetime
    export_until: datetime
    grace_days: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Operation":
        policy = row["policy"]
        if set(policy) != {"grace_days", "phi_scope", "approval_sha256"}:
            raise ValueError("Unrecognized pinned policy")
        days = policy["grace_days"]
        if (
            type(days) is not int
            or not 30 <= days <= 90
            or policy["phi_scope"] != "core-and-scribe-clinical-records"
        ):
            raise ValueError("Unrecognized pinned policy")
        checked_digest(policy["approval_sha256"])

        # SQLite loses timezone metadata; production/PostgreSQL remains aware.
        def persisted_time(value):
            return utc(value.replace(tzinfo=UTC) if value.tzinfo is None else value)

        accepted, expiry = (
            persisted_time(row["accepted_at"]),
            persisted_time(row["export_until"]),
        )
        if expiry != accepted + timedelta(days=days) or row["phase"] != "read-only":
            raise ValueError("Intake fence or expiry drift")
        inventory_sha = checked_digest(row["inventory_sha256"])
        if not hmac.compare_digest(inventory_sha, digest(row["inventory"])):
            raise ValueError("Intake inventory drift")
        return cls(
            row["id"],
            checked_digest(row["caller_sha256"]),
            row["doctor_ref"],
            inventory_sha,
            digest(policy),
            accepted,
            expiry,
            days,
        )

    @property
    def scope_sha256(self) -> str:
        return digest(
            [
                str(self.id),
                self.caller_sha256,
                self.doctor_ref,
                self.inventory_sha256,
                self.policy_sha256,
                self.export_until.isoformat(),
                CATALOG_VERSION,
            ]
        )


@dataclass(frozen=True, repr=False)
class Resource:
    ref_sha256: str
    caller_sha256: str
    custody_authorization_sha256: str | None = None
    other_custodian: bool | None = None
    legal_hold: bool | None = None
    created_at: datetime | None = None
    last_effective_at: datetime | None = None


@dataclass(frozen=True, repr=False)
class Scan:
    surface: str
    scope_sha256: str
    observed_at: datetime
    evidence_sha256: str
    # Signed operational closure must enumerate versions/soft-deletes/backup
    # retention and all copies, and verify write AND restore fences. A primary
    # row count or a deletion response cannot satisfy this attestation.
    copy_inventory_sha256: str | None = None
    resources: tuple[Resource, ...] = ()
    complete: bool = False
    write_and_restore_fence_verified: bool = False
    non_phi_classification_sha256: str | None = None
    baa_permission_sha256: str | None = None

    @property
    def resource_set_sha256(self) -> str:
        return digest(sorted(resource.ref_sha256 for resource in self.resources))


@dataclass(frozen=True, repr=False)
class AbsenceProof:
    scope_sha256: str
    copy_inventory_sha256: str
    resource_set_sha256: str
    observed_at: datetime
    evidence_sha256: str
    remaining_count: int
    complete: bool
    write_and_restore_fence_verified: bool


@dataclass(frozen=True, repr=False)
class PreservationProof:
    scope_sha256: str
    resource_set_sha256: str
    original_snapshot_sha256: str
    observed_at: datetime
    evidence_sha256: str
    complete: bool
    unchanged: bool


@dataclass(frozen=True, repr=False)
class DiscoveryClosure:
    scope_sha256: str
    inventory_sha256: str
    policy_sha256: str
    catalog_version: str
    observed_at: datetime
    evidence_sha256: str
    all_stores_and_custodians_resolved: bool


class DiscoveryAuthority(Protocol):
    """Authenticates the actual customer/BAA/custodian discovery decision.

    Never derive this authority from creator/doctor links. The attestation must
    be bound to this operation, pinned contract, current catalog and exact
    intake inventory. Unknown external copies and shared custody are holds.
    """

    def resolve(self, operation: Operation, at: datetime) -> DiscoveryClosure: ...


class ErasureAdapter(Protocol):
    """Trusted adapter: bound enumeration, idempotent erasure, fresh full scan.

    erase may touch ONLY exact authorized resources, never Doctor, Clerk,
    AuthorityIdentity, memberships/practice, audit facts or another custodian.
    Incomplete or immutable retained copies must report incomplete/remaining.
    Adapters must suppress PHI in exceptions, telemetry and evidence payloads.
    """

    def scan(self, operation: Operation, at: datetime) -> Scan: ...
    def revalidate(self, operation: Operation, scan: Scan, at: datetime) -> bool:
        """Recheck current custody/holds/fences for the persisted exact intent."""
        ...

    def erase(self, operation: Operation, scan: Scan, idempotency_key: str) -> None: ...
    def verify(
        self, operation: Operation, scan: Scan, at: datetime
    ) -> AbsenceProof: ...
    def verify_preserved(
        self, operation: Operation, scan: Scan, at: datetime
    ) -> PreservationProof: ...


class CertificateIssuer(Protocol):
    """Pinned authorized issuer, signer and customer delivery/acknowledgement.

    Must durably deduplicate the supplied certificate hash and verify actual
    signing/delivery; return their receipt digest. A draft is never issuance.
    """

    def issue(self, certificate: Mapping[str, Any], certificate_sha256: str) -> str: ...


@dataclass(frozen=True)
class Result:
    phase: str
    erasure_confirmed: bool = False
    certificate_issued: bool = False
    blockers: tuple[str, ...] = ()
    receipt_sha256: str | None = None


_SYNTHETIC_LOCKS: dict[tuple[Engine, UUID], RLock] = {}
_LOCKS_GUARD = RLock()


class Journal:
    """Dedicated lifecycle connections; never commit a caller clinical transaction.

    A PostgreSQL session advisory lock serializes external execution across
    transaction commits. SQLite's process lock exists only for synthetic tests.
    """

    def __init__(self, engine: Engine, operations, events):
        if not isinstance(engine, Engine):
            raise TypeError("Dedicated Engine required; caller Connection forbidden")
        self.engine, self.operations, self.events = engine, operations, events

    @contextmanager
    def session(self, operation_id: UUID):
        with self.engine.connect() as connection:
            key = int(digest(str(operation_id))[:15], 16)
            if connection.dialect.name == "postgresql":
                connection.execute(
                    sa_text("SELECT pg_advisory_lock(:key)"), {"key": key}
                )
                connection.commit()
                lock = None
            elif connection.dialect.name == "sqlite":
                with _LOCKS_GUARD:
                    lock = _SYNTHETIC_LOCKS.setdefault(
                        (self.engine, operation_id), RLock()
                    )
                lock.acquire()
            else:
                raise ValueError("Unqualified lifecycle database")
            try:
                yield LedgerSession(connection, self.operations, self.events)
            finally:
                connection.rollback()
                if lock is not None:
                    lock.release()
                else:
                    connection.execute(
                        sa_text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                    )
                    connection.commit()


class LedgerSession:
    """Private dedicated connection, held under Journal's execution lock."""

    def __init__(self, connection: Connection, operations, events):
        self.connection, self.operations, self.events = connection, operations, events

    def commit(self) -> None:
        self.connection.commit()

    def self_operation(
        self, operation_id: UUID, caller_sha256: str, doctor_ref: str
    ) -> Operation:
        row = (
            self.connection.execute(
                select(self.operations)
                .where(self.operations.c.id == operation_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if (
            row is None
            or not hmac.compare_digest(
                row["caller_sha256"], checked_digest(caller_sha256)
            )
            or row["doctor_ref"] != doctor_ref
        ):
            raise PermissionError("Cancellation operation is not self-owned")
        return Operation.from_row(dict(row))

    def existing(self, operation: Operation, kind: str) -> Mapping[str, Any] | None:
        return self.connection.execute(
            select(self.events.c.payload)
            .where(
                self.events.c.operation_id == operation.id, self.events.c.kind == kind
            )
            .order_by(self.events.c.sequence.desc())
            .limit(1)
        ).scalar_one_or_none()

    def prepared(self, operation: Operation) -> Mapping[str, Any] | None:
        rows = self.connection.execute(
            select(self.events.c.payload)
            .where(
                self.events.c.operation_id == operation.id,
                self.events.c.kind == "erasure-intent",
            )
            .order_by(self.events.c.sequence)
        ).scalars()
        return next((row for row in rows if row.get("stage") == "prepared"), None)

    def append(
        self, operation: Operation, kind: str, payload: Mapping[str, Any], at: datetime
    ) -> str:
        payload = dict(payload)
        payload["scope_sha256"] = operation.scope_sha256
        key = digest([kind, payload])
        if (
            self.connection.execute(
                select(self.events.c.id).where(
                    self.events.c.operation_id == operation.id,
                    self.events.c.event_key_sha256 == key,
                )
            ).first()
            is None
        ):
            sequence = (
                self.connection.execute(
                    select(func.coalesce(func.max(self.events.c.sequence), 0)).where(
                        self.events.c.operation_id == operation.id
                    )
                ).scalar_one()
                + 1
            )
            self.connection.execute(
                self.events.insert().values(
                    id=uuid4(),
                    operation_id=operation.id,
                    sequence=sequence,
                    event_key_sha256=key,
                    kind=kind,
                    recorded_at=utc(at),
                    payload=payload,
                )
            )
        return key


def validate_manifest_types(scan: Scan) -> None:
    """Closed runtime schema for every field serialized into immutable evidence."""
    if (
        type(scan) is not Scan
        or scan.surface not in BY_KEY
        or type(scan.resources) is not tuple
    ):
        raise ValueError("Invalid manifest shape")
    for value in (scan.scope_sha256, scan.evidence_sha256, scan.copy_inventory_sha256):
        checked_digest(value)
    for value in (scan.non_phi_classification_sha256, scan.baa_permission_sha256):
        if value is not None:
            checked_digest(value)
    if (
        type(scan.complete) is not bool
        or type(scan.write_and_restore_fence_verified) is not bool
    ):
        raise ValueError("Invalid manifest boolean")
    utc(scan.observed_at)
    for resource in scan.resources:
        if type(resource) is not Resource:
            raise ValueError("Invalid resource shape")
        checked_digest(resource.ref_sha256)
        checked_digest(resource.caller_sha256)
        if resource.custody_authorization_sha256 is not None:
            checked_digest(resource.custody_authorization_sha256)
        for value in (resource.other_custodian, resource.legal_hold):
            if value is not None and type(value) is not bool:
                raise ValueError("Invalid resource boolean")
        for value in (resource.created_at, resource.last_effective_at):
            if value is not None:
                utc(value)


def scan_blockers(
    operation: Operation, scan: Scan, surface: str, at: datetime
) -> list[str]:
    reasons = []
    try:
        validate_manifest_types(scan)
        if (
            scan.surface != surface
            or scan.scope_sha256 != operation.scope_sha256
            or not operation.export_until <= utc(scan.observed_at) <= at
        ):
            reasons.append("scope-or-time")
        if (
            scan.complete is not True
            or scan.write_and_restore_fence_verified is not True
        ):
            reasons.append("incomplete-copies-or-fence")
        refs = [resource.ref_sha256 for resource in scan.resources]
        if len(refs) > MAX_RESOURCE_ROWS:
            reasons.append("inventory-limit")
        if len(refs) != len(set(refs)):
            reasons.append("duplicate-resource")
        disposition = BY_KEY[surface].disposition
        if disposition == "erase":
            for resource in scan.resources:
                if resource.caller_sha256 != operation.caller_sha256:
                    reasons.append("different-caller")
                if resource.other_custodian is not False:
                    reasons.append("shared-custody")
                if resource.legal_hold is not False:
                    reasons.append("legal-hold")
                if not resource.custody_authorization_sha256:
                    reasons.append("custody-unresolved")
        elif disposition != "protected":
            checked_digest(scan.non_phi_classification_sha256 or "")
            if disposition == "baa-deidentified":
                checked_digest(scan.baa_permission_sha256 or "")
            elif any(
                resource.created_at is None or resource.last_effective_at is None
                for resource in scan.resources
            ):
                reasons.append("document-retention-anchor-missing")
            elif any(
                utc(value) > at
                for resource in scan.resources
                for value in (resource.created_at, resource.last_effective_at)
                if value is not None
            ):
                reasons.append("document-retention-anchor-future")
    except (ValueError, TypeError, AttributeError):
        reasons.append("invalid-evidence")
    return [f"{surface}:{reason}" for reason in sorted(set(reasons))]


def document_floors(scan: Scan) -> list[dict[str, str]]:
    years = 7 if BY_KEY[scan.surface].disposition == "finance" else 6
    return [
        {
            "ref_sha256": resource.ref_sha256,
            "minimum_retain_until": anniversary(
                max(utc(resource.created_at), utc(resource.last_effective_at)), years
            ).isoformat(),
        }
        for resource in scan.resources
        if resource.created_at is not None and resource.last_effective_at is not None
    ]


_SCAN_FIELDS = "observed_at evidence_sha256 copy_inventory_sha256 resources non_phi_classification_sha256 baa_permission_sha256".split()
_RESOURCE_FIELDS = "ref_sha256 caller_sha256 custody_authorization_sha256 other_custodian legal_hold created_at last_effective_at".split()


def pack_scan(scan: Scan) -> dict[str, Any]:
    # Only validated complete/fenced scans reach a prepared intent. The parent
    # operation and surface key bind constant fields without repeating them.
    validate_manifest_types(scan)
    data = {field: getattr(scan, field) for field in _SCAN_FIELDS}
    data["resources"] = [
        {field: getattr(resource, field) for field in _RESOURCE_FIELDS}
        for resource in scan.resources
    ]
    return json.loads(json.dumps(data, default=lambda value: utc(value).isoformat()))


def unpack_scan(payload: Mapping[str, Any], surface: str, operation: Operation) -> Scan:
    if set(payload) != set(_SCAN_FIELDS):
        raise ValueError("Unrecognized persisted manifest fields")
    data = dict(payload)
    data.update(
        surface=surface,
        scope_sha256=operation.scope_sha256,
        complete=True,
        write_and_restore_fence_verified=True,
    )
    data["observed_at"] = datetime.fromisoformat(data["observed_at"])
    resources = []
    for original in data["resources"]:
        if set(original) != set(_RESOURCE_FIELDS):
            raise ValueError("Unrecognized persisted resource fields")
        item = dict(original)
        for field in ("created_at", "last_effective_at"):
            if item[field] is not None:
                item[field] = datetime.fromisoformat(item[field])
        resources.append(Resource(**item))
    data["resources"] = tuple(resources)
    scan = Scan(**data)
    validate_manifest_types(scan)
    return scan


def restore_scans(
    prepared: Mapping[str, Any] | None, operation: Operation
) -> dict[str, Scan]:
    if (
        prepared is None
        or prepared["scope_sha256"] != operation.scope_sha256
        or set(prepared["scans"]) != set(BY_KEY)
    ):
        raise ValueError("Prepared manifest scope drift")
    if set(prepared) != {
        "stage",
        "manifest_sha256",
        "scans",
        "adapter_keys",
        "scope_sha256",
    }:
        raise ValueError("Unrecognized prepared manifest fields")
    checked_digest(prepared["manifest_sha256"])
    if prepared["stage"] != "prepared":
        raise ValueError("Invalid prepared stage")
    scans = {
        surface: unpack_scan(payload, surface, operation)
        for surface, payload in prepared["scans"].items()
    }
    expected = {
        surface: work_key(operation, surface, scan)
        for surface, scan in scans.items()
        if BY_KEY[surface].disposition == "erase"
    }
    if prepared["adapter_keys"] != expected:
        raise ValueError("Prepared work key drift")
    return scans


def work_key(operation: Operation, surface: str, scan: Scan) -> str:
    return digest(
        [str(operation.id), operation.scope_sha256, surface, scan.resource_set_sha256]
    )


class Coordinator:
    def __init__(
        self,
        adapters: Mapping[str, ErasureAdapter] | None = None,
        *,
        enabled: bool = False,
        discovery_authority: DiscoveryAuthority | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.adapters = dict(adapters or {})
        self.enabled = enabled
        self.discovery_authority = discovery_authority
        self.clock = clock  # Trusted runtime dependency; never a request timestamp.

    def advance(
        self,
        journal: Journal,
        *,
        operation_id: UUID,
        caller_sha256: str,
        doctor_ref: str,
    ) -> Result:
        with journal.session(operation_id) as ledger:
            return self._advance(
                ledger,
                operation_id=operation_id,
                caller_sha256=caller_sha256,
                doctor_ref=doctor_ref,
            )

    def _advance(
        self,
        journal: LedgerSession,
        *,
        operation_id: UUID,
        caller_sha256: str,
        doctor_ref: str,
    ) -> Result:
        operation = journal.self_operation(operation_id, caller_sha256, doctor_ref)
        at = utc(self.clock())
        if not self.enabled:
            return Result("disabled")
        if at < operation.export_until:
            return Result("read-only-export")
        prepared = journal.prepared(operation)
        try:
            restored = (
                restore_scans(prepared, operation) if prepared is not None else None
            )
        except Exception:
            return self._hold(journal, operation, ["prepared-intent-malformed"], at)
        existing = journal.existing(operation, "certificate-ready")
        if existing is not None:
            if (
                restored is None
                or existing["scope_sha256"] != operation.scope_sha256
                or not existing["certificate"].get("intent_sha256")
                or not existing["certificate"].get("mutation_finished_at")
            ):
                return self._hold(journal, operation, ["verified-scope-drift"], at)
            issued = journal.existing(operation, "certificate-issued")
            return Result(
                "certificate-issued" if issued else "certificate-ready",
                True,
                bool(issued),
                receipt_sha256=existing["receipt_sha256"],
            )
        scans, blockers, discovery_sha256 = {}, [], None
        try:
            if self.discovery_authority is None:
                raise ValueError("No authorized discovery decision")
            closure = self.discovery_authority.resolve(operation, at)
            discovery_sha256 = checked_digest(closure.evidence_sha256)
            if (
                closure.scope_sha256 != operation.scope_sha256
                or closure.inventory_sha256 != operation.inventory_sha256
                or closure.policy_sha256 != operation.policy_sha256
                or closure.catalog_version != CATALOG_VERSION
                or not operation.export_until
                <= utc(closure.observed_at)
                <= utc(self.clock())
                or closure.all_stores_and_custodians_resolved is not True
            ):
                raise ValueError("Discovery decision not bound to operation")
        except Exception:
            blockers.append("discovery-and-custody-closure-unresolved")
        if set(self.adapters) - set(BY_KEY):
            blockers.append("uncatalogued-surface")
        for surface in BY_KEY:
            adapter = self.adapters.get(surface)
            if adapter is None:
                blockers.append(f"{surface}:adapter-missing")
                continue
            try:
                scan = (
                    restored[surface]
                    if restored is not None
                    else adapter.scan(operation, utc(self.clock()))
                )
                blockers.extend(
                    scan_blockers(operation, scan, surface, utc(self.clock()))
                )
                scans[surface] = scan
            except Exception:
                # Never persist exception strings: they may contain PHI/paths.
                blockers.append(f"{surface}:enumeration-failed")
        if blockers:
            return self._hold(journal, operation, blockers, utc(self.clock()))
        manifest_sha = (
            prepared["manifest_sha256"]
            if prepared
            else digest(
                [
                    CATALOG_VERSION,
                    operation.scope_sha256,
                    discovery_sha256,
                    [
                        [
                            surface,
                            scan.resource_set_sha256,
                            scan.evidence_sha256,
                            scan.copy_inventory_sha256,
                        ]
                        for surface, scan in sorted(scans.items())
                    ],
                ]
            )
        )
        if prepared is None:
            prepared = {
                "stage": "prepared",
                "manifest_sha256": manifest_sha,
                "scans": {surface: pack_scan(scan) for surface, scan in scans.items()},
                "adapter_keys": {
                    surface: work_key(operation, surface, scan)
                    for surface, scan in scans.items()
                    if BY_KEY[surface].disposition == "erase"
                },
            }
            if len(json.dumps(prepared).encode()) > 60_000:
                return self._hold(
                    journal, operation, ["intent-manifest-too-large"], utc(self.clock())
                )
            intent_sha = journal.append(
                operation, "erasure-intent", prepared, utc(self.clock())
            )
        else:
            intent_sha = digest(["erasure-intent", prepared])
        journal.commit()  # Dedicated intent transaction MUST finish before erase.
        for surface, scan in scans.items():
            try:
                if (
                    self.adapters[surface].revalidate(
                        operation, scan, utc(self.clock())
                    )
                    is not True
                ):
                    blockers.append(f"{surface}:intent-no-longer-authorized")
            except Exception:
                blockers.append(f"{surface}:intent-revalidation-failed")
        if blockers:
            return self._hold(journal, operation, blockers, utc(self.clock()))
        proofs, retained, preservation = {}, {}, {}
        attempts = []
        for surface, scan in sorted(scans.items()):
            if BY_KEY[surface].disposition != "erase":
                continue
            key = prepared["adapter_keys"][surface]
            try:
                self.adapters[surface].erase(operation, scan, key)
                attempts.append([surface, key, "returned"])
            except Exception:
                attempts.append([surface, key, "failed"])
                blockers.append(f"{surface}:erasure-failed")
        mutation_finished = utc(self.clock())
        journal.append(
            operation,
            "erasure-intent",
            {
                "stage": "attempt-finished",
                "intent_sha256": intent_sha,
                "attempts": attempts,
                "mutation_finished_at": mutation_finished.isoformat(),
            },
            mutation_finished,
        )
        journal.commit()
        # Verify everything only after every attempted mutation. In particular,
        # deleting an FK-linked clinical row must not quietly remove audit/BAA
        # facts, another clinician's record, or the shared login.
        for surface, scan in sorted(scans.items()):
            if BY_KEY[surface].disposition != "erase":
                try:
                    proof = self.adapters[surface].verify_preserved(
                        operation, scan, mutation_finished
                    )
                    proof_completed = utc(self.clock())
                    checked_digest(proof.evidence_sha256)
                    if (
                        proof.scope_sha256 != operation.scope_sha256
                        or proof.resource_set_sha256 != scan.resource_set_sha256
                        or proof.original_snapshot_sha256 != scan.evidence_sha256
                        or not mutation_finished
                        <= utc(proof.observed_at)
                        <= proof_completed
                        or proof.complete is not True
                        or proof.unchanged is not True
                    ):
                        blockers.append(f"{surface}:preservation-not-verified")
                    else:
                        preservation[surface] = proof.evidence_sha256
                        if BY_KEY[surface].disposition != "protected":
                            retained[surface] = {
                                "classification_sha256": scan.non_phi_classification_sha256,
                                "baa_permission_sha256": scan.baa_permission_sha256,
                                "minimum_retention": document_floors(scan),
                                "preserved": True,
                            }
                except Exception:
                    blockers.append(f"{surface}:preservation-verification-failed")
                continue
            try:
                proof = self.adapters[surface].verify(
                    operation, scan, mutation_finished
                )
                proof_completed = utc(self.clock())
                checked_digest(proof.evidence_sha256)
                if (
                    proof.scope_sha256 != operation.scope_sha256
                    or proof.copy_inventory_sha256 != scan.copy_inventory_sha256
                    or proof.resource_set_sha256 != scan.resource_set_sha256
                    or not mutation_finished
                    <= utc(proof.observed_at)
                    <= proof_completed
                    or type(proof.remaining_count) is not int
                    or proof.remaining_count != 0
                    or proof.complete is not True
                    or proof.write_and_restore_fence_verified is not True
                ):
                    blockers.append(f"{surface}:absence-not-verified")
                else:
                    proofs[surface] = proof.evidence_sha256
            except Exception:
                blockers.append(f"{surface}:verification-failed")
        completed = utc(self.clock())
        if completed < mutation_finished or mutation_finished < max(
            at, *(scan.observed_at for scan in scans.values())
        ):
            blockers.append("trusted-clock-regression")
        journal.append(
            operation,
            "erasure-intent",
            {
                "stage": "verification-finished",
                "intent_sha256": intent_sha,
                "proofs": proofs,
                "preservation_proofs": preservation,
                "blockers": sorted(set(blockers)),
            },
            completed,
        )
        journal.commit()
        if blockers:
            return self._hold(journal, operation, blockers, completed)
        payload = {
            "manifest_sha256": manifest_sha,
            "intent_sha256": intent_sha,
            "mutation_finished_at": mutation_finished.isoformat(),
            "verified_at": completed.isoformat(),
            "proofs": proofs,
            "preservation_proofs": preservation,
            "retained_non_phi": retained,
            "shared_login_preserved": True,
            "other_members_preserved": True,
            "scope": "core-and-scribe-clinical-records",
            "catalog_version": CATALOG_VERSION,
        }
        receipt_sha = journal.append(operation, "verified-receipt", payload, completed)
        journal.append(
            operation,
            "certificate-ready",
            {"receipt_sha256": receipt_sha, "certificate": payload},
            completed,
        )
        journal.commit()
        return Result(
            "certificate-ready", erasure_confirmed=True, receipt_sha256=receipt_sha
        )

    @staticmethod
    def _hold(
        journal: LedgerSession, operation: Operation, blockers: list[str], at: datetime
    ) -> Result:
        codes = tuple(sorted(set(blockers)))
        journal.append(
            operation,
            "expiry-hold",
            {
                "blockers": list(codes),
                "erasure_confirmed": False,
                "export_expired": True,
            },
            at,
        )
        journal.commit()
        return Result("expiry-hold", blockers=codes)

    def issue_certificate(
        self,
        journal: Journal,
        *,
        operation_id: UUID,
        caller_sha256: str,
        doctor_ref: str,
        issuer: CertificateIssuer | None = None,
    ) -> Result:
        with journal.session(operation_id) as ledger:
            return self._issue_certificate(
                ledger,
                operation_id=operation_id,
                caller_sha256=caller_sha256,
                doctor_ref=doctor_ref,
                issuer=issuer,
            )

    def _issue_certificate(
        self,
        journal: LedgerSession,
        *,
        operation_id: UUID,
        caller_sha256: str,
        doctor_ref: str,
        issuer: CertificateIssuer | None,
    ) -> Result:
        operation = journal.self_operation(operation_id, caller_sha256, doctor_ref)
        at = utc(self.clock())
        if not self.enabled:
            return Result("disabled")
        ready = journal.existing(operation, "certificate-ready")
        if ready is None:
            return Result("expiry-hold", blockers=("verified-receipt-required",))
        try:
            restore_scans(journal.prepared(operation), operation)
        except Exception:
            return Result("expiry-hold", blockers=("prepared-intent-malformed",))
        if (
            ready["scope_sha256"] != operation.scope_sha256
            or not ready["certificate"].get("intent_sha256")
            or not ready["certificate"].get("mutation_finished_at")
            or at < datetime.fromisoformat(ready["certificate"]["verified_at"])
        ):
            return Result("expiry-hold", blockers=("verified-scope-or-time-drift",))
        issued = journal.existing(operation, "certificate-issued")
        if issued is not None:
            return Result(
                "certificate-issued", True, True, receipt_sha256=ready["receipt_sha256"]
            )
        if issuer is None:
            return Result(
                "certificate-ready",
                True,
                blockers=("authorized-issuer-and-delivery-required",),
                receipt_sha256=ready["receipt_sha256"],
            )
        try:
            journal.commit()
            certificate_sha = digest(ready["certificate"])
            acknowledgement = checked_digest(
                issuer.issue(ready["certificate"], certificate_sha)
            )
        except Exception:
            return Result(
                "certificate-ready",
                True,
                blockers=("certificate-signing-or-delivery-failed",),
                receipt_sha256=ready["receipt_sha256"],
            )
        journal.append(
            operation,
            "certificate-issued",
            {
                "receipt_sha256": ready["receipt_sha256"],
                "certificate_sha256": certificate_sha,
                "delivery_receipt_sha256": acknowledgement,
            },
            utc(self.clock()),
        )
        journal.commit()
        return Result(
            "certificate-issued", True, True, receipt_sha256=ready["receipt_sha256"]
        )
