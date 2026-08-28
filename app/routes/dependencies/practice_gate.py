"""Fail-closed practice gate for the internal API.

CLAUDE_BAA_IMPLEMENTATION guardrail 5 and ARCHITECTURE.md §8: no PHI-bearing
feature is reachable by an account without a recorded BAA acceptance for its
practice (or the negotiated-BAA-on-file fallback), an authorized membership,
and entitlement (trial, subscription, or exemption — ADR 0017). Routes are
gated by default so a future endpoint cannot ship ungated; the allowlist is
the onboarding surface that must work before the BAA exists, and the
internal-admin surface is governed by ``require_admin`` rather than by any
customer's BAA.

Doctors predating practices are not exempt: on their next login the frontend
routes them through practice creation and BAA acceptance (or an internal
admin sets ``negotiated_baa_on_file``).
"""

from fastapi import HTTPException, Request, status

from app.services.doctor_access import (
    doctor_capability_rollout_in_force,
    resolve_doctor_access,
)

from app.models.doctor import Doctor

EXEMPT_ROUTE_NAMES = {
    # settings back the onboarding wizard and hold no patient data
    "settings_data",
    "update_settings_data",
    # the practice/BAA flow itself
    "practice_status",
    "create_practice",
    "accept_practice_baa",
    "lookup_practice",
    "join_practice",
    "organization_access_opportunity",
    "claim_organization_access",
    "baa_current",
    "practice_baa_acceptances",
    "public_onboarding_state",
    "public_legal_current",
    "accept_public_legal",
    "public_capabilities",
    "practice_invitations",
    "create_practice_invitation",
    "accept_practice_invitation",
    "revoke_practice_invitation",
    "practice_members",
    "deactivate_practice_member",
    "reactivate_practice_member",
    # support view-as control plane; begin/targets enforce the independent
    # environment-scoped capability and status/end must remain reachable to
    # clear stale sessions even when the actor has no customer practice
    "view_as_status",
    "view_as_targets",
    "begin_view_as",
    "end_view_as",
    # enforces its own practice/BAA rules
    "complete_onboarding",
}

# These routes still require a real practice, an in-force BAA, and an authorized
# member. Only the entitlement check is skipped so a lapsed owner can pay their
# way back in without creating a broader practice-gate bypass.
ENTITLEMENT_EXEMPT_ROUTE_NAMES = {
    "create_billing_checkout",
    "create_billing_portal",
}

# Checkout must remain reachable while credential review is pending; starting
# the fixed billing clock never grants PHI access. The handlers still require
# the authoritative practice, BAA, membership, and public legal gates.
CREDENTIAL_EXEMPT_ROUTE_NAMES = {
    "create_billing_checkout",
    "create_billing_portal",
}

# Every protected provider route is classified explicitly. An unclassified
# future route remains available to legacy/managed practices but fails closed
# for a public practice until its owning workstream assigns a capability.
CAPABILITY_ROUTE_NAMES = {
    "intake_list": "intake_prep",
    "intake_detail": "intake_prep",
    "note_list": "soap_note",
    "patient_list": "soap_note",
    "note_detail": "soap_note",
    "open_sandbox_labs_record": "soap_note",
    "generate_signed_visit_recording_upload_url": "soap_note",
    "upload_visit_audio_file": "soap_note",
    "generate_signed_example_note_upload_url": "soap_note",
    "upload_example_note": "soap_note",
    "generate_signed_example_note_upload_url_v2": "soap_note",
    "upload_example_note_v2": "soap_note",
    "update_note_customization": "soap_note",
    "zoom_auth_url": "soap_note",
    "zoom_disconnect": "soap_note",
    "zoom_revoked_ack": "soap_note",
    "schedule_list": "soap_note",
    "cancel_meeting": "soap_note",
    "medication_list": "soap_note",
    "medication_create": "soap_note",
    "medication_update": "soap_note",
    "medication_delete": "soap_note",
    "medication_extract": "soap_note",
    "patient_reference_resources": "soap_note",
    "medication_drug_reference": "soap_note",
    "medication_nutrient_depletions": "soap_note",
    "lab_panel_list": "soap_note",
    "lab_panel_create": "soap_note",
    "generate_signed_lab_upload_url": "soap_note",
    "lab_panel_upload_complete": "soap_note",
    "lab_panel_delete": "soap_note",
    "lab_evaluations": "soap_note",
    "lab_range_list": "soap_note",
    "lab_range_upsert": "soap_note",
    "lab_range_reset": "soap_note",
}

ADMIN_PATH_PREFIX = "/internal/v1/admin"


def require_practice_in_force(request: Request) -> None:
    route = request.scope.get("route")
    route_name = route.name if route is not None else None
    if route_name in EXEMPT_ROUTE_NAMES:
        return

    # the internal-admin surface is Cosmaneura staff tooling: require_admin
    # (401 for non-admins) governs it, not a customer practice's BAA
    if request.url.path.startswith(ADMIN_PATH_PREFIX):
        return

    doctor: Doctor = request.state.doctor
    decision = resolve_doctor_access(
        doctor,
        require_entitlement=route_name not in ENTITLEMENT_EXEMPT_ROUTE_NAMES,
        require_credential=route_name not in CREDENTIAL_EXEMPT_ROUTE_NAMES,
    )
    if decision.allowed and route_name in CREDENTIAL_EXEMPT_ROUTE_NAMES:
        return
    if decision.allowed:
        capability = (
            CAPABILITY_ROUTE_NAMES.get(route_name)
            if isinstance(route_name, str)
            else None
        )
        if doctor_capability_rollout_in_force(doctor, capability):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This capability is not enabled for the public pilot.",
        )

    if decision.denial_reason == "entitlement_not_in_force":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="This practice's free trial has ended. Add billing to continue using Cosmas.",
        )

    if decision.denial_reason == "practice_membership_required":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Name or join a practice and accept the BAA before using Cosmas.",
        )

    if decision.denial_reason == "practice_membership_inactive":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This clinician seat is inactive. Ask the practice owner to reactivate it.",
        )

    if decision.denial_reason == "public_identity_unverified":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Your verified account identity is unavailable. Sign out and "
                "sign in again, then contact support if access remains blocked."
            ),
        )

    if decision.denial_reason in {
        "credential_pending",
        "credential_needs_review",
        "credential_rejected",
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Real-PHI capabilities remain unavailable until an authorized "
                "credential reviewer approves this clinician."
            ),
        )

    if decision.denial_reason == "public_legal_not_in_force":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accept the current Terms and Privacy Policy before using Cosmas.",
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="The Cosmaneura Inc BAA must be in force before Cosmas will process PHI.",
    )
