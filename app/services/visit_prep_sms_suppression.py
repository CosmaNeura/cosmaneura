"""Core-owned STOP registry and shared transaction lock, not a provider adapter.

The future authenticated provider adapter must normalize and verify each STOP.
No browser or flag can admit one. Suppression is terminal for the exact sender
resource and recipient, including across practices sharing that sender. Rotating
credentials/connection IDs does not change scope. No START/reset exists here.
"""

import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, cast

from typeid import TypeID

from app.configuration import visit_prep_sms
from app.configuration.visit_prep_sms import VisitPrepSMSRouting
from app.services.visit_prep_email_evidence import _closed_object, _digest, _json, _time
from app.services.visit_prep_packages import _database_now
from app.utils.encryption import decrypt_value, encrypt_value, pseudonymous_digest

from activemodel.session_manager import get_engine
from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_sms_stop import VisitPrepSMSStopEvent
from sqlalchemy import text
from sqlmodel import Session, col, select


class SMSSuppressionUnavailable(ValueError):
    def __init__(self):
        super().__init__("SMS suppression is unavailable.")


def sms_recipient_digest(phone: str) -> str:
    if not isinstance(phone, str) or re.fullmatch(r"\+[1-9][0-9]{7,14}", phone) is None:
        raise SMSSuppressionUnavailable()
    return pseudonymous_digest(phone, context="visit-prep-sms-recipient-v1")


def sms_sender_digest(routing: VisitPrepSMSRouting) -> str:
    if (
        type(routing) is not VisitPrepSMSRouting
        or re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", routing.adapter_namespace) is None
        or not 1 <= len(routing.account_reference) <= 256
        or any(not 33 <= ord(char) <= 126 for char in routing.account_reference)
        or re.fullmatch(r"\+[1-9][0-9]{7,14}", routing.sender_number) is None
    ):
        raise SMSSuppressionUnavailable()
    return pseudonymous_digest(
        _json(
            {
                "adapter": routing.adapter_namespace,
                "account": routing.account_reference,
                "sender": routing.sender_number,
            }
        ),
        context="visit-prep-sms-sender-v1",
    )


def configured_sms_sender_digest() -> str | None:
    routing = visit_prep_sms.ROUTING
    if type(routing) is not VisitPrepSMSRouting:
        raise SMSSuppressionUnavailable()
    if routing == VisitPrepSMSRouting():
        return None
    return sms_sender_digest(routing)


@dataclass(frozen=True, repr=False)
class NormalizedSMSStop:
    sender_digest: str
    recipient_digest: str
    source_event_digest: str
    source_evidence_hmac: str
    occurred_at: datetime


class SMSStopSourceVerifier(Protocol):
    def __call__(
        self, session: Session, event: NormalizedSMSStop, *, checked_at: datetime
    ) -> bool:
        """Verify authenticated adapter source and exact normalized STOP intent.

        A keyed label is not authentication. Recheck current sender/account source
        binding and original verified provider event against every field, including
        occurrence time. No provider I/O, commit, rollback or lock release is allowed.
        This caller is trusted Core code; a browser cannot choose the verifier.
        """
        ...


@dataclass(frozen=True, repr=False)
class SMSStopReceipt:
    event_id: str
    recorded_at: datetime
    disposition: Literal["recorded", "replayed"]


def _lock_recipient(session: Session, sender: str, recipient: str) -> None:
    if (
        not _digest(sender)
        or not _digest(recipient)
        or session.connection().get_isolation_level() != "READ COMMITTED"
    ):
        raise SMSSuppressionUnavailable()
    # The storage guard uses this exact key too. Contention fails closed rather
    # than waiting indefinitely or permitting dispatch without the STOP fence.
    key = f"visit-prep-sms-stop-v1:{sender}:{recipient}"
    if (
        session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        ).scalar_one()
        is not True
    ):
        raise SMSSuppressionUnavailable()


def _manifest(row: VisitPrepSMSStopEvent) -> dict:
    return {
        "format_version": 1,
        "purpose": "visit_prep_sms_sender_stop",
        "event_id": str(row.id),
        "sender_digest": row.sender_digest,
        "recipient_digest": row.recipient_digest,
        "source_event_digest": row.source_event_digest,
        "source_evidence_hmac": row.source_evidence_hmac,
        "occurred_at": _time(row.occurred_at).isoformat(),
    }


def _verify(session: Session, row: VisitPrepSMSStopEvent) -> None:
    if (
        type(row.envelope_format_version) is not int
        or row.envelope_format_version != 1
        or any(
            not _digest(value)
            for value in (
                row.sender_digest,
                row.recipient_digest,
                row.source_event_digest,
                row.source_evidence_hmac,
                row.payload_hmac,
            )
        )
        or not _time(row.occurred_at)
        <= _time(row.recorded_at)
        <= _database_now(session)
        or not isinstance(row.evidence_ciphertext, str)
        or not 1 <= len(row.evidence_ciphertext.encode()) <= 4096
    ):
        raise SMSSuppressionUnavailable()
    plaintext = decrypt_value(row.evidence_ciphertext)
    if len(plaintext.encode()) > 2048:
        raise SMSSuppressionUnavailable()
    payload = json.loads(plaintext, object_pairs_hook=_closed_object)
    if _json(payload) != _json(_manifest(row)) or not hmac.compare_digest(
        row.payload_hmac,
        pseudonymous_digest(_json(payload), context="visit-prep-sms-stop-evidence-v1"),
    ):
        raise SMSSuppressionUnavailable()
    audits = session.exec(
        select(AuthorityAuditEvent)
        .where(
            AuthorityAuditEvent.resource_type == "visit_prep_sms_stop",
            AuthorityAuditEvent.resource_ref == str(row.id),
        )
        .limit(2)
    ).all()
    if len(audits) != 1:
        raise SMSSuppressionUnavailable()
    audit = audits[0]
    if (
        audit.event_source != "platform_authoritative"
        or audit.event_type != "visit_prep.sms_stopped"
        or audit.outcome != "suppressed"
        or audit.request_digest != row.payload_hmac
        or audit.route != "visit_prep_sms_stop_adapter"
        or audit.intent != "sender_wide_sms_stop"
        or audit.platform_refs != []
        or audit.occurred_at != row.recorded_at
        or not row.recorded_at <= audit.recorded_at <= _database_now(session)
        or any(
            value is not None
            for value in (
                audit.actor_id,
                audit.tenant_id,
                audit.person_id,
                audit.interaction_id,
                audit.workload_client_id,
                audit.client_event_id,
                audit.content_sha256,
            )
        )
    ):
        raise SMSSuppressionUnavailable()


def read_locked_sms_suppression(
    session: Session, *, sender_digest: str, recipient_digest: str
) -> bool:
    """Keep this same transaction open and recheck before any future SMS effect.

    False is only absence of STOP, never permission to send. Consent, standing
    approval, current lineage, quiet hours, cadence and transport gates are separate.
    Not connected to a sender yet; errors/lock contention must deny dispatch.
    """
    _lock_recipient(session, sender_digest, recipient_digest)
    row = session.exec(
        select(VisitPrepSMSStopEvent)
        .where(
            VisitPrepSMSStopEvent.sender_digest == sender_digest,
            VisitPrepSMSStopEvent.recipient_digest == recipient_digest,
        )
        .order_by(
            col(VisitPrepSMSStopEvent.recorded_at).desc(),
            col(VisitPrepSMSStopEvent.id).desc(),
        )
        .limit(1)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        return False
    _verify(session, row)
    return True


def record_verified_sms_stop(
    *, event: NormalizedSMSStop, source_verifier: SMSStopSourceVerifier | None = None
) -> SMSStopReceipt:
    """Owned atomic receipt/audit transaction; STOP works even when sending is off.

    No enabled verifier is installed. Synthetic adapters qualify the domain only;
    actual provider callback authentication and retry wiring remain required.
    """
    try:
        if (
            type(event) is not NormalizedSMSStop
            or not callable(source_verifier)
            or any(
                not _digest(value)
                for value in (
                    event.sender_digest,
                    event.recipient_digest,
                    event.source_event_digest,
                    event.source_evidence_hmac,
                )
            )
        ):
            raise SMSSuppressionUnavailable()
        occurred = _time(event.occurred_at)
        with Session(get_engine()) as session:
            _lock_recipient(session, event.sender_digest, event.recipient_digest)
            transaction, nested = (
                session.get_transaction(),
                session.get_nested_transaction(),
            )
            now = _database_now(session)
            if (
                occurred > now
                or source_verifier(session, event, checked_at=now) is not True
                or session.get_transaction() is not transaction
                or session.get_nested_transaction() is not nested
                or transaction is None
                or not transaction.is_active
            ):
                raise SMSSuppressionUnavailable()
            prior = session.exec(
                select(VisitPrepSMSStopEvent)
                .where(
                    VisitPrepSMSStopEvent.sender_digest == event.sender_digest,
                    VisitPrepSMSStopEvent.source_event_digest
                    == event.source_event_digest,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ).one_or_none()
            if prior is not None:
                _verify(session, prior)
                if (
                    prior.recipient_digest != event.recipient_digest
                    or prior.occurred_at != occurred
                    or prior.source_evidence_hmac != event.source_evidence_hmac
                ):
                    raise SMSSuppressionUnavailable()
                row, disposition = prior, "replayed"
            else:
                row = VisitPrepSMSStopEvent(
                    sender_digest=event.sender_digest,
                    recipient_digest=event.recipient_digest,
                    source_event_digest=event.source_event_digest,
                    source_evidence_hmac=event.source_evidence_hmac,
                    occurred_at=occurred,
                    recorded_at=_database_now(session),
                    evidence_ciphertext="",
                    payload_hmac="0" * 64,
                )
                row.id = cast(TypeIDType, TypeID(prefix="visit_prep_sms_stop"))
                plaintext = _json(_manifest(row))
                row.evidence_ciphertext = encrypt_value(plaintext)
                row.payload_hmac = pseudonymous_digest(
                    plaintext, context="visit-prep-sms-stop-evidence-v1"
                )
                session.add(row)
                session.flush()
                session.refresh(row)
                session.add(
                    AuthorityAuditEvent(
                        event_type="visit_prep.sms_stopped",
                        outcome="suppressed",
                        occurred_at=row.recorded_at,
                        recorded_at=_database_now(session),
                        resource_type="visit_prep_sms_stop",
                        resource_ref=str(row.id),
                        request_digest=row.payload_hmac,
                        route="visit_prep_sms_stop_adapter",
                        intent="sender_wide_sms_stop",
                    )
                )
                session.flush()
                _verify(session, row)
                disposition = "recorded"
            now = _database_now(session)
            if (
                source_verifier(session, event, checked_at=now) is not True
                or session.get_transaction() is not transaction
                or session.get_nested_transaction() is not nested
                or transaction is None
                or not transaction.is_active
            ):
                raise SMSSuppressionUnavailable()
            receipt = SMSStopReceipt(
                str(row.id),
                row.recorded_at,
                cast(Literal["recorded", "replayed"], disposition),
            )
            session.commit()
            return receipt
    except Exception:
        # No provider, SQL, contact or source proof details leave this boundary.
        raise SMSSuppressionUnavailable() from None
