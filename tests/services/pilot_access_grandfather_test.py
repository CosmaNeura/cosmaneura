import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest

from app.services.pilot_access_grandfather import (
    GrandfatherGrantInput,
    PilotAccessGrandfatherError,
    active_pilot_access_grant,
    grant_pilot_access_grants,
    pilot_access_grant_plan_sha256,
    pilot_access_revoke_plan_sha256,
    plan_pilot_access_grants,
    revoke_pilot_access_grants,
    revoked_pilot_access_grant,
)

from app.models.doctor import Doctor, DoctorRole
from app.models.pilot_access_grandfather_grant import PilotAccessGrandfatherGrant
from app.models.practice import Practice, PracticeMember

SOURCE_REF = "clerk-export:legacy-pilot-2026-08-23"
GRANT_OPERATOR = "change:legacy-pilot-grant"
GRANT_REASON = "Preserve approved legacy pilot access"


def _entries(label: str) -> list[GrandfatherGrantInput]:
    nonce = secrets.token_hex(5)
    return [
        GrandfatherGrantInput(
            clerk_id=f"user_{label}_{nonce}_{index:02d}",
            email=f"pilot-{label}-{nonce}-{index:02d}@example.com",
        )
        for index in range(47)
    ]


def _grant(
    entries: list[GrandfatherGrantInput],
    *,
    now: datetime | None = None,
):
    source_sha256 = _source_sha256(entries)
    plan_sha256 = pilot_access_grant_plan_sha256(
        entries,
        source_ref=SOURCE_REF,
        source_sha256=source_sha256,
        operator_ref=GRANT_OPERATOR,
        reason=GRANT_REASON,
    )
    return grant_pilot_access_grants(
        entries,
        source_ref=SOURCE_REF,
        source_sha256=source_sha256,
        operator_ref=GRANT_OPERATOR,
        reason=GRANT_REASON,
        confirm_plan_sha256=plan_sha256,
        now=now,
    )


def _source_sha256(entries: list[GrandfatherGrantInput]) -> str:
    canonical = "".join(
        f"{entry.clerk_id.strip()}\t{entry.email.strip().lower()}\n"
        for entry in sorted(entries, key=lambda item: item.clerk_id.strip())
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_grant_records_all_47_clerk_accounts_without_mutating_tenant_state():
    now = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)
    entries = _entries("complete")
    existing = Doctor(
        clerk_id=entries[0].clerk_id,
        email=entries[0].email.upper(),
        biography="",
    ).save()
    practice = Practice(
        legal_name="Existing Pilot Practice",
        join_code=f"G{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        billing_exempt=False,
        negotiated_baa_on_file=False,
        negotiated_baa_required=False,
    ).save()
    PracticeMember(
        practice_id=practice.id,
        doctor_id=existing.id,
        role="workforce",
    ).save()

    result = _grant(entries, now=now)

    assert result.created_count == 47
    assert result.unchanged_count == 0
    assert PilotAccessGrandfatherGrant.count() == 47
    by_clerk = {grant.clerk_id: grant for grant in result.grants}
    existing_grant = by_clerk[entries[0].clerk_id]
    assert existing_grant.doctor_id_snapshot == existing.id
    assert existing_grant.practice_id_snapshot == practice.id
    assert existing_grant.email_snapshot == entries[0].email
    assert len(existing_grant.email_sha256) == 64
    assert existing_grant.source_sha256 == _source_sha256(entries)
    assert existing_grant.granted_at == now

    prelogin_grant = by_clerk[entries[1].clerk_id]
    assert prelogin_grant.doctor_id_snapshot is None
    assert prelogin_grant.practice_id_snapshot is None

    practice.refresh()
    assert practice.billing_exempt is False
    assert practice.negotiated_baa_on_file is False
    assert practice.negotiated_baa_required is False
    assert practice.baa_accepted_at is None
    assert practice.organization_id is None
    assert PracticeMember.where(PracticeMember.practice_id == practice.id).count() == 1


def test_grant_exact_retry_is_idempotent_after_prelogin_doctor_is_created():
    entries = _entries("retry")
    first = _grant(entries)
    newly_created = Doctor(
        clerk_id=entries[1].clerk_id,
        email=entries[1].email,
        biography="",
    ).save()

    retry = _grant(entries)

    assert retry.created_count == 0
    assert retry.unchanged_count == 47
    assert {grant.id for grant in retry.grants} == {grant.id for grant in first.grants}
    assert PilotAccessGrandfatherGrant.count() == 47
    persisted = PilotAccessGrandfatherGrant.get(clerk_id=newly_created.clerk_id)
    assert persisted is not None
    assert persisted.doctor_id_snapshot is None


def test_active_predicate_binds_prelogin_grant_to_clerk_subject_and_email():
    entries = _entries("predicate")
    granted_at = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)
    predicate_at = datetime(2026, 8, 23, 14, 0, 1, tzinfo=timezone.utc)
    _grant(entries, now=granted_at)
    doctor = Doctor(
        clerk_id=entries[2].clerk_id,
        email=entries[2].email,
        biography="",
    ).save()

    assert active_pilot_access_grant(doctor, at=predicate_at) is not None

    unpersisted_impostor = Doctor(
        clerk_id=entries[3].clerk_id,
        email=entries[3].email,
        biography="",
    )
    assert active_pilot_access_grant(unpersisted_impostor, at=predicate_at) is None

    doctor.email = f"changed-{secrets.token_hex(4)}@example.com"
    doctor.save()
    assert active_pilot_access_grant(doctor, at=predicate_at) is None


def test_existing_doctor_email_mismatch_fails_batch_without_roster_pii():
    entries = _entries("mismatch")
    source_sha256 = _source_sha256(entries)
    actual_email = f"actual-{secrets.token_hex(4)}@example.com"
    Doctor(
        clerk_id=entries[0].clerk_id,
        email=actual_email,
        biography="",
    ).save()
    plan_sha256 = pilot_access_grant_plan_sha256(
        entries,
        source_ref=SOURCE_REF,
        source_sha256=source_sha256,
        operator_ref=GRANT_OPERATOR,
        reason=GRANT_REASON,
    )

    with pytest.raises(PilotAccessGrandfatherError) as exc_info:
        grant_pilot_access_grants(
            entries,
            source_ref=SOURCE_REF,
            source_sha256=source_sha256,
            operator_ref=GRANT_OPERATOR,
            reason=GRANT_REASON,
            confirm_plan_sha256=plan_sha256,
        )

    message = str(exc_info.value)
    assert entries[0].email not in message
    assert actual_email not in message
    assert entries[0].clerk_id not in message
    assert PilotAccessGrandfatherGrant.count() == 0


def test_plan_rejects_prelogin_email_owned_by_a_different_clerk_subject():
    entries = _entries("prelogin-email-owner")
    conflicting_clerk_id = f"user_existing_{secrets.token_hex(6)}"
    Doctor(
        clerk_id=conflicting_clerk_id,
        email=entries[0].email.upper(),
        biography="",
    ).save()

    with pytest.raises(
        PilotAccessGrandfatherError, match="different existing account"
    ) as exc_info:
        plan_pilot_access_grants(
            entries,
            source_ref=SOURCE_REF,
            source_sha256=_source_sha256(entries),
            operator_ref=GRANT_OPERATOR,
            reason=GRANT_REASON,
        )

    message = str(exc_info.value)
    assert entries[0].email not in message
    assert entries[0].clerk_id not in message
    assert conflicting_clerk_id not in message
    assert PilotAccessGrandfatherGrant.count() == 0


def test_plan_rejects_a_previously_revoked_member_without_roster_pii():
    entries = _entries("revoked-member")
    doctor = Doctor(
        clerk_id=entries[0].clerk_id,
        email=entries[0].email,
        biography="",
    ).save()
    practice = Practice(
        legal_name="Previously Offboarded Practice",
        join_code=f"R{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
    ).save()
    revoked_at = datetime.now(timezone.utc)
    PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="workforce",
        access_revoked_at=revoked_at,
        access_revoked_by_ref="test-suite",
        access_revocation_reason="Prior approved offboarding",
        access_records_retain_until=revoked_at + timedelta(days=365 * 8),
    ).save()

    with pytest.raises(
        PilotAccessGrandfatherError, match="prior access revocation"
    ) as exc_info:
        plan_pilot_access_grants(
            entries,
            source_ref=SOURCE_REF,
            source_sha256=_source_sha256(entries),
            operator_ref=GRANT_OPERATOR,
            reason=GRANT_REASON,
        )

    message = str(exc_info.value)
    assert entries[0].email not in message
    assert entries[0].clerk_id not in message
    assert PilotAccessGrandfatherGrant.count() == 0


def test_grant_conflict_is_all_or_nothing_and_uses_aggregate_error():
    first_entries = _entries("first")
    _grant(first_entries)
    second_entries = _entries("second")
    second_entries[0] = first_entries[0]
    second_source_sha256 = _source_sha256(second_entries)
    plan_sha256 = pilot_access_grant_plan_sha256(
        second_entries,
        source_ref="clerk-export:second-plan",
        source_sha256=second_source_sha256,
        operator_ref=GRANT_OPERATOR,
        reason=GRANT_REASON,
    )

    with pytest.raises(PilotAccessGrandfatherError) as exc_info:
        grant_pilot_access_grants(
            second_entries,
            source_ref="clerk-export:second-plan",
            source_sha256=second_source_sha256,
            operator_ref=GRANT_OPERATOR,
            reason=GRANT_REASON,
            confirm_plan_sha256=plan_sha256,
        )

    assert first_entries[0].email not in str(exc_info.value)
    assert PilotAccessGrandfatherGrant.count() == 47
    assert (
        PilotAccessGrandfatherGrant.where(
            PilotAccessGrandfatherGrant.source_ref == "clerk-export:second-plan"
        ).count()
        == 0
    )


def test_grant_requires_exact_47_account_manifest_and_confirmation():
    entries = _entries("cardinality")
    source_sha256 = _source_sha256(entries)

    with pytest.raises(PilotAccessGrandfatherError, match="reviewed account count"):
        pilot_access_grant_plan_sha256(
            entries[:-1],
            source_ref=SOURCE_REF,
            source_sha256=source_sha256,
            operator_ref=GRANT_OPERATOR,
            reason=GRANT_REASON,
        )

    with pytest.raises(PilotAccessGrandfatherError, match="confirmed plan"):
        grant_pilot_access_grants(
            entries,
            source_ref=SOURCE_REF,
            source_sha256=source_sha256,
            operator_ref=GRANT_OPERATOR,
            reason=GRANT_REASON,
            confirm_plan_sha256="f" * 64,
        )
    assert PilotAccessGrandfatherGrant.count() == 0


def test_staging_uses_its_separately_reviewed_six_account_cohort(monkeypatch):
    entries = _entries("staging")[:6]
    monkeypatch.setattr(
        "app.services.pilot_access_grandfather.is_staging", lambda: True
    )

    plan = plan_pilot_access_grants(
        entries,
        source_ref="clerk-export:staging-pilot-2026-08-23",
        source_sha256=_source_sha256(entries),
        operator_ref=GRANT_OPERATOR,
        reason=GRANT_REASON,
    )

    assert plan.account_count == 6
    with pytest.raises(PilotAccessGrandfatherError, match="reviewed account count"):
        plan_pilot_access_grants(
            _entries("wrong-staging-count"),
            source_ref="clerk-export:staging-pilot-2026-08-23",
            source_sha256="a" * 64,
            operator_ref=GRANT_OPERATOR,
            reason=GRANT_REASON,
        )


def test_plan_rejects_source_digest_for_a_different_identity_cohort_without_pii():
    entries = _entries("source-digest")
    unrelated_entries = _entries("unrelated-source")

    with pytest.raises(PilotAccessGrandfatherError, match="source digest") as exc_info:
        plan_pilot_access_grants(
            entries,
            source_ref=SOURCE_REF,
            source_sha256=_source_sha256(unrelated_entries),
            operator_ref=GRANT_OPERATOR,
            reason=GRANT_REASON,
        )

    message = str(exc_info.value)
    assert entries[0].email not in message
    assert entries[0].clerk_id not in message
    assert PilotAccessGrandfatherGrant.count() == 0


def test_revoke_is_atomic_idempotent_and_retains_evidence_for_seven_years():
    granted_at = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)
    revoked_at = datetime(2028, 2, 29, 16, 30, tzinfo=timezone.utc)
    entries = _entries("revoke")
    _grant(entries, now=granted_at)
    doctor = Doctor(
        clerk_id=entries[0].clerk_id,
        email=entries[0].email,
        biography="",
    ).save()
    clerk_ids = [entries[0].clerk_id, entries[1].clerk_id]
    plan_sha256 = pilot_access_revoke_plan_sha256(
        clerk_ids,
        operator_ref="change:pilot-offboard",
        reason="Offboard approved legacy users",
    )

    result = revoke_pilot_access_grants(
        clerk_ids,
        operator_ref="change:pilot-offboard",
        reason="Offboard approved legacy users",
        confirm_plan_sha256=plan_sha256,
        now=revoked_at,
    )

    assert result.revoked_count == 2
    assert result.unchanged_count == 0
    assert {grant.retain_until for grant in result.grants} == {
        datetime(2035, 2, 28, 16, 30, tzinfo=timezone.utc)
    }
    assert active_pilot_access_grant(doctor) is None
    doctor.refresh()
    assert doctor.deleted_at == revoked_at
    assert revoked_pilot_access_grant(entries[0].clerk_id) is not None
    assert revoked_pilot_access_grant(entries[1].clerk_id) is not None

    retry = revoke_pilot_access_grants(
        clerk_ids,
        operator_ref="change:pilot-offboard",
        reason="Offboard approved legacy users",
        confirm_plan_sha256=plan_sha256,
        now=revoked_at,
    )
    assert retry.revoked_count == 0
    assert retry.unchanged_count == 2
    assert {grant.id for grant in retry.grants} == {grant.id for grant in result.grants}


def test_revoke_missing_grant_does_not_partially_revoke():
    entries = _entries("revoke-atomic")
    _grant(entries)
    clerk_ids = [entries[0].clerk_id, "user_missing_grandfather_subject"]
    plan_sha256 = pilot_access_revoke_plan_sha256(
        clerk_ids,
        operator_ref="change:pilot-offboard-missing",
        reason="Test atomic refusal",
    )

    with pytest.raises(PilotAccessGrandfatherError, match="missing or.*conflicting"):
        revoke_pilot_access_grants(
            clerk_ids,
            operator_ref="change:pilot-offboard-missing",
            reason="Test atomic refusal",
            confirm_plan_sha256=plan_sha256,
        )

    persisted = PilotAccessGrandfatherGrant.get(clerk_id=entries[0].clerk_id)
    assert persisted is not None
    assert persisted.revoked_at is None
    assert revoked_pilot_access_grant(entries[0].clerk_id) is None


def test_revoke_refuses_to_disable_a_platform_administrator():
    entries = _entries("admin-revoke")
    doctor = Doctor(
        clerk_id=entries[0].clerk_id,
        email=entries[0].email,
        biography="",
        role=DoctorRole.admin,
    ).save()
    _grant(entries)
    plan_sha256 = pilot_access_revoke_plan_sha256(
        [entries[0].clerk_id],
        operator_ref="change:pilot-admin-offboard",
        reason="Test protected administrator",
    )

    with pytest.raises(PilotAccessGrandfatherError, match="administrators"):
        revoke_pilot_access_grants(
            [entries[0].clerk_id],
            operator_ref="change:pilot-admin-offboard",
            reason="Test protected administrator",
            confirm_plan_sha256=plan_sha256,
        )

    doctor.refresh()
    grant = PilotAccessGrandfatherGrant.get(clerk_id=entries[0].clerk_id)
    assert doctor.deleted_at is None
    assert grant is not None
    assert grant.revoked_at is None
