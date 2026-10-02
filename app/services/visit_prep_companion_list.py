"""Explicit companion preparation selection using Core's owner list domain."""

import json
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.configuration import authority, service_auth
from app.configuration.clerk import VisitPrepPhysicianPrincipal
from app.configuration.service_auth import VisitPrepWorkloadPrincipal
from app.services.companion_encounter_contract import digest
from app.services.companion_record_contract import ActorRef, Closed, Digest, TenantRef
from app.services.companion_records import (
    RecordUnavailable,
    check_deadline,
    transaction_budget,
)
from app.services.visit_prep_command_contract import (
    RecipientInput,
    _normalize_recipient,
)
from app.services.visit_prep_companion import (
    PreparationRef,
    VisitPrepCompanionDenied,
    _locked_connection,
    _reauthorize,
    _receiver_gate,
    _type_id,
)
from app.services.visit_prep_packages import _database_now, list_visit_preps_in_session
from app.utils.encryption import decrypt_value

from activemodel.session_manager import global_session
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep import VisitPrep

PATH = "/service/v1/companion/visit-prep/preparations"
MAX_RESPONSE_BYTES = 32768


@dataclass(frozen=True)
class SelectorList:
    preparations: tuple = field(repr=False)
    recipients: tuple = field(repr=False)
    next_before_id: object


def _recipient(prep):
    """Minimum identifier for selection; never return contacts or answer words."""
    if prep.recipient_snapshot_format_version != 1:
        raise RecordUnavailable
    plaintext = decrypt_value(prep.recipient_snapshot_ciphertext)
    if len(plaintext.encode("utf-8")) > 12000:
        raise RecordUnavailable

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise RecordUnavailable
            value[key] = item
        return value

    value = json.loads(plaintext, object_pairs_hook=unique)
    if (
        set(value) != {"version", "first_name", "role", "email", "phone"}
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise RecordUnavailable
    recipient = _normalize_recipient(
        RecipientInput(
            first_name=value["first_name"],
            role=value["role"],
            email=value["email"],
            phone=value["phone"],
        )
    )
    return recipient.first_name, recipient.role


class VisitPrepCompanionListRequest(Closed):
    contract_version: Literal["1"]
    action: Literal["visit-prep.list"]
    purpose: Literal["provider_previsit_review"]
    expected_actor_ref: ActorRef
    expected_tenant_ref: TenantRef
    connection_sha256: Digest
    workspace_id: UUID
    request_nonce: UUID
    before_ref: PreparationRef | None
    limit: Annotated[int, Field(ge=1, le=20, strict=True)]


def read_visit_prep_companion_list(
    *, workload, physician, request, deadline, serialize
):
    """Keep selector metadata private until current authority and commit ACK."""
    if not isinstance(workload, VisitPrepWorkloadPrincipal) or not isinstance(
        physician, VisitPrepPhysicianPrincipal
    ):
        raise VisitPrepCompanionDenied
    request = VisitPrepCompanionListRequest.model_validate(request.model_dump())
    auth_settings = authority.AUTHORITY_KERNEL_SETTINGS
    policy = service_auth.COMPANION_VISIT_PREP_AUTH_SETTINGS
    check_deadline(deadline)
    with global_session() as session:
        try:
            with session.no_autoflush, transaction_budget(session, deadline):
                _receiver_gate(
                    workload, physician, _database_now(session), auth_settings, policy
                )
                scope, connection = _locked_connection(
                    session, physician, request, auth_settings
                )

                def selection(*, audit=True):
                    page = list_visit_preps_in_session(
                        session,
                        owner_practice_member_id=scope.member_id,
                        owner_doctor_id=scope.doctor_id,
                        practice_id=scope.practice_id,
                        expected_clerk_subject=physician.subject,
                        limit=request.limit,
                        before_id=_type_id(request.before_ref)
                        if request.before_ref
                        else None,
                        audit=audit,
                    )
                    return SelectorList(
                        page.preparations,
                        tuple(
                            _recipient(session.get(VisitPrep, p.id))
                            for p in page.preparations
                        ),
                        page.next_before_id,
                    )

                result = selection()
                session.add(
                    AuthorityAuditEvent(
                        event_type="visit_prep.companion_list",
                        outcome="allowed",
                        occurred_at=_database_now(session),
                        workload_client_id=workload.client_id,
                        client_event_id=request.request_nonce,
                        actor_id=_type_id(connection.actor_ref),
                        tenant_id=_type_id(connection.tenant_ref),
                        resource_type="practice_member",
                        resource_ref=str(scope.member_id),
                        route="companion_visit_prep_list",
                        intent=request.purpose,
                        request_digest=digest(request.model_dump(mode="json")),
                        platform_refs=[
                            {"kind": "workspace", "ref": str(request.workspace_id)}
                        ],
                    )
                )
                session.flush()
                at = _reauthorize(session, scope, physician, request, auth_settings)
                _receiver_gate(workload, physician, at, auth_settings, policy)
                body = serialize(result, at)
                if (
                    not isinstance(body, bytes)
                    or not body
                    or len(body) > MAX_RESPONSE_BYTES
                ):
                    raise RecordUnavailable
                final = selection(audit=False)
                if final != result:
                    raise RecordUnavailable
                at = _reauthorize(session, scope, physician, request, auth_settings)
                _receiver_gate(workload, physician, at, auth_settings, policy)
                check_deadline(deadline)
                session.commit()
                check_deadline(deadline)
                return body
        except Exception:
            session.rollback()
            raise
