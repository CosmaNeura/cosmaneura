"""Short-lived verifier-only aliases; never retain or reconstruct the bearer.

The issuer requires current owner authority and a separately qualified delivery
authorization source. It does not rotate the canonical invitation or claim that
a booking or self-attested patient choice authorizes the first contact.
"""

import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, cast
from uuid import UUID

from typeid import TypeID

from app.configuration.visit_prep import VisitPrepSettings
from app.services.visit_prep_command_contract import _new_access_token
from app.services.visit_prep_email_evidence import (
    _closed_object,
    _digest,
    _json,
    _time,
    _uuid,
)
from app.services.visit_prep_email_policy import _authorize_owner
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import _lock_current_lineage, _lock_visit_prep
from app.utils.encryption import decrypt_value, encrypt_value, pseudonymous_digest

from activemodel.session_manager import get_engine
from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_delivery_link import VisitPrepDeliveryLink
from sqlmodel import Session, select


class DeliveryLinkUnavailable(ValueError):
    def __init__(self):
        super().__init__("Visit Prep delivery link is unavailable.")


@dataclass(frozen=True, repr=False)
class DeliveryLinkIntent:
    visit_prep_id: TypeIDType
    credential_id: TypeIDType
    operation_id: str
    expires_at: datetime


class DeliveryLinkSourceVerifier(Protocol):
    def __call__(
        self, session: Session, intent: DeliveryLinkIntent, *, checked_at: datetime
    ) -> bool:
        """Check exact current permission, delivery purpose and source under locks.

        No installed default. Trusted Core adapter only: no browser-selected
        verifier, I/O, commit, rollback or release of source/consent/approval locks.
        A keyed label, booking contact or patient self-attestation is not proof.
        """
        ...


@dataclass(frozen=True, repr=False)
class DeliveryLinkReceipt:
    link_id: str
    disposition: Literal["issued", "replayed"]
    expires_at: datetime
    access_token: str | None


def _manifest(row: VisitPrepDeliveryLink) -> dict:
    return {
        "purpose": "visit_prep_delivery_link_v1",
        "format_version": 1,
        "id": str(row.id),
        "prep": str(row.visit_prep_id),
        "owner": str(row.owner_practice_member_id),
        "version": str(row.question_set_version_id),
        "credential": str(row.credential_id),
        "operation": str(row.operation_id),
        "token_hash": row.token_hash,
        "issued_at": _time(row.issued_at).isoformat(),
        "expires_at": _time(row.expires_at).isoformat(),
    }


def verify_delivery_link(session: Session, row: VisitPrepDeliveryLink) -> None:
    try:
        if (
            type(row.envelope_format_version) is not int
            or row.envelope_format_version != 1
            or not _digest(row.token_hash)
            or not _digest(row.payload_hmac)
            or not _uuid(str(row.operation_id))
            or not _time(row.issued_at)
            < _time(row.expires_at)
            <= row.issued_at + timedelta(hours=24)
            or row.issued_at > _database_now(session)
            or not 1 <= len(row.evidence_ciphertext.encode()) <= 4096
        ):
            raise ValueError
        plaintext = decrypt_value(row.evidence_ciphertext)
        if len(plaintext.encode()) > 2500:
            raise ValueError
        payload = json.loads(plaintext, object_pairs_hook=_closed_object)
        if _json(payload) != _json(_manifest(row)) or not hmac.compare_digest(
            row.payload_hmac,
            pseudonymous_digest(
                _json(payload), context="visit-prep-delivery-link-evidence-v1"
            ),
        ):
            raise ValueError
        audits = session.exec(
            select(AuthorityAuditEvent)
            .where(
                AuthorityAuditEvent.resource_type == "visit_prep_link",
                AuthorityAuditEvent.resource_ref == str(row.id),
            )
            .limit(2)
        ).all()
        if len(audits) != 1:
            raise ValueError
        audit = audits[0]
        if (
            audit.event_source != "platform_authoritative"
            or audit.event_type != "visit_prep.delivery_link_issued"
            or audit.outcome != "issued"
            or audit.request_digest != row.payload_hmac
            or audit.client_event_id != row.operation_id
            or audit.occurred_at != row.issued_at
            or not row.issued_at <= audit.recorded_at <= _database_now(session)
            or audit.route != "visit_prep_delivery_link"
            or audit.intent != "bounded_delivery_link"
            or audit.platform_refs
            != [
                {"kind": "visit_prep", "ref": str(row.visit_prep_id)},
                {"kind": "practice_member", "ref": str(row.owner_practice_member_id)},
            ]
            or any(
                v is not None
                for v in (
                    audit.actor_id,
                    audit.tenant_id,
                    audit.person_id,
                    audit.interaction_id,
                    audit.workload_client_id,
                    audit.content_sha256,
                )
            )
        ):
            raise ValueError
    except Exception:
        raise DeliveryLinkUnavailable() from None


def delivery_token_deadline(
    session: Session, *, prep, credential, token_hash: str
) -> datetime:
    """Bind a supplied bearer to locked current lineage without minting authority."""
    try:
        if hmac.compare_digest(credential.token_hash, token_hash):
            return credential.expires_at
        alias = session.exec(
            select(VisitPrepDeliveryLink).where(
                VisitPrepDeliveryLink.token_hash == token_hash
            )
        ).one_or_none()
        if alias is None:
            raise ValueError
        verify_delivery_link(session, alias)
        now = _database_now(session)
        if (
            alias.visit_prep_id != prep.id
            or alias.owner_practice_member_id != prep.owner_practice_member_id
            or alias.question_set_version_id != prep.question_set_version_id
            or alias.credential_id != credential.id
            or not alias.issued_at <= now < alias.expires_at
            or alias.expires_at > credential.expires_at
        ):
            raise ValueError
        return alias.expires_at
    except Exception:
        raise DeliveryLinkUnavailable() from None


def issue_locked_delivery_link(
    session: Session,
    *,
    intent: DeliveryLinkIntent,
    settings: VisitPrepSettings,
    source_verifier: DeliveryLinkSourceVerifier | None = None,
    view_as_active: bool = False,
    **scope,
) -> DeliveryLinkReceipt:
    """Private transaction composition; caller must commit before using the bearer."""
    if (
        type(intent) is not DeliveryLinkIntent
        or not _uuid(intent.operation_id)
        or not callable(source_verifier)
        or view_as_active is not False
        or type(settings) is not VisitPrepSettings
        or not settings.public_api_enabled
    ):
        raise DeliveryLinkUnavailable()
    _time(intent.expires_at)
    owner, practice, _ = _authorize_owner(session, settings=settings, **scope)
    prep = _lock_visit_prep(
        session, owner_practice_member_id=owner.id, visit_prep_id=intent.visit_prep_id
    )
    schedule, credential = _lock_current_lineage(session, visit_prep=prep)

    def context_valid(root, current_schedule, current_credential, now):
        return (
            root.practice_id == practice.id
            and root.owner_doctor_id == owner.doctor_id
            and current_credential.id == intent.credential_id == credential.id
            and current_schedule.id == schedule.id
            and current_credential.revoked_at is None
            and root.revoked_at is None
            and root.completed_at is None
            and root.misdirected_at is None
            and now
            < intent.expires_at
            <= min(
                current_credential.expires_at,
                current_schedule.scheduled_at,
                now + timedelta(hours=24),
            )
        )

    def current():
        _, _, now = _authorize_owner(session, settings=settings, **scope)
        if not context_valid(prep, schedule, credential, now):
            raise DeliveryLinkUnavailable()
        transaction, nested = (
            session.get_transaction(),
            session.get_nested_transaction(),
        )
        assert source_verifier is not None
        if (
            source_verifier(session, intent, checked_at=now) is not True
            or session.get_transaction() is not transaction
            or session.get_nested_transaction() is not nested
            or transaction is None
            or not transaction.is_active
        ):
            raise DeliveryLinkUnavailable()
        final_prep = _lock_visit_prep(
            session, owner_practice_member_id=owner.id, visit_prep_id=prep.id
        )
        final_schedule, final_credential = _lock_current_lineage(
            session, visit_prep=final_prep
        )
        _, _, final_now = _authorize_owner(session, settings=settings, **scope)
        if not context_valid(final_prep, final_schedule, final_credential, final_now):
            raise DeliveryLinkUnavailable()
        return final_now

    issued_at = current()
    prior = session.exec(
        select(VisitPrepDeliveryLink)
        .where(
            VisitPrepDeliveryLink.visit_prep_id == prep.id,
            VisitPrepDeliveryLink.operation_id == UUID(intent.operation_id),
        )
        .with_for_update()
    ).one_or_none()
    if prior is not None:
        verify_delivery_link(session, prior)
        if (
            prior.credential_id != intent.credential_id
            or prior.expires_at != intent.expires_at
        ):
            raise DeliveryLinkUnavailable()
        result = DeliveryLinkReceipt(str(prior.id), "replayed", prior.expires_at, None)
    else:
        from app.services.visit_prep_command_contract import access_token_hash

        token = _new_access_token()
        row = VisitPrepDeliveryLink(
            visit_prep_id=prep.id,
            owner_practice_member_id=owner.id,
            question_set_version_id=prep.question_set_version_id,
            credential_id=credential.id,
            operation_id=UUID(intent.operation_id),
            token_hash=access_token_hash(token),
            payload_hmac="0" * 64,
            evidence_ciphertext="",
            issued_at=issued_at,
            expires_at=intent.expires_at,
        )
        row.id = cast(TypeIDType, TypeID("visit_prep_link"))
        payload = _json(_manifest(row))
        row.evidence_ciphertext = encrypt_value(payload)
        row.payload_hmac = pseudonymous_digest(
            payload, context="visit-prep-delivery-link-evidence-v1"
        )
        session.add(row)
        session.flush()
        session.add(
            AuthorityAuditEvent(
                event_type="visit_prep.delivery_link_issued",
                outcome="issued",
                occurred_at=issued_at,
                client_event_id=row.operation_id,
                resource_type="visit_prep_link",
                resource_ref=str(row.id),
                request_digest=row.payload_hmac,
                route="visit_prep_delivery_link",
                intent="bounded_delivery_link",
                platform_refs=[
                    {"kind": "visit_prep", "ref": str(prep.id)},
                    {"kind": "practice_member", "ref": str(owner.id)},
                ],
            )
        )
        session.flush()
        verify_delivery_link(session, row)
        result = DeliveryLinkReceipt(str(row.id), "issued", row.expires_at, token)
    current()
    return result


def issue_delivery_link(**arguments) -> DeliveryLinkReceipt:
    result = None
    try:
        with Session(get_engine()) as session:
            result = issue_locked_delivery_link(session, **arguments)
            session.commit()
    except Exception:
        result = None
    if result is None:
        raise DeliveryLinkUnavailable() from None
    return result
