"""Opt-in expiry metadata for the existing retained conversation projection.

Deadlines come from the same persisted receipts the retained reader verifies.
This read creates no receipt and does not renew history or source authority.
The older closed projections and their services remain unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from pydantic import AwareDatetime, Field, field_validator, model_validator

import app.services.authority as authority
import app.services.authority_conversation as conversation
import app.services.authority_conversation_display as display
import app.services.authority_conversation_read as history
from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.services import reviewed_excerpt_citations as citations
from app.services.provider_database_deadline import provider_database_deadline

from app.models.authority import AuthorityIdempotencyRecord, MountContextSession
from app.models.authority_conversation import CanonicalConversationTurn as Turn
from app.models.authority_conversation import CanonicalConversationUserPhase as User
from app.models.provider_record_authority import ProviderRecordAuthority
from sqlmodel import Session


class TimedConversationReadRequest(display.ConversationDisplaySelection):
    schema_version: Literal["1.3.0"]


class TimedDisplayMessage(display.RetainedDisplayMessage):
    expires_at: AwareDatetime | None

    @field_validator("expires_at")
    @classmethod
    def utc_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("Expiry must be UTC")
        return value

    @model_validator(mode="after")
    def expiry_role(self):
        if (self.role == "user") != (self.expires_at is None):
            raise ValueError("Invalid timed message expiry")
        return self


class TimedProjection(history.Closed):
    thread_ref: history.OpaqueRef
    messages: list[TimedDisplayMessage] = Field(max_length=12)
    next_cursor: None


class TimedConversationReadResponse(history.Closed):
    schema_version: Literal["1.3.0"] = "1.3.0"
    observed_at: AwareDatetime
    history: TimedProjection
    turns: list[history.TurnStatus] = Field(max_length=6)

    @model_validator(mode="after")
    def observed_deadlines(self):
        if self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("Observation must be UTC")
        for message in self.history.messages:
            if history._utc(message.recorded_at) > self.observed_at or (
                message.expires_at is not None
                and message.expires_at <= self.observed_at
            ):
                raise ValueError("Timed message is unavailable")
        return self


def _turn_deadlines(session: Session, row, now: datetime) -> list[datetime]:
    """Collect only the phase/context receipts already validated by display."""
    turn, user, final = row
    display._accepted_user(session, row, now)
    display._final_payload(session, turn, final, now)
    deadlines = [
        history._utc(
            display._phase_receipt(session, turn, phase, operation, now).retain_until
        )
        for phase, operation in (
            (user, conversation.USER_ACCEPTED_OPERATION),
            (final, conversation.FINAL_RESPONSE_OPERATION),
        )
    ]
    for operation in (history.prep.OPERATION, history.OPERATION):
        receipt = history._receipt(session, turn, operation)
        if receipt is not None:
            # _final_payload checked its live status, turn reference and the
            # immutable final digest including the exact context reference.
            deadlines.append(history._utc(receipt.retain_until))
    return deadlines


def _verified_deadline(
    session: Session,
    row,
    workload: str,
    mount: MountContextSession,
    scope: ProviderRecordAuthority,
    now: datetime,
    settings: AuthorityKernelSettings,
) -> datetime:
    """Use the exact retained witness, never a new history/source selection.

    Retained validation already verifies each witness member's phase/context
    receipts and its inherited provider source. History receipt expiry also
    preserves the original transitive source deadline. Include all those
    existing deadlines, including earlier user/final phases, without traversing
    a new dependency graph or deriving a duration from recorded_at.
    """
    turn, _, final = row
    deadlines = _turn_deadlines(session, row, now)
    if final.reviewed_excerpt_bindings is None:
        dependency = history._verified(
            session, row, workload, mount, scope, now, settings
        )
        if dependency is not None:
            deadlines.append(history._utc(dependency[0].retain_until))
    else:
        bindings = citations.CitationBindings.model_validate(
            citations.canonical_bindings(final.reviewed_excerpt_bindings)
        )
        witness = bindings.history_witness
        if witness is not None:
            for member in witness.members:
                previous = session.exec(
                    history._query(workload, mount, scope).where(
                        Turn.id == member.turn_ref
                    )
                ).one_or_none()
                if previous is None or previous[2] is None:
                    raise history.ConversationContextChanged
                deadlines.extend(_turn_deadlines(session, previous, now))
            if witness.dependency_ref is not None:
                inherited = session.get(
                    AuthorityIdempotencyRecord, witness.dependency_ref
                )
                if (
                    inherited is None
                    or not history._live(inherited, now)
                    or inherited.operation != history.prep.OPERATION
                    or inherited.workload_client_id != workload
                ):
                    raise history.ConversationContextChanged
                source = session.exec(
                    history._query(workload, mount, scope).where(
                        Turn.interaction_id == inherited.interaction_id
                    )
                ).one_or_none()
                if source is None or source[2] is None:
                    raise history.ConversationContextChanged
                deadlines.extend(_turn_deadlines(session, source, now))
                deadlines.append(history._utc(inherited.retain_until))
    deadline = min(deadlines)
    if deadline <= now:
        raise history.ConversationContextChanged
    return deadline


@provider_database_deadline
def read_timed_conversation(
    *,
    session: Session,
    workload_client_id: str,
    mount_context_token: str,
    request: TimedConversationReadRequest,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> dict[str, Any]:
    context, mount = authority.authorize_mount_read(
        session=session, mount_context_token=mount_context_token, settings=settings
    )
    scope = history._scope(session, context, mount)
    query = history._query(workload_client_id, mount, scope)
    if request.client_turn_id is not None:
        query = query.where(Turn.client_turn_id == request.client_turn_id)
    rows = list(
        session.exec(
            query.order_by(
                cast(Any, User.recorded_at).desc(), cast(Any, Turn.id).desc()
            ).limit(6)
        ).all()
    )
    messages, statuses = [], []
    now = datetime.now(UTC)
    denied = (
        history.ConversationContextChanged,
        history.prep.ProviderContextChanged,
        conversation.ConversationInvariantError,
        citations.CitationDenied,
        ValueError,
        IndexError,
    )
    for row in reversed(rows):
        turn, user, final = row
        try:
            display._original_lineage(session, turn, mount, scope, settings)
            display._accepted_user(session, row, now)
            if history._utc(user.recorded_at) > now:
                raise history.ConversationContextChanged
        except denied:
            continue
        common = {"turn_ref": str(turn.id), "client_turn_id": str(turn.client_turn_id)}
        messages.append(
            {
                **common,
                "role": "user",
                "content": user.content,
                "recorded_at": history._utc(user.recorded_at).isoformat(),
                "presentation": None,
                "reviewed_citations": None,
                "display_state": None,
                "expires_at": None,
            }
        )
        state, retained = ("pending", None) if final is None else ("stale", None)
        expiry = None
        if final is not None:
            try:
                retained = display._retained_final(
                    session, row, workload_client_id, mount, scope, now, settings
                )
                expiry = _verified_deadline(
                    session, row, workload_client_id, mount, scope, now, settings
                )
                if history._utc(final.recorded_at) > now:
                    raise history.ConversationContextChanged
                state = "completed"
            except history.SessionPrepSourceBusy:
                raise history.ConversationContextChanged from None
            except denied:
                retained, expiry = None, None
        if retained is not None and expiry is not None:
            messages.append(
                {
                    **common,
                    "role": "assistant",
                    "content": final.content,
                    "recorded_at": history._utc(final.recorded_at).isoformat(),
                    "presentation": history._presentation(final),
                    "reviewed_citations": citations.display_citations(
                        str(final.id), final.reviewed_excerpt_bindings
                    ),
                    "display_state": retained,
                    "expires_at": expiry.isoformat(),
                }
            )
        statuses.append(
            {
                **common,
                "state": state,
                "outcome": final.outcome if state == "completed" else None,
                "recorded_at": history._utc(
                    final.recorded_at if state == "completed" else user.recorded_at
                ).isoformat(),
            }
        )
    if request.mode == "turn" and not statuses:
        raise history.ConversationNotFound
    display._trim(messages, statuses)
    result = TimedConversationReadResponse.model_validate(
        {
            "schema_version": "1.3.0",
            "observed_at": now,
            "history": {
                "thread_ref": str(scope.binding_id),
                "messages": messages,
                "next_cursor": None,
            },
            "turns": statuses,
        }
    ).model_dump(mode="json")
    session.commit()
    return result
