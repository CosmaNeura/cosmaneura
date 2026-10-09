"""Current provider action and source checks, with no outgoing service call."""

import json
from datetime import datetime, timedelta
from uuid import UUID

from app.configuration.clerk import VerifiedProviderSession
from app.services import billing_header_operations as preparation
from app.services.billing_header_contract import ConfirmedBillingVisit
from app.services.cab_bridge_contract import (
    digest,
    minimized_operation,
    workload_request,
)

from activemodel.session_manager import get_session
from app.models.billing_header_action import BillingHeaderAction as Action
from app.models.billing_header_generation_continuation import (
    BillingHeaderGenerationContinuation as Continuation,
)
from app.models.billing_header_operation import (
    BillingHeaderPreparedOperation as Operation,
)
from sqlmodel import col, func, select

require = preparation._require


def _now(session):
    return session.exec(select(func.clock_timestamp())).one()


def _current(session, actor, operation_id):
    row = session.exec(
        select(Operation).where(Operation.operation_id == operation_id)
    ).one_or_none()
    row = preparation._present(row)
    require(
        row is not None
        and row.actor.get("issuer") == actor.issuer
        and row.actor.get("subject") == actor.subject
    )
    scope = preparation._scope(session, actor, row.note_id)
    latest = session.exec(
        select(func.max(Operation.source_revision)).where(
            Operation.appointment_id == row.appointment_id,
            Operation.schema_ref == row.schema_ref,
        )
    ).one()
    require(row.source_revision == latest)
    require(
        row.practice_id == scope[1].id
        and row.patient_id == scope[3].id
        and row.source == scope[5].model_dump()
        and row.actor.get("confirmedComplete") is True
    )
    require(
        row.ordered_content == json.loads(scope[6]) and row.projected_text == scope[7]
    )
    ConfirmedBillingVisit.model_validate(row.visit)
    admission = preparation._present(preparation._admission(scope, actor))
    keys = ("admissionId", "dataClass", "reviewRef", "contentSha256")
    require(all(admission.model_dump()[k] == row.admission[k] for k in keys))
    now = _now(session)
    require(now < datetime.fromisoformat(admission.expiresAt.replace("Z", "+00:00")))
    require(actor.expires_at is None or now < actor.expires_at)
    return row, now, admission


def begin_action(actor, operation_id, purpose, command):
    """Only the current-provider BFF calls this; there is no registration endpoint."""
    with get_session() as session:
        row, now, admission = _current(session, actor, operation_id)
        operation = minimized_operation(row)
        payload = workload_request(operation, purpose, command)
        action = Action(
            operation_id=row.operation_id,
            purpose=purpose,
            request_sha256=digest(payload),
            operation_sha256=digest(operation),
            actor={
                "issuer": actor.issuer,
                "subject": actor.subject,
                "sessionSha256": actor.session_sha256,
            },
            created_at=now,
            expires_at=min(
                now + timedelta(seconds=120),
                actor.expires_at or now + timedelta(seconds=120),
                datetime.fromisoformat(admission.expiresAt.replace("Z", "+00:00")),
            ),
        )
        session.add(action)
        session.flush()
        result = (payload, str(action.id))
        session.commit()  # Release Core source locks before the CAB callback.
        return result


def begin_async_action(actor, operation_id, command):
    """Claim the exact Generate intent before HTTP; retry only reads its status."""
    with get_session() as session:
        row, now, admission = _current(session, actor, operation_id)
        operation = minimized_operation(row)
        payload = workload_request(operation, "header.generate", command)
        generation_id = UUID(command["operationId"])
        # _current holds the source/scope locks, serializing the first local claim.
        action = session.exec(
            select(Action).where(
                Action.operation_id == row.operation_id,
                Action.generation_id == generation_id,
            )
        ).one_or_none()
        if action is not None:
            require(
                action.request_sha256 == digest(payload)
                and action.operation_sha256 == digest(operation)
                and action.actor.get("issuer") == actor.issuer
                and action.actor.get("subject") == actor.subject
            )
            require(
                not action.continuation_consent
                or action.actor.get("sessionSha256") == actor.session_sha256
            )
            session.commit()
            return payload, str(action.id), True
        provider_expiry = preparation._present(actor.expires_at)
        require(provider_expiry > now)
        consent = command.get("continuationConsent") is True
        expiry = min(
            now + timedelta(seconds=360),
            datetime.fromisoformat(admission.expiresAt.replace("Z", "+00:00")),
        )
        if not consent:
            expiry = min(expiry, provider_expiry)
        action = Action(
            operation_id=row.operation_id,
            purpose="header.generate",
            generation_id=generation_id,
            request_sha256=digest(payload),
            operation_sha256=digest(operation),
            actor={
                "issuer": actor.issuer,
                "subject": actor.subject,
                "sessionSha256": actor.session_sha256,
            },
            created_at=now,
            expires_at=expiry,
            continuation_consent=consent,
            initial_authorized_until=min(expiry, provider_expiry) if consent else None,
        )
        session.add(action)
        session.flush()
        result = payload, str(action.id), False
        session.commit()
        return result


def complete_generation(operation_id, request):
    """A trusted, exact terminal receipt closes authority; it never grants any."""
    with get_session() as session:
        action = session.exec(
            select(Action)
            .where(
                Action.operation_id == operation_id,
                Action.purpose == "header.generate",
                col(Action.generation_id).is_not(None),
                Action.request_sha256 == request.requestSha256,
            )
            .with_for_update()
        ).one_or_none()
        row = session.exec(
            select(Operation).where(
                Operation.operation_id == operation_id,
            )
        ).one_or_none()
        require(
            action is not None
            and row is not None
            and action.actor.get("issuer") == request.actor.issuer
            and action.actor.get("subject") == request.actor.subject
            and action.operation_sha256 == digest(minimized_operation(row))
            and row.source["contentSha256"] == request.sourceContentSha256
            and row.source["textSha256"] == request.sourceTextSha256
        )
        action = preparation._present(action)
        if action.finished_at is not None:
            require(action.completion_status == request.status)
        else:
            action.finished_at = _now(session)
            action.completion_status = request.status
            session.add(action)
            session.commit()
        return {
            "schemaVersion": "core-cab-generation-completion-receipt.v1",
            "operationId": str(operation_id),
            "requestSha256": request.requestSha256,
            "status": request.status,
            "closed": True,
        }


def finish_action(action_id):
    with get_session() as session:
        action = session.exec(
            select(Action).where(Action.id == action_id).with_for_update()
        ).one_or_none()
        if action is not None and action.finished_at is None:
            action.finished_at = _now(session)
            session.add(action)
            session.commit()


def authorize_action(operation_id, request):
    with get_session() as session:
        now = _now(session)
        action = session.exec(
            select(Action)
            .where(
                Action.operation_id == operation_id,
                Action.purpose == request.purpose,
                Action.request_sha256 == request.requestSha256,
                col(Action.finished_at).is_(None),
                Action.created_at <= now,
                Action.expires_at > now,
            )
            .order_by(col(Action.created_at).desc())
            .limit(1)
        ).first()
        action = preparation._present(action)
        require(
            action is not None
            and action.actor.get("issuer") == request.actor.issuer
            and action.actor.get("subject") == request.actor.subject
        )
        actor = VerifiedProviderSession(
            request.actor.issuer, request.actor.subject, action.actor["sessionSha256"]
        )
        row, now, admission = _current(session, actor, operation_id)
        # Practice/source locks precede the action lock, including the BFF finish path.
        action = session.exec(
            select(Action)
            .where(Action.id == action.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        now = _now(session)
        require(action.finished_at is None)
        authorized_until = _authorized_until(session, action)
        require(now < authorized_until)
        operation = minimized_operation(row)
        require(
            action.operation_sha256 == digest(operation)
            and now < action.expires_at
            and request.sourceContentSha256 == row.source["contentSha256"]
            and request.sourceTextSha256 == row.source["textSha256"]
        )
        result = {
            "schemaVersion": "core-cab-authorization.v1",
            "allowed": True,
            "operationId": str(row.operation_id),
            "purpose": request.purpose,
            "requestSha256": request.requestSha256,
            "operationSha256": digest(operation),
            "practiceId": str(row.practice_id),
            "actor": {k: operation["actor"][k] for k in ("issuer", "subject", "role")},
            "source": operation["source"],
            "admission": operation["admission"],
            "authorizedAt": now.isoformat(),
            "expiresAt": min(
                now + timedelta(seconds=30),
                action.expires_at,
                authorized_until,
                datetime.fromisoformat(admission.expiresAt.replace("Z", "+00:00")),
            ).isoformat(),
        }

        if action.generation_id is not None:
            result["generationExpiresAt"] = action.expires_at.isoformat()
            if action.continuation_consent:
                result["generationContinuationRequired"] = True
                result["generationAuthorizedUntil"] = authorized_until.isoformat()
        return result


def _authorized_until(session, action):
    if not action.continuation_consent:
        return action.expires_at
    latest = session.exec(
        select(func.max(Continuation.authorized_until)).where(
            Continuation.action_id == action.id
        )
    ).one()
    return min(
        action.expires_at,
        latest or preparation._present(action.initial_authorized_until),
    )


def _continuation_receipt(session, action):
    return {
        "schemaVersion": "core-cab-generation-continuation.v1",
        "purpose": "header.generation-continue",
        "sourceOperationId": str(action.operation_id),
        "operationId": str(action.generation_id),
        "requestSha256": action.request_sha256,
        "generationExpiresAt": action.expires_at.isoformat(),
        "generationAuthorizedUntil": _authorized_until(session, action).isoformat(),
    }


def continuation_receipt(action_id):
    with get_session() as session:
        action = preparation._present(session.get(Action, action_id))
        require(action.continuation_consent)
        return _continuation_receipt(session, action)


def continue_generation(actor, operation_id, command):
    """An explicit authenticated action appends evidence; status never calls this."""
    with get_session() as session:
        row, now, admission = _current(session, actor, operation_id)
        generate = {
            k: command[k] for k in ("encounterId", "expectedSource", "operationId")
        }
        generate.update(responseMode="async", continuationConsent=True)
        original = workload_request(
            minimized_operation(row), "header.generate", generate
        )
        require(digest(original) == command["requestSha256"])
        action = session.exec(
            select(Action)
            .where(
                Action.operation_id == operation_id,
                Action.generation_id == UUID(command["operationId"]),
                Action.request_sha256 == command["requestSha256"],
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        action = preparation._present(action)
        now = _now(session)
        authorized_until = _authorized_until(session, action)
        require(
            action.continuation_consent
            and action.finished_at is None
            and action.actor
            == {
                "issuer": actor.issuer,
                "subject": actor.subject,
                "sessionSha256": actor.session_sha256,
            }
            and now < min(action.expires_at, authorized_until)
        )
        verified_expiry = preparation._present(actor.expires_at)
        expiry = min(
            verified_expiry,
            action.expires_at,
            datetime.fromisoformat(admission.expiresAt.replace("Z", "+00:00")),
        )
        require(now < expiry)
        # A cached/equal token is a read of the current receipt, not new evidence.
        if expiry > authorized_until:
            count = session.exec(
                select(func.count())
                .select_from(Continuation)
                .where(Continuation.action_id == action.id)
            ).one()
            require(count < 48)
            session.add(
                Continuation(
                    action_id=action.id,
                    request_sha256=action.request_sha256,
                    actor=dict(action.actor),
                    created_at=now,
                    authorized_until=expiry,
                )
            )
            session.flush()
        result = _continuation_receipt(session, action)
        session.commit()
        return result
