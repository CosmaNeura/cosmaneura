import secrets
from datetime import datetime, timedelta, timezone

import pytest

from activemodel.session_manager import get_session
from app.models.organization_access import (
    ProviderOrganization,
)
from app.models.practice import Practice
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel


def _join_code(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4).upper()[:7]}"


def _organization(*, domain: str) -> ProviderOrganization:
    return ProviderOrganization(
        legal_name="Bootstrap Guard Organization",
        slug=f"bootstrap-guard-{secrets.token_hex(4)}",
        email_domain=domain,
        status="active",
        access_code_salt="a" * 32,
        access_code_digest=secrets.token_hex(32),
        access_code_version=1,
        access_code_created_at=datetime.now(timezone.utc),
    ).save()


def test_negotiated_baa_required_database_rejects_clickwrap_state():
    practice = Practice(
        legal_name="Negotiated BAA Only, PLLC",
        join_code=_join_code("N"),
        email_domain="negotiated-only.example",
        negotiated_baa_required=True,
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=14),
    )

    with get_session() as session:
        savepoint = session.begin_nested()
        session.add(practice)
        with pytest.raises(
            DBAPIError,
            match="ck_practice_negotiated_baa_required_no_clickwrap",
        ):
            session.flush()
        savepoint.rollback()


def test_practice_organization_domain_database_constraint_rejects_mismatch():
    organization = _organization(domain="fiat.example")
    practice = Practice(
        legal_name="Mismatched Practice, PLLC",
        join_code=_join_code("D"),
        email_domain="other.example",
        organization_id=organization.id,
        negotiated_baa_required=True,
    )

    with get_session() as session:
        savepoint = session.begin_nested()
        session.add(practice)
        with pytest.raises(DBAPIError, match="fk_practice_organization_domain"):
            session.flush()
        savepoint.rollback()


def test_bootstrap_guard_model_metadata_matches_database_migration():
    practice_table = SQLModel.metadata.tables["practice"]
    organization_table = SQLModel.metadata.tables["provider_organization"]
    roster_table = SQLModel.metadata.tables["organization_roster_approval"]
    member_table = SQLModel.metadata.tables["practice_member"]
    audit_table = SQLModel.metadata.tables["practice_access_audit"]

    assert {constraint.name for constraint in practice_table.constraints}.issuperset(
        {
            "practice_ck_practice_negotiated_baa_required_no_clickwrap_check",
            "fk_practice_organization_domain",
        }
    )
    assert "practice_organization_id_idx" in {
        index.name for index in practice_table.indexes
    }
    assert "uq_provider_organization_id_email_domain" in {
        constraint.name for constraint in organization_table.constraints
    }
    assert "uq_org_roster_approval_claim_binding" in {
        constraint.name for constraint in roster_table.constraints
    }
    assert {
        "fk_practice_member_organization_claim_binding",
        "practice_member_ck_practice_member_role_check",
    }.issubset({constraint.name for constraint in member_table.constraints})
    assert "uq_practice_access_audit_provider_bootstrap_ref" in {
        index.name for index in audit_table.indexes
    }
