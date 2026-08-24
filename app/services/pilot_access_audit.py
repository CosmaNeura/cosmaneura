"""Aggregate-only integrity audit for pilot continuity authorization lineages."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import cast

from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS
from app.environments import python_environment
from app.services.clerk_cohort import (
    ClerkCohortError,
    ClerkCohortIdentity,
    canonical_clerk_email_pair_sha256,
)
from app.services.continuity_reconciliation import (
    ContinuityIdentityInput,
    ContinuityManifest,
    continuity_amendment_plan_sha256,
)
from app.services.pilot_access_grandfather import (
    PILOT_ACCESS_GRANDFATHER_REQUIRED_ACCOUNT_COUNT,
    PILOT_ACCESS_GRANDFATHER_STAGING_ACCOUNT_COUNT,
    GrandfatherGrantInput,
    pilot_access_grant_plan_sha256,
    required_pilot_access_grandfather_account_count,
)

from activemodel.session_manager import get_session
from app.models.pilot_access_continuity_amendment import (
    PilotAccessContinuityAmendment,
    PilotAccessContinuityAmendmentIdentity,
)
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice_access_audit import PracticeAccessAction, PracticeAccessAudit
from sqlmodel import Session, select


@dataclass(frozen=True, slots=True)
class PilotAccessAuditReport:
    payload: dict[str, object]

    @property
    def valid(self) -> bool:
        return self.payload["cohort_integrity_valid"] is True


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _count_digests(values: list[str]) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return [
        {"count": count, "sha256": digest} for digest, count in sorted(counts.items())
    ]


def _expected_runtime_environment() -> str | None:
    environment = python_environment()
    return environment if environment in {"staging", "production"} else None


def _audit_with_session(
    session: Session,
    *,
    expected_environment: str | None,
    expected_clerk_issuer: str | None,
) -> PilotAccessAuditReport:
    if expected_environment == "staging":
        required_base_count = PILOT_ACCESS_GRANDFATHER_STAGING_ACCOUNT_COUNT
    elif expected_environment == "production":
        required_base_count = PILOT_ACCESS_GRANDFATHER_REQUIRED_ACCOUNT_COUNT
    else:
        required_base_count = required_pilot_access_grandfather_account_count()
    grants = list(session.exec(select(PilotAccessGrandfatherGrant)).all())
    amendments = list(session.exec(select(PilotAccessContinuityAmendment)).all())
    amendment_entries = list(
        session.exec(select(PilotAccessContinuityAmendmentIdentity)).all()
    )
    receipts = list(
        session.exec(
            select(PracticeAccessAudit).where(
                PracticeAccessAudit.action
                == PracticeAccessAction.doctor_identity_rebound.value
            )
        ).all()
    )

    integrity_error_count = 0
    for grant in grants:
        if not hmac.compare_digest(grant.email_sha256, _digest(grant.email_snapshot)):
            integrity_error_count += 1

    grant_by_id = {str(grant.id): grant for grant in grants}
    if len(grant_by_id) != len(grants):
        integrity_error_count += 1

    receipts_by_digest: dict[str, list[PracticeAccessAudit]] = {}
    for receipt in receipts:
        if receipt.evidence_sha256:
            receipts_by_digest.setdefault(receipt.evidence_sha256, []).append(receipt)
    predecessors_by_rebind_digest: dict[str, list[PilotAccessGrandfatherGrant]] = {}
    for grant in grants:
        if grant.revocation_plan_sha256:
            predecessors_by_rebind_digest.setdefault(
                grant.revocation_plan_sha256, []
            ).append(grant)

    predecessor_for_successor: dict[str, PilotAccessGrandfatherGrant] = {}
    successor_for_predecessor: dict[str, PilotAccessGrandfatherGrant] = {}
    rebind_error_count = 0
    for successor in grants:
        predecessors = predecessors_by_rebind_digest.get(
            successor.grant_plan_sha256, []
        )
        matching_receipts = receipts_by_digest.get(successor.grant_plan_sha256, [])
        if not predecessors or not matching_receipts:
            continue
        if len(predecessors) != 1 or len(matching_receipts) != 1:
            rebind_error_count += 1
            continue
        predecessor = predecessors[0]
        receipt = matching_receipts[0]
        expected_receipt_ref = (
            f"doctor-identity-rebind:{successor.doctor_id_snapshot}:"
            f"{_digest(successor.clerk_id)[:24]}"
        )
        compatible = bool(
            predecessor.id != successor.id
            and predecessor.revoked_at is not None
            and predecessor.revoked_at == successor.granted_at
            and predecessor.source_ref == successor.source_ref
            and hmac.compare_digest(predecessor.source_sha256, successor.source_sha256)
            and predecessor.email_snapshot == successor.email_snapshot
            and hmac.compare_digest(predecessor.email_sha256, successor.email_sha256)
            and predecessor.doctor_id_snapshot in (None, successor.doctor_id_snapshot)
            and predecessor.practice_id_snapshot
            in (None, successor.practice_id_snapshot)
            and successor.doctor_id_snapshot is not None
            and successor.practice_id_snapshot is not None
            and receipt.practice_id == successor.practice_id_snapshot
            and receipt.operator_ref == predecessor.revoked_by_ref
            and receipt.operator_ref == successor.granted_by_ref
            and receipt.reason == predecessor.revocation_reason
            and receipt.reason == successor.grant_reason
            and receipt.effective_at == successor.granted_at
            and receipt.expires_at is None
            and receipt.document_reference == expected_receipt_ref
        )
        if not compatible:
            rebind_error_count += 1
            continue
        predecessor_key = str(predecessor.id)
        successor_key = str(successor.id)
        if (
            predecessor_key in successor_for_predecessor
            or successor_key in predecessor_for_successor
        ):
            rebind_error_count += 1
            continue
        predecessor_for_successor[successor_key] = predecessor
        successor_for_predecessor[predecessor_key] = successor

    # A rebind audit that is also a grant revocation must have exactly one
    # compatible replacement. Ordinary offboarding revocations have no rebind
    # receipt and remain valid history.
    for predecessor in grants:
        digest = predecessor.revocation_plan_sha256
        if not digest or digest not in receipts_by_digest:
            continue
        if str(predecessor.id) not in successor_for_predecessor:
            rebind_error_count += 1

    entries_by_amendment: dict[str, list[PilotAccessContinuityAmendmentIdentity]] = {}
    referenced_amendment_grants: set[str] = set()
    amendment_error_count = 0
    for entry in amendment_entries:
        entries_by_amendment.setdefault(str(entry.amendment_id), []).append(entry)
        if str(entry.grant_id) in referenced_amendment_grants:
            amendment_error_count += 1
        referenced_amendment_grants.add(str(entry.grant_id))

    amendment_by_id = {str(batch.id): batch for batch in amendments}
    if len(amendment_by_id) != len(amendments):
        amendment_error_count += 1
    if any(key not in amendment_by_id for key in entries_by_amendment):
        amendment_error_count += 1

    amendment_plan_digests: list[str] = []
    amendment_account_count = 0
    if (
        amendments
        and expected_environment in {"staging", "production"}
        and expected_clerk_issuer is None
    ):
        amendment_error_count += 1
    for batch in amendments:
        rows = entries_by_amendment.get(str(batch.id), [])
        amendment_account_count += len(rows)
        amendment_plan_digests.append(batch.plan_sha256)
        if len(rows) != batch.account_count or not rows:
            amendment_error_count += 1
            continue
        if (
            expected_environment is not None
            and batch.environment != expected_environment
        ):
            amendment_error_count += 1
        if (
            expected_clerk_issuer is not None
            and batch.clerk_issuer != expected_clerk_issuer
        ):
            amendment_error_count += 1

        manifest_inputs: list[ContinuityIdentityInput] = []
        seen_subjects: set[str] = set()
        for row in rows:
            grant = grant_by_id.get(str(row.grant_id))
            if grant is None:
                amendment_error_count += 1
                continue
            if row.clerk_subject in seen_subjects:
                amendment_error_count += 1
            seen_subjects.add(row.clerk_subject)
            if (
                row.clerk_issuer != batch.clerk_issuer
                or grant.clerk_id != row.clerk_subject
                or not hmac.compare_digest(grant.email_sha256, row.email_sha256)
                or grant.doctor_id_snapshot != row.doctor_id_snapshot
                or grant.practice_id_snapshot != row.practice_id_snapshot
                or grant.source_ref != batch.source_ref
                or not hmac.compare_digest(grant.source_sha256, batch.manifest_sha256)
                or not hmac.compare_digest(grant.grant_plan_sha256, batch.plan_sha256)
                or grant.granted_by_ref != batch.applied_by_ref
                or grant.grant_reason != batch.reason
                or grant.granted_at != batch.applied_at
                or (row.safe_prelogin and row.doctor_id_snapshot is not None)
                or (not row.safe_prelogin and row.doctor_id_snapshot is None)
            ):
                amendment_error_count += 1
            manifest_inputs.append(
                ContinuityIdentityInput(
                    issuer=row.clerk_issuer,
                    subject=row.clerk_subject,
                    verified_primary_email=grant.email_snapshot,
                    doctor_id=row.doctor_id_snapshot,
                    safe_prelogin=row.safe_prelogin,
                    continuity_basis=row.continuity_basis,
                    practice_id=row.practice_id_snapshot,
                )
            )
        if len(manifest_inputs) != len(rows):
            continue
        manifest = ContinuityManifest(
            environment=batch.environment,
            identities=tuple(
                sorted(
                    manifest_inputs,
                    key=lambda identity: (identity.issuer, identity.subject),
                )
            ),
            manifest_sha256=batch.manifest_sha256,
        )
        recomputed = continuity_amendment_plan_sha256(
            manifest,
            source_ref=batch.source_ref,
            operator_ref=batch.applied_by_ref,
            reason=batch.reason,
        )
        if not hmac.compare_digest(recomputed, batch.plan_sha256):
            amendment_error_count += 1

    successor_ids = set(predecessor_for_successor)
    initial_grants = [grant for grant in grants if str(grant.id) not in successor_ids]
    base_grants = [
        grant
        for grant in initial_grants
        if str(grant.id) not in referenced_amendment_grants
    ]
    if any(grant_id not in grant_by_id for grant_id in referenced_amendment_grants):
        amendment_error_count += 1

    base_source_sha256: str | None = None
    base_valid = False
    if base_grants:
        try:
            base_source_sha256 = canonical_clerk_email_pair_sha256(
                tuple(
                    ClerkCohortIdentity(
                        clerk_id=grant.clerk_id,
                        email=grant.email_snapshot,
                    )
                    for grant in base_grants
                )
            )
        except ClerkCohortError:
            base_source_sha256 = None
        source_refs = {grant.source_ref for grant in base_grants}
        source_digests = {grant.source_sha256 for grant in base_grants}
        operator_refs = {grant.granted_by_ref for grant in base_grants}
        reasons = {grant.grant_reason for grant in base_grants}
        plan_digests = {grant.grant_plan_sha256 for grant in base_grants}
        recomputed_plan: str | None = None
        if (
            base_source_sha256 is not None
            and len(source_refs) == 1
            and len(source_digests) == 1
            and len(operator_refs) == 1
            and len(reasons) == 1
        ):
            try:
                recomputed_plan = pilot_access_grant_plan_sha256(
                    [
                        GrandfatherGrantInput(
                            clerk_id=grant.clerk_id,
                            email=grant.email_snapshot,
                        )
                        for grant in base_grants
                    ],
                    source_ref=next(iter(source_refs)),
                    source_sha256=next(iter(source_digests)),
                    operator_ref=next(iter(operator_refs)),
                    reason=next(iter(reasons)),
                )
            except Exception:
                recomputed_plan = None
        base_valid = bool(
            len(base_grants) == required_base_count
            and base_source_sha256 is not None
            and len(source_digests) == 1
            and hmac.compare_digest(next(iter(source_digests)), base_source_sha256)
            and recomputed_plan is not None
            and len(plan_digests) == 1
            and hmac.compare_digest(next(iter(plan_digests)), recomputed_plan)
        )

    # Prove that every row belongs to one initial authorization lineage and
    # that a historical row has at most one audited rebind successor.
    lineage_error_count = 0
    reached: set[str] = set()
    active_lineage_count = 0
    revoked_lineage_count = 0
    for initial in initial_grants:
        cursor = initial
        local: set[str] = set()
        while True:
            key = str(cursor.id)
            if key in local or key in reached:
                lineage_error_count += 1
                break
            local.add(key)
            reached.add(key)
            successor = successor_for_predecessor.get(key)
            if successor is None:
                if cursor.revoked_at is None:
                    active_lineage_count += 1
                else:
                    revoked_lineage_count += 1
                break
            if cursor.revoked_at is None:
                lineage_error_count += 1
                break
            cursor = successor
    if len(reached) != len(grants):
        lineage_error_count += len(grants) - len(reached)

    active_count = sum(grant.revoked_at is None for grant in grants)
    revoked_count = len(grants) - active_count
    total_integrity_errors = (
        integrity_error_count
        + amendment_error_count
        + rebind_error_count
        + lineage_error_count
    )
    cohort_integrity_valid = bool(base_valid and total_integrity_errors == 0)
    if not grants:
        state = "empty"
    elif not cohort_integrity_valid:
        state = "invalid"
    elif active_lineage_count and revoked_lineage_count:
        state = "mixed"
    elif active_lineage_count:
        state = "active"
    else:
        state = "revoked"

    payload: dict[str, object] = {
        "active_count": active_count,
        "amendment_account_count": amendment_account_count,
        "amendment_count": len(amendments),
        "amendment_plan_digests": _count_digests(amendment_plan_digests),
        "base_count": len(base_grants),
        "canonical_source_sha256": base_source_sha256,
        "cohort_integrity_valid": cohort_integrity_valid,
        "doctor_snapshot_count": sum(
            grant.doctor_id_snapshot is not None for grant in grants
        ),
        "grant_plan_digests": _count_digests(
            [grant.grant_plan_sha256 for grant in grants]
        ),
        "integrity_error_count": total_integrity_errors,
        "lineage_count": len(initial_grants),
        "prelogin_snapshot_count": sum(
            grant.doctor_id_snapshot is None for grant in grants
        ),
        "rebind_count": len(predecessor_for_successor),
        "revocation_plan_digests": _count_digests(
            [
                grant.revocation_plan_sha256
                for grant in grants
                if grant.revocation_plan_sha256
            ]
        ),
        "revoked_count": revoked_count,
        "source_digests": _count_digests([grant.source_sha256 for grant in grants]),
        "state": state,
        "total_count": len(grants),
    }
    return PilotAccessAuditReport(payload=payload)


def audit_pilot_access_state(
    *,
    expected_environment: str | None = None,
    expected_clerk_issuer: str | None = None,
    session: Session | None = None,
) -> PilotAccessAuditReport:
    """Audit base grants, omissions, revocations, and audited rebind chains."""

    runtime_environment = (
        expected_environment
        if expected_environment is not None
        else _expected_runtime_environment()
    )
    runtime_issuer = expected_clerk_issuer
    if runtime_issuer is None and AUTHORITY_KERNEL_SETTINGS.has_valid_clerk_issuer():
        runtime_issuer = AUTHORITY_KERNEL_SETTINGS.clerk_issuer
    if session is not None:
        return _audit_with_session(
            session,
            expected_environment=runtime_environment,
            expected_clerk_issuer=runtime_issuer,
        )
    with get_session() as managed:
        return _audit_with_session(
            cast(Session, managed),
            expected_environment=runtime_environment,
            expected_clerk_issuer=runtime_issuer,
        )
