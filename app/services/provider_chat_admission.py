"""Distributed admission from canonical metadata, never from browser identity."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from app.configuration.service_auth import SOVEREIGN_TOOL_AUTH_SETTINGS
from app.services import authority

from activemodel.types import TypeIDType
from app.models.authority import MountContextSession, MountContextToken
from app.models.authority_conversation import (
    CanonicalConversationFinalPhase,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from sqlalchemy import func
from sqlmodel import Session, col, select

BURST_LIMIT = 10
BURST_WINDOW = timedelta(seconds=60)
INFLIGHT_LIMIT = 2
RESERVATION_WINDOW = timedelta(seconds=195)
_LOCK_NAMESPACE = "session-prep-provider-chat-admission-v1"


class ProviderChatAdmissionLimited(Exception):
    """Fixed metadata only; the caller must validate authority before raising."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = max(1, min(60, retry_after_seconds))
        super().__init__("provider_chat_admission_limited")


class ProviderChatAdmissionUnavailable(Exception):
    pass


class ProviderChatWindowExpired(Exception):
    pass


@dataclass(frozen=True)
class AdmissionAttempt:
    actor_id: TypeIDType | None
    obtained: bool


def _provider_workload(workload_client_id: str) -> bool:
    return workload_client_id in SOVEREIGN_TOOL_AUTH_SETTINGS.allowed_client_ids


def begin_admission(
    session: Session,
    *,
    workload_client_id: str,
    mount_context_token: str,
    payload: dict[str, Any],
) -> AdmissionAttempt | None:
    """Try a namespaced lock BEFORE authority row locks, without authorizing.

    The stored actor merely chooses a serialization key. Full current authority
    and an exact actor match remain mandatory before any quota decision. A busy
    attempt never waits while retaining Doctor/token/lineage locks, and cannot
    replace a revoked-token denial or an existing canonical replay.
    """
    if not (
        _provider_workload(workload_client_id)
        and payload.get("phase") == "user_accepted"
        and payload.get("ingress_kind") == "chat_text"
    ):
        return None
    actor_id = session.exec(
        select(col(MountContextSession.actor_id))
        .join(
            MountContextToken,
            col(MountContextToken.session_id) == MountContextSession.id,
        )
        .where(
            MountContextToken.token_hash == authority._token_hash(mount_context_token),
            MountContextSession.surface_ceiling == "provider_assistant",
            MountContextSession.auth_tier_ceiling == "provider",
        )
    ).one_or_none()
    if actor_id is None:
        return AdmissionAttempt(None, False)
    material = f"{_LOCK_NAMESPACE}:{actor_id}:{workload_client_id}".encode("ascii")
    lock_key = int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=True)
    obtained = session.exec(select(func.pg_try_advisory_xact_lock(lock_key))).one()
    return AdmissionAttempt(actor_id, bool(obtained))


def _database_now(session: Session) -> datetime:
    # PostgreSQL now() is transaction-start time and can predate a lock wait.
    if session.connection().get_isolation_level() != "READ COMMITTED":
        raise ProviderChatAdmissionUnavailable("provider_chat_isolation_unavailable")
    return session.exec(select(func.clock_timestamp())).one()


def admit_new_chat(
    session: Session,
    *,
    attempt: AdmissionAttempt,
    authorized: authority.AuthorizedInteraction,
    workload_client_id: str,
) -> datetime:
    """Count and append under one transaction; an exact replay never comes here."""
    if (
        attempt.actor_id is None
        or attempt.actor_id != authorized.mount_session.actor_id
        or authorized.context.surface != "provider_assistant"
        or authorized.context.auth_tier != "provider"
        or authorized.lineage.workload_client_id != workload_client_id
    ):
        raise ProviderChatAdmissionUnavailable("provider_chat_binding_unavailable")
    if not attempt.obtained:
        raise ProviderChatAdmissionLimited(1)
    now = _database_now(session)
    scope = (
        CanonicalConversationTurn.actor_id == attempt.actor_id,
        CanonicalConversationTurn.workload_client_id == workload_client_id,
        CanonicalConversationUserPhase.ingress_kind == "chat_text",
    )
    recent = session.exec(
        select(CanonicalConversationUserPhase.recorded_at)
        .join(CanonicalConversationTurn)
        .where(*scope, CanonicalConversationUserPhase.recorded_at > now - BURST_WINDOW)
        .order_by(cast(Any, CanonicalConversationUserPhase.recorded_at).desc())
        .limit(BURST_LIMIT)
    ).all()
    if len(recent) >= BURST_LIMIT:
        raise ProviderChatAdmissionLimited(
            math.ceil((recent[-1] + BURST_WINDOW - now).total_seconds())
        )
    unfinished = session.exec(
        select(CanonicalConversationUserPhase.recorded_at)
        .join(CanonicalConversationTurn)
        .outerjoin(
            CanonicalConversationFinalPhase,
            col(CanonicalConversationFinalPhase.turn_id)
            == CanonicalConversationTurn.id,
        )
        .where(
            *scope,
            CanonicalConversationUserPhase.recorded_at > now - RESERVATION_WINDOW,
            cast(Any, CanonicalConversationFinalPhase.id).is_(None),
        )
        .limit(INFLIGHT_LIMIT)
    ).all()
    if len(unfinished) >= INFLIGHT_LIMIT:
        raise ProviderChatAdmissionLimited(5)
    return now


def require_final_window(
    session: Session, *, workload_client_id: str, interaction_id: TypeIDType
) -> None:
    """Reject new late finals only; exact stored-final replay stays unchanged."""
    if not _provider_workload(workload_client_id):
        return
    accepted_at = session.exec(
        select(CanonicalConversationUserPhase.recorded_at)
        .join(CanonicalConversationTurn)
        .where(
            CanonicalConversationTurn.interaction_id == interaction_id,
            CanonicalConversationTurn.workload_client_id == workload_client_id,
            CanonicalConversationUserPhase.ingress_kind == "chat_text",
        )
    ).one_or_none()
    if (
        accepted_at is not None
        and _database_now(session) >= accepted_at + RESERVATION_WINDOW
    ):
        raise ProviderChatWindowExpired("provider_chat_window_expired")
