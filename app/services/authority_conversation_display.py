"""Display-only accepted conversation snapshots under current record authority.

This service never supplies inference memory, upgrades a prior read receipt or
substitutes a current note for the source of an accepted answer. Retained
display and current-source conversation reads are separate contracts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import Field, model_validator

import app.services.authority as authority
import app.services.authority_conversation as conversation
import app.services.authority_conversation_read as history
from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.services import reviewed_excerpt_citations as citations
from app.services.provider_database_deadline import provider_database_deadline
from app.services.provider_record_authority import validate_provider_record_scope
from app.services.session_prep_note_sources import _prior_patient_scope

from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.authority import (
    AuthorityIdempotencyRecord,
    AuthorityPersonBinding,
    InteractionLineage,
    MountContextSession,
)
from app.models.authority_conversation import CanonicalConversationFinalPhase as Final
from app.models.authority_conversation import CanonicalConversationTurn as Turn
from app.models.authority_conversation import CanonicalConversationUserPhase as User
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.provider_record_authority import ProviderRecordAuthority
from app.models.scribe_patient_identity import ScribePatientIdentity
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, col, select


class ConversationDisplaySelection(history.Closed):
    mode: Literal["recent", "turn"]
    client_turn_id: UUID | None

    @model_validator(mode="after")
    def selection(self):
        if (self.mode == "turn") != (self.client_turn_id is not None) or (
            self.client_turn_id is not None and self.client_turn_id.version != 4
        ):
            raise ValueError("Invalid turn selection")
        return self


class RetainedConversationReadRequest(ConversationDisplaySelection):
    schema_version: Literal["1.2.0"]


class RetainedDisplayMessage(history.DisplayMessage):
    reviewed_citations: list[citations.DisplayCitation] | None = Field(max_length=64)
    display_state: Literal["historical", "superseded"] | None

    @model_validator(mode="after")
    def role_fields(self):
        if self.role == "user":
            if (
                self.reviewed_citations is not None
                or self.display_state is not None
                or self.presentation is not None
            ):
                raise ValueError("Invalid user display fields")
        elif (
            self.reviewed_citations is None
            or self.display_state is None
            or self.presentation is None
        ):
            raise ValueError("Missing accepted assistant display fields")
        elif self.reviewed_citations:
            prefix = self.reviewed_citations[0].citation_ref.rsplit(".", 1)[0]
            if [item.ordinal for item in self.reviewed_citations] != list(
                range(1, len(self.reviewed_citations) + 1)
            ) or any(
                item.citation_ref != f"{prefix}.{item.ordinal}"
                for item in self.reviewed_citations
            ):
                raise ValueError("Invalid original citation order")
        return self


class RetainedProjection(history.Closed):
    thread_ref: history.OpaqueRef
    messages: list[RetainedDisplayMessage] = Field(max_length=12)
    next_cursor: None


class RetainedConversationReadResponse(history.Closed):
    schema_version: Literal["1.2.0"] = "1.2.0"
    history: RetainedProjection
    turns: list[history.TurnStatus] = Field(max_length=6)


class ConversationCitationReadRequest(ConversationDisplaySelection):
    schema_version: Literal["1.1.0"]


class CitationDisplayMessage(history.DisplayMessage):
    reviewed_citations: list[citations.DisplayCitation] = Field(max_length=64)


class CitationProjection(history.Closed):
    thread_ref: history.OpaqueRef
    messages: list[CitationDisplayMessage] = Field(max_length=12)
    next_cursor: None


class ConversationCitationReadResponse(history.Closed):
    schema_version: Literal["1.1.0"]
    history: CitationProjection
    turns: list[history.TurnStatus] = Field(max_length=6)


def _phase_receipt(
    session: Session, turn: Turn, phase: User | Final, operation: str, now: datetime
) -> AuthorityIdempotencyRecord:
    receipt = history._receipt(session, turn, operation)
    if (
        receipt is None
        or not history._live(receipt, now)
        or receipt.logical_ref != str(turn.client_turn_id)
        or receipt.response_ref != str(turn.id)
        or receipt.request_digest != phase.request_digest
        or receipt.workload_client_id != turn.workload_client_id
        or receipt.interaction_id != turn.interaction_id
        or phase.turn_id != turn.id
    ):
        raise history.ConversationContextChanged
    return receipt


def _accepted_user(session: Session, row, now: datetime):
    turn, user, _ = row
    payload = conversation._canonical_payload(
        {
            "phase": "user_accepted",
            "ingress_kind": user.ingress_kind,
            "action_code": user.action_code,
            "message": {"content_type": user.content_type, "content": user.content},
        }
    )
    if (
        user.ingress_kind != "chat_text"
        or user.action_code is not None
        or user.request_digest != conversation._request_digest(payload)
        or history._utc(user.recorded_at) != history._utc(turn.created_at)
    ):
        raise history.ConversationContextChanged
    _phase_receipt(session, turn, user, conversation.USER_ACCEPTED_OPERATION, now)


def _original_lineage(
    session: Session,
    turn: Turn,
    mount: MountContextSession,
    scope: ProviderRecordAuthority,
    settings: AuthorityKernelSettings,
):
    lineage = session.get(
        InteractionLineage, turn.interaction_id, populate_existing=True
    )
    original = (
        session.get(MountContextSession, lineage.session_id, populate_existing=True)
        if lineage
        else None
    )
    if (
        lineage is None
        or original is None
        or turn.client_turn_id.version != 4
        or lineage.workload_client_id != turn.workload_client_id
        or lineage.client_turn_id != turn.client_turn_id
        or original.status != "active"
        or original.revoked_at is not None
        or original.actor_id != mount.actor_id
        or original.tenant_id != mount.tenant_id
        or original.person_binding_id != scope.binding_id
        or original.source_identity_id != mount.source_identity_id
    ):
        raise history.ConversationContextChanged
    # The original mount's expiry is not reused as a grant. The caller supplied
    # a fresh, locked token and this resolves the original identity's CURRENT
    # real Doctor, Patient, practice and record eligibility before showing data.
    context = authority._context_for_session(
        session, original, clerk_issuer=settings.clerk_issuer
    )
    if (
        history._scope(session, context, original).id != scope.id
        or lineage.binding_fingerprint != authority._binding_fingerprint(context)
        or turn.actor_id != original.actor_id
        or turn.tenant_id != original.tenant_id
        or turn.person_id != context.person_id
    ):
        raise history.ConversationContextChanged


def _trim(messages: list[dict[str, Any]], statuses: list[dict[str, Any]]):
    while (
        len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode())
        > history.MAX_MESSAGES_BYTES
    ):
        removed = statuses.pop(0)["turn_ref"]
        messages[:] = [
            message for message in messages if message["turn_ref"] != removed
        ]


def _final_payload(session: Session, turn: Turn, final: Final, now: datetime):
    _phase_receipt(session, turn, final, conversation.FINAL_RESPONSE_OPERATION, now)
    payload: dict[str, Any] = {
        "phase": "final_response",
        "outcome": final.outcome,
        "response": {"content_type": final.content_type, "content": final.content},
        "presentation": history._presentation(final),
    }
    bindings = final.reviewed_excerpt_bindings
    if bindings is not None:
        payload["reviewed_excerpts"] = citations.canonical_bindings(bindings)[
            "selection"
        ]
    for operation, key in (
        (history.prep.OPERATION, "provider_context_ref"),
        (history.OPERATION, "history_context_ref"),
    ):
        receipt = history._receipt(session, turn, operation)
        if receipt is not None:
            if not history._live(receipt, now) or receipt.logical_ref != str(turn.id):
                raise history.ConversationContextChanged
            payload[key] = str(receipt.id)
    if final.request_digest != conversation._request_digest(
        conversation._canonical_payload(payload),
        bindings,
    ):
        raise history.ConversationContextChanged
    return payload


def _retained_source_fence(
    session: Session, scope: ProviderRecordAuthority, bindings: Any
):
    """Fence exact retained source scope without reselecting current notes.

    Superseded note rows remain valid retained snapshot identities. Use the
    shared source fence's order and canonical-patient predicate, without
    its current source-window/selection checks. Patient SHARE locks prevent
    non-key doctor reassignment. No content/freshness decision is made here.
    """
    try:
        with session.begin_nested():
            refs = [item.note_ref for item in bindings.context_witness.sources]
            notes = session.exec(
                select(AIVisitNote)
                .where(col(AIVisitNote.id).in_(refs))
                .execution_options(populate_existing=True)
            ).all()
            if len(notes) != len(refs) or any(
                n.doctor_id != scope.doctor_id for n in notes
            ):
                raise history.ConversationContextChanged
            appointment_ids = {
                scope.appointment_id,
                *(note.appointment_id for note in notes),
            }
            appointments = session.exec(
                select(Appointment)
                .where(col(Appointment.id).in_(appointment_ids))
                .order_by(col(Appointment.id))
                .with_for_update(of=Appointment, nowait=True)
                .execution_options(populate_existing=True)
            ).all()
            if len(appointments) != len(appointment_ids) or any(
                a.doctor_id != scope.doctor_id for a in appointments
            ):
                raise history.ConversationContextChanged
            identities = session.exec(
                select(ScribePatientIdentity)
                .where(col(ScribePatientIdentity.appointment_id).in_(appointment_ids))
                .order_by(col(ScribePatientIdentity.appointment_id))
                .with_for_update(of=ScribePatientIdentity, nowait=True)
                .execution_options(populate_existing=True)
            ).all()
            patient_ids = {
                scope.patient_id,
                *(a.patient_id for a in appointments),
                *(
                    i.canonical_patient_id
                    for i in identities
                    if i.canonical_patient_id is not None
                    and i.state in {"matched", "confirmed"}
                ),
            }
            patients = session.exec(
                select(Patient)
                .where(col(Patient.id).in_(patient_ids))
                .order_by(col(Patient.id))
                .with_for_update(of=Patient, read=True, nowait=True)
                .execution_options(populate_existing=True)
            ).all()
            if len(patients) != len(patient_ids) or any(
                p.doctor_id != scope.doctor_id for p in patients
            ):
                raise history.ConversationContextChanged
            target = next(
                (a for a in appointments if a.id == scope.appointment_id), None
            )
            source_appointments = appointment_ids - {scope.appointment_id}
            if (
                target is None
                or target.patient_id != scope.patient_id
                or any(n.appointment_id == scope.appointment_id for n in notes)
            ):
                raise history.ConversationContextChanged
            eligible = session.exec(
                select(Appointment).where(
                    col(Appointment.id).in_(source_appointments),
                    _prior_patient_scope(session, scope.doctor_id, target),
                )
            ).all()
            if len(eligible) != len(source_appointments):
                raise history.ConversationContextChanged
            locked_notes = session.exec(
                select(AIVisitNote)
                .where(col(AIVisitNote.id).in_(refs))
                .order_by(col(AIVisitNote.id))
                .with_for_update(of=AIVisitNote, nowait=True)
                .execution_options(populate_existing=True)
            ).all()
            if len(locked_notes) != len(refs) or any(
                n.doctor_id != scope.doctor_id
                or n.appointment_id not in source_appointments
                for n in locked_notes
            ):
                raise history.ConversationContextChanged
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == "55P03":
            raise history.SessionPrepSourceBusy from None
        raise


def _history_witness(
    session: Session,
    turn: Turn,
    accepted: Final,
    bindings: Any,
    workload: str,
    mount: MountContextSession,
    scope: ProviderRecordAuthority,
    now: datetime,
    settings: AuthorityKernelSettings,
):
    receipt = history._receipt(session, turn, history.OPERATION)
    witness = bindings.history_witness
    if receipt is None and witness is None:
        return
    if receipt is None or witness is None:
        raise history.ConversationContextChanged
    cutoff, dependency = history._marker(receipt)
    if (
        not history._live(receipt, now)
        or receipt.logical_ref != str(turn.id)
        or witness.context_ref != str(receipt.id)
        or witness.response_ref != receipt.response_ref
        or witness.request_digest != receipt.request_digest
        or witness.cutoff != cutoff
        or witness.dependency_ref != dependency
        or cutoff > history._utc(accepted.recorded_at)
    ):
        raise history.ConversationContextChanged
    messages, members = [], []
    # Exact persisted membership only. Never select a new subset, recurse into
    # history tools or send this reconstructed retained text to inference.
    for member in witness.members:
        row = session.exec(
            history._query(workload, mount, scope).where(
                Turn.id == member.turn_ref,
            )
        ).one_or_none()
        if row is None or row[2] is None or row[0].id == turn.id:
            raise history.ConversationContextChanged
        previous, user, final = row
        _original_lineage(session, previous, mount, scope, settings)
        _accepted_user(session, row, now)
        _final_payload(session, previous, final, now)
        if (
            user.request_digest != member.user_digest
            or final.request_digest != member.final_digest
            or final.outcome != "completed"
            or final.presentation_mode != "standard"
            or history._utc(final.recorded_at) > cutoff
        ):
            raise history.ConversationContextChanged
        messages.extend(
            [
                history.MemoryMessage(role="user", content=user.content),
                history.MemoryMessage(role="assistant", content=final.content),
            ]
        )
        members.append(member.model_dump(mode="json"))
    result = history.HistoryResult(
        messages=messages,
        source_version_digest=witness.source_version_digest,
    )
    if (
        len(
            json.dumps(
                [message.model_dump(mode="json") for message in messages],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )
        > history.MAX_MESSAGES_BYTES
        or history._digest(str(turn.id), result, members) != receipt.request_digest
    ):
        raise history.ConversationContextChanged
    if dependency is not None:
        inherited = session.get(AuthorityIdempotencyRecord, dependency)
        if (
            inherited is None
            or not history._live(inherited, now)
            or inherited.operation != history.prep.OPERATION
            or inherited.workload_client_id != workload
        ):
            raise history.ConversationContextChanged
        source_row = session.exec(
            history._query(workload, mount, scope).where(
                Turn.interaction_id == inherited.interaction_id,
            )
        ).one_or_none()
        if source_row is None or source_row[2] is None:
            raise history.ConversationContextChanged
        source_turn, _, source_final = source_row
        _original_lineage(session, source_turn, mount, scope, settings)
        _accepted_user(session, source_row, now)
        _final_payload(session, source_turn, source_final, now)
        if source_final.reviewed_excerpt_bindings is not None:
            source_bindings = citations.CitationBindings.model_validate(
                citations.canonical_bindings(source_final.reviewed_excerpt_bindings),
            )
            if source_bindings.context_ref != dependency:
                raise history.ConversationContextChanged
            _retained_source_fence(session, scope, source_bindings)
            inherited_result = citations.retained_context_result(
                session=session,
                scope=scope,
                turn=source_turn,
                final=source_final,
                bindings=source_bindings,
                now=now,
            )
        else:
            # Legacy sources have no retained witness. They remain eligible
            # only through the unchanged CURRENT-source gate; never salvage.
            _, inherited_result = history._source(
                session,
                dependency,
                workload,
                mount,
                scope,
                now,
                settings,
            )
        if inherited_result.source_version_digest != witness.source_version_digest:
            raise history.ConversationContextChanged
    elif witness.source_version_digest is not None:
        raise history.ConversationContextChanged


def _retained_final(
    session: Session,
    row,
    workload: str,
    mount: MountContextSession,
    scope: ProviderRecordAuthority,
    now: datetime,
    settings: AuthorityKernelSettings,
) -> Literal["historical", "superseded"]:
    turn, user, final = row
    payload = _final_payload(session, turn, final, now)
    if history._utc(final.recorded_at) < history._utc(user.recorded_at):
        raise history.ConversationContextChanged
    if final.reviewed_excerpt_bindings is None:
        # Legacy answers retain the same freshness contract as before. A
        # missing retained source witness never makes stale content displayable.
        history._verified(session, row, workload, mount, scope, now, settings)
        return "historical"
    bindings = citations.CitationBindings.model_validate(
        citations.canonical_bindings(final.reviewed_excerpt_bindings),
    )
    if (
        final.outcome != "completed"
        or final.presentation_mode != "standard"
        or final.suppress_citations
        or payload.get("provider_context_ref") != bindings.context_ref
    ):
        raise history.ConversationContextChanged
    _retained_source_fence(session, scope, bindings)
    original = citations.retained_context_result(
        session=session,
        scope=scope,
        turn=turn,
        final=final,
        bindings=bindings,
        now=now,
    )
    _history_witness(
        session, turn, final, bindings, workload, mount, scope, now, settings
    )
    if (
        original.source_version_digest != bindings.source_version_digest
        or citations.enumeration_digest(original.reviewed_sources)
        != bindings.selection.enumeration_sha256
    ):
        raise history.ConversationContextChanged
    quotes = []
    for item in bindings.excerpts:
        source = original.reviewed_sources[item.source_index]
        title, quote = list(source.sections.items())[item.section_index]
        quotes.append((quote, source.source_at, title))
    if final.content != citations.render_quotes(
        quotes,
        mode=bindings.selection.mode,
        source_count=len(original.reviewed_sources),
        current_through=original.current_through,
    ):
        raise history.ConversationContextChanged
    try:
        history._verified(session, row, workload, mount, scope, now, settings)
    except (
        history.ConversationContextChanged,
        history.prep.ProviderContextChanged,
        conversation.ConversationInvariantError,
    ):
        return "superseded"
    return "historical"


@provider_database_deadline
def read_retained_conversation(
    *,
    session: Session,
    workload_client_id: str,
    mount_context_token: str,
    request: RetainedConversationReadRequest,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> dict[str, Any]:
    context, mount = authority.authorize_mount_read(
        session=session,
        mount_context_token=mount_context_token,
        settings=settings,
    )
    scope = history._scope(session, context, mount)
    query = history._query(workload_client_id, mount, scope)
    if request.client_turn_id is not None:
        query = query.where(Turn.client_turn_id == request.client_turn_id)
    rows = list(
        session.exec(
            query.order_by(
                cast(Any, User.recorded_at).desc(),
                cast(Any, Turn.id).desc(),
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
            _original_lineage(session, turn, mount, scope, settings)
            _accepted_user(session, row, now)
        except denied:
            continue  # Corrupt/unreceipted ingress never becomes visible history.
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
            }
        )
        state, display = ("pending", None) if final is None else ("stale", None)
        if final is not None:
            try:
                display = _retained_final(
                    session, row, workload_client_id, mount, scope, now, settings
                )
                state = "completed"
            except history.SessionPrepSourceBusy:
                raise history.ConversationContextChanged from None
            except denied:
                pass  # Never repair a missing/corrupt map with a current note.
        if display is not None:
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
                    "display_state": display,
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
    _trim(messages, statuses)
    result = RetainedConversationReadResponse.model_validate(
        {
            "schema_version": "1.2.0",
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


@provider_database_deadline
def read_current_with_citations(
    *,
    session: Session,
    workload_client_id: str,
    mount_context_token: str,
    request: ConversationCitationReadRequest,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> dict[str, Any]:
    """Preserve the unpublished 1.1 projection's CURRENT-source read gates."""
    context, mount = authority.authorize_mount_read(
        session=session,
        mount_context_token=mount_context_token,
        settings=settings,
    )
    scope = history._scope(session, context, mount)
    selected = history._select(
        session,
        workload_client_id,
        mount,
        scope,
        datetime.now(UTC),
        settings,
        turn_id=request.client_turn_id,
    )
    if request.mode == "turn" and not selected:
        raise history.ConversationNotFound
    messages, statuses = [], []
    for (turn, user, final), state, _ in selected:
        common = {"turn_ref": str(turn.id), "client_turn_id": str(turn.client_turn_id)}
        messages.append(
            {
                **common,
                "role": "user",
                "content": user.content,
                "recorded_at": history._utc(user.recorded_at).isoformat(),
                "presentation": None,
                "reviewed_citations": [],
            }
        )
        if state == "completed":
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
    _trim(messages, statuses)
    result = ConversationCitationReadResponse.model_validate(
        {
            "schema_version": "1.1.0",
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


def read_retained_citation(
    *,
    session: Session,
    doctor: Doctor,
    final_ref: Any,
    ordinal: int,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> dict[str, Any]:
    """Resolve one accepted ordinal for a CURRENT authenticated real provider.

    Browser authentication replaces neither record ownership nor receipt
    integrity. The expired original bearer is never used as a read grant.
    """
    if not settings.enabled or not settings.is_complete() or type(ordinal) is not int:
        raise citations.CitationDenied
    # Bound this authenticated browser read without inventing a workload
    # identity or changing an engine, pool or database-wide configuration.
    for name, value in (("lock_timeout", "1000ms"), ("statement_timeout", "3000ms")):
        session.execute(
            text(
                "SELECT set_config(:name, CASE WHEN "
                "current_setting(:name)::interval = interval '0' OR "
                "current_setting(:name)::interval > CAST(CAST(:value AS text) AS interval) "
                "THEN CAST(:value AS text) ELSE current_setting(:name) END, true)"
            ),
            {"name": name, "value": value},
        )
    final = session.get(Final, final_ref)
    turn = session.get(Turn, final.turn_id) if final else None
    lineage = session.get(InteractionLineage, turn.interaction_id) if turn else None
    mount = session.get(MountContextSession, lineage.session_id) if lineage else None
    binding = (
        session.get(AuthorityPersonBinding, mount.person_binding_id) if mount else None
    )
    if binding is None or turn is None or final is None or mount is None:
        raise citations.CitationDenied
    scope = validate_provider_record_scope(
        session=session,
        binding=binding,
        actor_id=turn.actor_id,
        clerk_subject=doctor.clerk_id,
    )
    if scope is None or scope.doctor_id != doctor.id:
        raise citations.CitationDenied
    row = session.exec(
        history._query(turn.workload_client_id, mount, scope).where(
            Final.id == final.id,
        )
    ).one_or_none()
    if row is None or final.reviewed_excerpt_bindings is None:
        raise citations.CitationDenied
    now = datetime.now(UTC)
    _original_lineage(session, turn, mount, scope, settings)
    _accepted_user(session, row, now)
    _retained_final(session, row, turn.workload_client_id, mount, scope, now, settings)
    bindings = citations.CitationBindings.model_validate(
        citations.canonical_bindings(final.reviewed_excerpt_bindings),
    )
    if not 1 <= ordinal <= len(bindings.excerpts):
        raise citations.CitationDenied
    original = citations.retained_context_result(
        session=session,
        scope=scope,
        turn=turn,
        final=final,
        bindings=bindings,
        now=now,
    )
    item = bindings.excerpts[ordinal - 1]
    source = original.reviewed_sources[item.source_index]
    return {
        "schema_version": "1.0.0",
        "citation": citations.display_citations(
            str(final.id), bindings.model_dump(mode="json")
        )[ordinal - 1],
        "content_sha256": item.content_sha256,
        "sections": [
            {"title": title, "text": text} for title, text in source.sections.items()
        ],
    }
