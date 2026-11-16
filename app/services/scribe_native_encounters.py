"""Explicit native visit versions reuse Core's current authority and writers."""

from collections.abc import Callable

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.configuration.scribe_native import native_visit_actions_v2_enabled
from app.services import authority, scribe_encounters, scribe_provider_context
from app.services.companion_records import (
    RecordBudget,
    RecordUnavailable,
    check_deadline,
)
from app.services.scribe_encounter_contract import (
    CreateRequest,
    ExistingPatient,
    PatientMatchCandidatesRequest,
    PatientMatchSaveRequest,
    PatientsRequest,
    PrivateNoteReadRequest,
    PrivateNoteSaveRequest,
)


def execute_native_encounter(
    *,
    issuer: str,
    subject: str,
    session_id: str,
    request: (
        PatientsRequest
        | CreateRequest
        | PatientMatchCandidatesRequest
        | PatientMatchSaveRequest
        | PrivateNoteReadRequest
        | PrivateNoteSaveRequest
    ),
    authority_settings: AuthorityKernelSettings,
    settings_provider: Callable[[], ScribeEncounterSettings],
    deadline: float | RecordBudget,
    native_visit_actions_v2: bool = False,
) -> dict[str, object]:
    # A second, closed native route selects this subset. The existing browser
    # encounter route and its global gate keep their original behavior.
    if type(native_visit_actions_v2) is not bool:
        raise ValueError("native_visit_version_invalid")
    allowed = (
        (
            PatientsRequest,
            CreateRequest,
            PatientMatchCandidatesRequest,
            PatientMatchSaveRequest,
            PrivateNoteReadRequest,
            PrivateNoteSaveRequest,
        )
        if native_visit_actions_v2
        else (PatientsRequest, CreateRequest)
    )
    if not isinstance(request, allowed) or (
        not native_visit_actions_v2
        and isinstance(request, CreateRequest)
        and not isinstance(request.patient, ExistingPatient)
    ):
        raise ValueError("native_existing_patient_operation_required")

    def available():
        check_deadline(deadline)
        if not settings_provider().enabled or (
            native_visit_actions_v2 and not native_visit_actions_v2_enabled()
        ):
            raise RecordUnavailable

    def full_settings():
        # The existing writer re-reads this selection at its commit boundary.
        return ScribeEncounterSettings(
            enabled=settings_provider().enabled and native_visit_actions_v2_enabled()
        )

    available()

    def current():
        return scribe_provider_context._connection(
            issuer=issuer,
            subject=subject,
            session_id=session_id,
            authority_settings=authority_settings,
            deadline=deadline,
        )

    connection = current()
    if (
        request.expected_actor_ref != connection.actor_ref
        or request.expected_tenant_ref != connection.tenant_ref
    ):
        raise authority.AuthorityRequestDeniedError
    # This exact existing service owns patient/doctor selection, practice/member
    # checks, the doctor+creation_key lock/receipt, metadata audit, current final
    # authority recheck and commit-time gate. No parallel writer is introduced.
    value = scribe_encounters.encounter_workflow(
        issuer=issuer,
        subject=subject,
        session_id=session_id,
        request=request,
        authority_settings=authority_settings,
        settings_provider=full_settings
        if native_visit_actions_v2
        else settings_provider,
        deadline=deadline,
    )
    account = value.get("account")
    if (
        not isinstance(account, dict)
        or value.get("version") != "1"
        or value.get("action") != request.action
        or value.get("request_nonce") != str(request.request_nonce)
        or account.get("actor_ref") != connection.actor_ref
        or account.get("tenant_ref") != connection.tenant_ref
        or current() != connection
    ):
        raise authority.AuthorityRequestDeniedError
    available()
    # The domain writer retains its existing v1 contract. Only the explicitly
    # selected full native facade changes its outer protocol version.
    return {**value, "version": "2"} if native_visit_actions_v2 else value
