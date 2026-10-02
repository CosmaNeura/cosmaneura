"""Current provider action and source checks, with no outgoing service call."""

import json
from datetime import datetime, timedelta

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
from app.models.billing_header_operation import (
    BillingHeaderPreparedOperation as Operation,
)
from sqlmodel import func, select

require = preparation._require


def _now(session):
    return session.exec(select(func.clock_timestamp())).one()


def _current(session, actor, operation_id):
    row = session.exec(
        select(Operation).where(Operation.operation_id == operation_id)
    ).one_or_none()
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
    admission = preparation._admission(scope, actor)
    require(admission is not None)
    keys = ("admissionId", "dataClass", "reviewRef", "contentSha256")
    require(all(admission.model_dump()[k] == row.admission[k] for k in keys))
    now = _now(session)
    require(now < datetime.fromisoformat(admission.expiresAt.replace("Z", "+00:00")))
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
                datetime.fromisoformat(admission.expiresAt.replace("Z", "+00:00")),
            ),
        )
        session.add(action)
        session.flush()
        result = (payload, str(action.id))
        session.commit()  # Release Core source locks before the CAB callback.
        return result


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
                Action.finished_at.is_(None),
                Action.created_at <= now,
                Action.expires_at > now,
            )
            .order_by(Action.created_at.desc())
            .limit(1)
        ).first()
        require(
            action is not None
            and action.actor.get("issuer") == request.actor.issuer
            and action.actor.get("subject") == request.actor.subject
        )
        actor = VerifiedProviderSession(
            request.actor.issuer, request.actor.subject, action.actor["sessionSha256"]
        )
        row, now, _ = _current(session, actor, operation_id)
        # Practice/source locks precede the action lock, including the BFF finish path.
        action = session.exec(
            select(Action)
            .where(Action.id == action.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        now = _now(session)
        require(action.finished_at is None)
        operation = minimized_operation(row)
        require(
            action.operation_sha256 == digest(operation)
            and now < action.expires_at
            and request.sourceContentSha256 == row.source["contentSha256"]
            and request.sourceTextSha256 == row.source["textSha256"]
        )
        return {
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
                now + timedelta(seconds=30), action.expires_at
            ).isoformat(),
        }
