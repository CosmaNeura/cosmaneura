import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, cast

import typer
from fastapi.routing import APIRoute
from typeid import TypeID

from activemodel.session_manager import global_session
from activemodel.types import TypeIDType

app = typer.Typer()


def _superadmin_capability_manifest_from_input(
    manifest_path: Path | None,
):
    """Load an owner-only file or collect one target without terminal echo."""

    from app.services.superadmin_capability import (
        configured_superadmin_capability_environment,
        configured_superadmin_clerk_issuer,
        load_private_superadmin_capability_manifest,
        parse_superadmin_capability_manifest,
        validate_superadmin_manifest_runtime_scope,
    )

    if manifest_path is not None:
        manifest = load_private_superadmin_capability_manifest(manifest_path)
    else:
        environment = configured_superadmin_capability_environment()
        issuer = configured_superadmin_clerk_issuer()
        subject = typer.prompt("Exact Clerk subject", hide_input=True)
        email = typer.prompt("Exact verified primary email", hide_input=True)
        doctor_id = typer.prompt("Exact Doctor TypeID", hide_input=True)
        kinds = typer.prompt(
            "Capability kinds (comma-separated platform_admin,view_as)",
            hide_input=True,
        )
        manifest = parse_superadmin_capability_manifest(
            {
                "schema_version": 1,
                "environment": environment.value,
                "clerk_issuer": issuer,
                "accounts": [
                    {
                        "clerk_subject": subject,
                        "verified_primary_email": email,
                        "doctor_id": doctor_id,
                        "capability_kinds": [
                            value.strip() for value in kinds.split(",") if value.strip()
                        ],
                    }
                ],
            }
        )
    validate_superadmin_manifest_runtime_scope(manifest)
    return manifest


@app.command()
def write_versions():
    """
    Write chrome, redis, and postgres versions to disk.

    Helpful to automatically ensuring dev, test, and prod parity
    """

    from app.configuration.versions import (
        VERSIONS_FILE,
        chrome_version,
        postgres_version,
        redis_version,
    )

    VERSIONS_FILE.write_text(
        json.dumps(
            {
                "chrome": chrome_version(),
                "postgres": postgres_version(),
                "redis": redis_version(),
            },
            indent=2,
        )
    )


@app.command()
def dump_openapi(app_target: str = "api_app"):
    """
    Dump OpenAPI schema for the specified app target.
    """
    import importlib
    import json

    from app.utils.openapi import generate_openapi_schema

    # Dynamically get the target app from app.server module
    server_module = importlib.import_module("app.server")
    available_apps = [
        name
        for name in dir(server_module)
        # TODO is this a fastapi router? Probably a better way to do this?
        if not name.startswith("_") and hasattr(getattr(server_module, name), "routes")
    ]

    if app_target not in available_apps:
        typer.echo(
            f"Error: '{app_target}' not found. Available targets: {', '.join(available_apps)}"
        )
        raise typer.Exit(1)

    target_app = getattr(server_module, app_target)

    # Use our utility function
    openapi = generate_openapi_schema(target_app)

    typer.echo(json.dumps(openapi))


@app.command()
def routes():
    "output list of routes available in the application"

    from app.server import api_app

    for route in api_app.routes:
        if isinstance(route, APIRoute):
            methods = sorted(route.methods)
            method = ", ".join(methods)
            typer.echo(f"{method}   {route.path}    {route.name}")
        else:
            typer.echo(f"unsupported route: {route}")


def _typeid(value: str, expected_prefix: str) -> TypeIDType:
    try:
        parsed = TypeID.from_string(value)
    except Exception as exc:
        raise typer.BadParameter("must be a valid TypeID") from exc
    if parsed.prefix != expected_prefix:
        raise typer.BadParameter(f"must have the {expected_prefix}_ prefix")
    # ActiveModel annotates TypeID-valued model fields with its SQLAlchemy
    # TypeDecorator even though the validated runtime value is typeid.TypeID.
    return cast(TypeIDType, parsed)


@app.command("ensure-staging-synthetic-preview-appointment")
def ensure_staging_synthetic_preview_appointment(
    doctor_id: str = typer.Option(...),
    doctor_email: str = typer.Option(...),
    operator_ref: str = typer.Option(...),
    confirm_staging_only: bool = typer.Option(
        False,
        "--confirm-staging-only",
        help="Confirm this command is targeting the staging deployment only.",
    ),
    confirm_no_real_patient_data: bool = typer.Option(
        False,
        "--confirm-no-real-patient-data",
        help="Confirm the command must create fixed synthetic data only.",
    ),
) -> None:
    """Create or find one fixed synthetic staging patient and appointment."""

    from app.services.staging_synthetic_appointment import (
        StagingSyntheticAppointmentError,
        ensure_staging_synthetic_appointment,
    )

    if not confirm_staging_only or not confirm_no_real_patient_data:
        typer.echo(
            "Provisioning refused: --confirm-staging-only and "
            "--confirm-no-real-patient-data are required.",
            err=True,
        )
        raise typer.Exit(2)

    try:
        with global_session():
            result = ensure_staging_synthetic_appointment(
                doctor_id=_typeid(doctor_id, "doctor"),
                doctor_email=doctor_email,
                operator_ref=operator_ref,
            )
    except StagingSyntheticAppointmentError as exc:
        typer.echo(f"Provisioning refused: {exc}", err=True)
        raise typer.Exit(1) from exc

    state = "created" if result.created else "already exists"
    typer.echo(
        "Synthetic staging fixture "
        f"{state}: patient_id={result.patient.id} "
        f"appointment_id={result.appointment.id}."
    )


@app.command("provision-athena-preview-record")
def provision_athena_preview_record(
    doctor_id: str = typer.Option(...),
    patient_id: str = typer.Option(...),
    record_ref: str = typer.Option(...),
    operator_ref: str = typer.Option(...),
    confirm_synthetic_only: bool = typer.Option(
        False,
        "--confirm-synthetic-only",
        help="Confirm this is athena Preview practice 195900 synthetic data.",
    ),
) -> None:
    """Provision the single governed sandbox record mapping for a patient."""

    from app.services.ehr_sandbox_record_mapping import (
        SandboxRecordMappingError,
        provision_athena_preview_mapping,
    )

    if not confirm_synthetic_only:
        typer.echo(
            "Provisioning refused: --confirm-synthetic-only is required.",
            err=True,
        )
        raise typer.Exit(2)

    try:
        with global_session():
            mapping = provision_athena_preview_mapping(
                doctor_id=_typeid(doctor_id, "doctor"),
                patient_id=_typeid(patient_id, "patient"),
                record_ref=record_ref,
                operator_ref=operator_ref,
            )
    except SandboxRecordMappingError as exc:
        typer.echo(f"Provisioning refused: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Provisioned synthetic Preview mapping {mapping.id}.")


@app.command("designate-athena-preview-patient")
def designate_athena_preview_patient_record(
    doctor_id: str = typer.Option(...),
    patient_id: str = typer.Option(...),
    operator_ref: str = typer.Option(...),
    confirm_no_real_patient_data: bool = typer.Option(
        False,
        "--confirm-no-real-patient-data",
        help=(
            "Confirm this platform patient is a synthetic demo record and "
            "contains no real patient data."
        ),
    ),
) -> None:
    """Designate a doctor-scoped platform patient as synthetic-only."""

    from app.services.ehr_sandbox_record_mapping import (
        SandboxRecordMappingError,
        designate_athena_preview_patient,
    )

    if not confirm_no_real_patient_data:
        typer.echo(
            "Designation refused: --confirm-no-real-patient-data is required.",
            err=True,
        )
        raise typer.Exit(2)
    try:
        with global_session():
            designation = designate_athena_preview_patient(
                doctor_id=_typeid(doctor_id, "doctor"),
                patient_id=_typeid(patient_id, "patient"),
                operator_ref=operator_ref,
            )
    except SandboxRecordMappingError as exc:
        typer.echo(f"Designation refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Designated synthetic Preview patient {designation.id}.")


@app.command("revoke-athena-preview-record")
def revoke_athena_preview_record(
    doctor_id: str = typer.Option(...),
    patient_id: str = typer.Option(...),
    operator_ref: str = typer.Option(...),
) -> None:
    """Revoke an active sandbox mapping without deleting its history."""

    from app.services.ehr_sandbox_record_mapping import (
        SandboxRecordMappingError,
        revoke_athena_preview_mapping,
    )

    try:
        with global_session():
            mapping = revoke_athena_preview_mapping(
                doctor_id=_typeid(doctor_id, "doctor"),
                patient_id=_typeid(patient_id, "patient"),
                operator_ref=operator_ref,
            )
    except SandboxRecordMappingError as exc:
        typer.echo(f"Revocation refused: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Revoked synthetic Preview mapping {mapping.id}.")


@app.command("revoke-athena-preview-patient-designation")
def revoke_athena_preview_patient_record_designation(
    doctor_id: str = typer.Option(...),
    patient_id: str = typer.Option(...),
    operator_ref: str = typer.Option(...),
) -> None:
    """Remove synthetic status after the associated mapping is revoked."""

    from app.services.ehr_sandbox_record_mapping import (
        SandboxRecordMappingError,
        revoke_athena_preview_patient_designation,
    )

    try:
        with global_session():
            designation = revoke_athena_preview_patient_designation(
                doctor_id=_typeid(doctor_id, "doctor"),
                patient_id=_typeid(patient_id, "patient"),
                operator_ref=operator_ref,
            )
    except SandboxRecordMappingError as exc:
        typer.echo(f"Designation revocation refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Revoked synthetic patient designation {designation.id}.")


@app.command("provision-staging-provider-authority")
def provision_staging_provider_authority(
    doctor_id: str = typer.Option(...),
    doctor_email: str = typer.Option(...),
    appointment_id: str = typer.Option(...),
    operator_ref: str = typer.Option(...),
    tenant_id: str | None = typer.Option(
        None,
        help=(
            "Optional existing authority tenant. Omit for the first pilot provider; "
            "reuse the returned tenant id for additional providers in that practice."
        ),
    ),
    confirm_staging_only: bool = typer.Option(
        False,
        "--confirm-staging-only",
    ),
    confirm_synthetic_only: bool = typer.Option(
        False,
        "--confirm-synthetic-only",
    ),
) -> None:
    """Bind a signed-in provider and synthetic appointment to authority records."""

    from app.services.authority_provisioning import (
        AuthorityProvisioningError,
    )
    from app.services.authority_provisioning import (
        provision_staging_provider_authority as provision,
    )

    if not confirm_staging_only or not confirm_synthetic_only:
        typer.echo(
            "Provisioning refused: --confirm-staging-only and "
            "--confirm-synthetic-only are required.",
            err=True,
        )
        raise typer.Exit(2)
    try:
        with global_session():
            result = provision(
                doctor_id=_typeid(doctor_id, "doctor"),
                doctor_email=doctor_email,
                appointment_id=_typeid(appointment_id, "appointment"),
                operator_ref=operator_ref,
                tenant_id=(
                    _typeid(tenant_id, "authority_tenant") if tenant_id else None
                ),
            )
    except AuthorityProvisioningError as exc:
        typer.echo(f"Provisioning refused: {exc}", err=True)
        raise typer.Exit(1) from exc

    state = "created" if result.created else "already exists"
    typer.echo(
        "Provider authority "
        f"{state}: actor_id={result.actor_id} tenant_id={result.tenant_id} "
        f"person_id={result.person_id} binding_ref={result.binding_ref}."
    )


@app.command("rebind-staging-provider-authority-identity")
def rebind_staging_provider_authority_identity_command(
    doctor_id: str = typer.Option(...),
    appointment_id: str = typer.Option(...),
    actor_id: str = typer.Option(...),
    operator_ref: str = typer.Option(...),
    reason: str = typer.Option(...),
    current_clerk_pair_sha256: str = typer.Option(
        ...,
        "--current-clerk-pair-sha256",
        help="Confirmed digest of the one active Clerk subject/email pair.",
    ),
    stale_subject_sha256: str = typer.Option(
        ...,
        "--stale-subject-sha256",
        help="Confirmed SHA-256 of the one stale active Clerk subject.",
    ),
    confirm_plan_sha256: str | None = typer.Option(
        None,
        "--confirm-plan-sha256",
        help="Exact digest printed by dry-run; required with --apply.",
    ),
    confirm_staging_only: bool = typer.Option(False, "--confirm-staging-only"),
    confirm_revoke_only_stale_identity: bool = typer.Option(
        False,
        "--confirm-revoke-only-stale-identity",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the validated plan."),
) -> None:
    """Rebind one reviewed synthetic provider actor to its current Clerk subject."""

    from app.services.authority_identity_rebind import (
        AuthorityIdentityRebindError,
        plan_staging_provider_authority_identity_rebind,
        rebind_staging_provider_authority_identity,
    )

    try:
        parsed_doctor_id = _typeid(doctor_id, "doctor")
        parsed_appointment_id = _typeid(appointment_id, "appointment")
        parsed_actor_id = _typeid(actor_id, "authority_actor")
        with global_session():
            if not apply:
                plan = plan_staging_provider_authority_identity_rebind(
                    doctor_id=parsed_doctor_id,
                    appointment_id=parsed_appointment_id,
                    actor_id=parsed_actor_id,
                    operator_ref=operator_ref,
                    reason=reason,
                    expected_current_clerk_pair_sha256=current_clerk_pair_sha256,
                    expected_stale_subject_sha256=stale_subject_sha256,
                )
                state = "already-applied" if plan.already_applied else "ready"
                typer.echo(
                    "DRY RUN: staging authority identity rebind "
                    f"state={state} identities={plan.identity_count} "
                    f"active={plan.active_identity_count} "
                    f"revoked={plan.revoked_identity_count} "
                    f"plan_sha256={plan.plan_sha256}; no changes made."
                )
                return

            if (
                not confirm_staging_only
                or not confirm_revoke_only_stale_identity
                or confirm_plan_sha256 is None
            ):
                raise AuthorityIdentityRebindError(
                    "apply requires both staging confirmations and the plan digest"
                )
            result = rebind_staging_provider_authority_identity(
                doctor_id=parsed_doctor_id,
                appointment_id=parsed_appointment_id,
                actor_id=parsed_actor_id,
                operator_ref=operator_ref,
                reason=reason,
                expected_current_clerk_pair_sha256=current_clerk_pair_sha256,
                expected_stale_subject_sha256=stale_subject_sha256,
                confirmed_plan_sha256=confirm_plan_sha256,
            )
    except AuthorityIdentityRebindError:
        typer.echo(
            "Authority identity rebind refused; no identity values were printed.",
            err=True,
        )
        raise typer.Exit(1) from None

    state = "applied" if result.changed else "already applied"
    typer.echo(f"Authority identity rebind {state}; plan_sha256={result.plan_sha256}.")


@app.command("rebind-doctor-clerk-identity")
def rebind_doctor_clerk_identity_command(
    doctor_id: str = typer.Option(
        ..., help="Stable Doctor TypeID for the account whose records are retained."
    ),
    operator_ref: str = typer.Option(..., help="Durable operator/ticket reference."),
    reason: str = typer.Option(..., help="Reason for the privileged recovery."),
    confirm_plan_sha256: str | None = typer.Option(
        None,
        "--confirm-plan-sha256",
        help="Exact digest printed by dry-run; required with --apply.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the reviewed plan."),
) -> None:
    """Rebind a retained provider record after its Clerk sign-in was recreated.

    Identity values are prompted privately and never printed. The original
    Clerk account must be absent, and the replacement must expose the same
    verified primary email. Clinical and legal records remain on the original
    Doctor row; an empty replacement shell is retained as a disabled tombstone.
    """

    from app.services.doctor_identity_rebind import (
        DoctorIdentityRebindError,
        plan_doctor_identity_rebind,
        rebind_doctor_identity,
        resolve_live_rebind_identity,
    )

    stale_clerk_id = typer.prompt("Prior Clerk subject", hide_input=True)
    replacement_clerk_id = typer.prompt("Replacement Clerk subject", hide_input=True)
    confirmed_email = typer.prompt("Exact verified account email", hide_input=True)
    try:
        replacement = resolve_live_rebind_identity(
            stale_clerk_id=stale_clerk_id,
            replacement_clerk_id=replacement_clerk_id,
            confirmed_email=confirmed_email,
        )
        parsed_doctor_id = _typeid(doctor_id, "doctor")
        with global_session():
            plan = plan_doctor_identity_rebind(
                doctor_id=parsed_doctor_id,
                stale_clerk_id=stale_clerk_id,
                replacement=replacement,
                operator_ref=operator_ref,
                reason=reason,
            )
            if not apply:
                state = "already applied" if plan.already_applied else "ready"
                typer.echo(
                    "DRY RUN: doctor identity recovery "
                    f"state={state} doctor_id={plan.doctor_id} "
                    f"practice_id={plan.practice_id} "
                    f"environment={plan.environment} "
                    f"clerk_issuer_sha256={plan.clerk_issuer_sha256} "
                    f"provisional_shell={plan.provisional_doctor_id is not None} "
                    f"pilot_grant={plan.pilot_grant_rebound} "
                    f"authority_identities={plan.authority_identity_count} "
                    f"plan_sha256={plan.plan_sha256}; no changes made."
                )
                return
            if confirm_plan_sha256 is None:
                raise DoctorIdentityRebindError(
                    "--apply requires --confirm-plan-sha256"
                )
            result = rebind_doctor_identity(
                doctor_id=parsed_doctor_id,
                stale_clerk_id=stale_clerk_id,
                replacement=replacement,
                operator_ref=operator_ref,
                reason=reason,
                confirmed_plan_sha256=confirm_plan_sha256,
            )
    except DoctorIdentityRebindError:
        outcome = "outcome is ambiguous" if apply else "validation failed"
        raise typer.BadParameter(
            f"doctor identity recovery {outcome}; verify account state; "
            "no identity values were printed"
        ) from None

    state = "applied" if result.changed else "already applied"
    typer.echo(
        f"Doctor identity recovery {state}: doctor_id={result.doctor_id} "
        f"plan_sha256={result.plan_sha256}."
    )


@app.command("close-provider-account")
def close_provider_account_command(
    doctor_id: str = typer.Option(
        ..., help="Stable Doctor TypeID for the provider account to disable."
    ),
    operator_ref: str = typer.Option(..., help="Durable operator/ticket reference."),
    reason: str = typer.Option(..., help="Reason for the privileged closure."),
    confirm_email: str | None = typer.Option(
        None,
        help=(
            "Exact account email confirmation. Omit to enter it privately and keep "
            "it out of shell history."
        ),
    ),
    confirm_plan_sha256: str | None = typer.Option(
        None,
        "--confirm-plan-sha256",
        help="Exact digest printed by dry-run; required with --apply.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the reviewed plan."),
) -> None:
    """Disable a provider account while retaining records for at least 7 years.

    This command never deletes the Clerk identity, clinical records, BAA
    acceptances, or audit history. A bound Stripe subscription is not canceled;
    its billing follow-up is reported explicitly.
    """

    import hmac

    from app.services.doctor_account_closure import (
        DoctorAccountClosureError,
        close_doctor_account,
        plan_doctor_account_closure,
    )

    _validate_operator_change(operator_ref, reason)
    entered_email = confirm_email or typer.prompt(
        "Exact account email", hide_input=True
    )
    try:
        parsed_doctor_id = _typeid(doctor_id, "doctor")
        with global_session():
            doctor = _doctor_by_id(doctor_id)
            stored_email = (doctor.email or "").strip().lower()
            supplied_email = entered_email.strip().lower()
            if not stored_email or not hmac.compare_digest(
                stored_email, supplied_email
            ):
                raise DoctorAccountClosureError(
                    "email confirmation does not match the provider account"
                )
            plan = plan_doctor_account_closure(
                doctor_id=parsed_doctor_id,
                operator_ref=operator_ref,
                reason=reason,
            )
            if not apply:
                state = "already applied" if plan.already_applied else "ready"
                typer.echo(
                    "DRY RUN: provider account closure "
                    f"state={state} doctor_id={plan.doctor_id} "
                    f"practice_id={plan.practice_id} member_id={plan.member_id} "
                    f"role={plan.member_role} pilot_grants={plan.pilot_grant_count} "
                    f"roster_approvals={plan.organization_approval_count} "
                    f"authority_identities={plan.authority_identity_count} "
                    f"active_mount_sessions={plan.active_mount_session_count} "
                    f"video_integrations={plan.video_integration_count} "
                    f"stripe_follow_up={plan.stripe_follow_up_required} "
                    f"plan_sha256={plan.plan_sha256}; no changes made."
                )
                return
            if confirm_plan_sha256 is None:
                raise DoctorAccountClosureError(
                    "--apply requires --confirm-plan-sha256"
                )
            result = close_doctor_account(
                doctor_id=parsed_doctor_id,
                operator_ref=operator_ref,
                reason=reason,
                confirmed_plan_sha256=confirm_plan_sha256,
            )
    except DoctorAccountClosureError as exc:
        raise typer.BadParameter(str(exc)) from None

    state = "applied" if result.changed else "already applied"
    typer.echo(
        f"Provider account closure {state}: doctor_id={result.doctor_id} "
        f"practice_id={result.practice_id} "
        f"retain_until={result.retain_until.isoformat()} "
        f"stripe_follow_up={result.stripe_follow_up_required} "
        f"plan_sha256={result.plan_sha256}; Clerk and retained records were not deleted."
    )


@app.command("authority-ingestion-status")
def authority_ingestion_status(
    older_than_minutes: int = typer.Option(15, min=1),
    limit: int = typer.Option(100, min=1, max=1000),
) -> None:
    """Print value-free stale ingestion counts and opaque interaction refs."""

    from app.services.authority_reconciliation import incomplete_ingestion_status

    with global_session():
        report = incomplete_ingestion_status(
            older_than_minutes=older_than_minutes,
            limit=limit,
        )
    typer.echo(
        json.dumps(
            {
                "older_than_minutes": report.older_than_minutes,
                "matching_interaction_count": report.matching_interaction_count,
                "pending_audit_count": report.pending_audit_count,
                "failed_audit_count": report.failed_audit_count,
                "pending_usage_count": report.pending_usage_count,
                "failed_usage_count": report.failed_usage_count,
                "interactions": [
                    {
                        "interaction_ref": row.interaction_ref,
                        "audit_status": row.audit_status,
                        "usage_status": row.usage_status,
                    }
                    for row in report.interactions
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


@app.command("reconcile-authority-ingestion-failed")
def reconcile_authority_ingestion_failed(
    interaction_ref: str = typer.Option(...),
    component: str = typer.Option(..., help="One of: audit, usage, both."),
    older_than_minutes: int = typer.Option(15, min=1),
    confirm_staging_only: bool = typer.Option(False, "--confirm-staging-only"),
) -> None:
    """Explicitly mark one stale staging ingestion component failed."""

    from app.environments import is_staging
    from app.services.authority_reconciliation import (
        IngestionReconciliationError,
        mark_stale_ingestion_failed,
    )

    if not confirm_staging_only or not is_staging():
        typer.echo(
            "Reconciliation refused: staging environment and "
            "--confirm-staging-only are required.",
            err=True,
        )
        raise typer.Exit(2)
    if component not in {"audit", "usage", "both"}:
        typer.echo("Reconciliation refused: invalid component.", err=True)
        raise typer.Exit(2)
    try:
        with global_session():
            result = mark_stale_ingestion_failed(
                interaction_ref=interaction_ref,
                component=cast(Literal["audit", "usage", "both"], component),
                older_than_minutes=older_than_minutes,
            )
    except IngestionReconciliationError as exc:
        typer.echo("Reconciliation refused: interaction state is ineligible.", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        json.dumps(
            {
                "interaction_ref": result.interaction_ref,
                "audit_status": result.audit_status,
                "usage_status": result.usage_status,
                "changed": result.changed,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


@app.command("reconcile-stale-canonical-turn")
def reconcile_stale_canonical_turn(
    turn_ref: str = typer.Option(...),
    older_than_minutes: int = typer.Option(15, min=1),
    apply: bool = typer.Option(False, "--apply"),
    confirm_staging_only: bool = typer.Option(False, "--confirm-staging-only"),
) -> None:
    """Dry-run or write one fixed unavailable final for one stale accepted turn."""

    from app.environments import is_staging
    from app.services.authority_reconciliation import (
        IngestionReconciliationError,
        reconcile_stale_accepted_turn,
    )

    if not is_staging():
        typer.echo("Reconciliation refused: staging environment is required.", err=True)
        raise typer.Exit(2)
    if apply and not confirm_staging_only:
        typer.echo(
            "Reconciliation refused: --apply requires --confirm-staging-only.",
            err=True,
        )
        raise typer.Exit(2)
    try:
        with global_session():
            result = reconcile_stale_accepted_turn(
                turn_id=_typeid(turn_ref, "conversation_turn"),
                older_than_minutes=older_than_minutes,
                apply=apply,
            )
    except IngestionReconciliationError as exc:
        typer.echo("Reconciliation refused: turn state is ineligible.", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        json.dumps(
            {
                "turn_ref": result.turn_ref,
                "interaction_ref": result.interaction_ref,
                "state": result.state,
                "mode": "apply" if apply else "dry-run",
                "changed": result.changed,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


@app.command("list-pilot-doctors")
def list_pilot_doctors() -> None:
    """List pre-cutover doctors (onboarded, no practice membership yet).

    These are the early-adopter pilots: on next login the frontend routes
    them through practice creation + BAA acceptance, which starts a 14-day
    trial. Comp them with ``set-billing-exempt`` once their practice exists.
    Practices are never created for them here — the covered entity's legal
    name on an acceptance record must be typed by the accepting owner.
    """
    from app.models.doctor import Doctor
    from app.models.practice import PracticeMember

    with global_session():
        pilots = [
            doctor
            for doctor in cast(list[Doctor], Doctor.all())
            if doctor.onboarding_completed_at is not None
            and PracticeMember.get(doctor_id=doctor.id) is None
        ]
        if not pilots:
            typer.echo("No onboarded doctors without a practice membership.")
            return
        for doctor in pilots:
            typer.echo(f"{doctor.id}  {doctor.email}")


def _practice_by_join_code(join_code: str):
    from app.models.practice import Practice

    normalized = join_code.strip().upper().replace(" ", "").replace("-", "")
    practice = Practice.get(join_code=normalized)
    if practice is None:
        typer.echo(f"No practice with join code {join_code!r}.", err=True)
        raise typer.Exit(1)
    return practice


def _utc_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise typer.BadParameter("must be a YYYY-MM-DD date") from exc


@app.command("set-billing-exempt")
def set_billing_exempt(
    join_code: str | None = typer.Option(
        None, help="Legacy practice join code; mutually exclusive with --practice-id."
    ),
    practice_id: str | None = typer.Option(
        None, help="Stable practice TypeID; preferred for administrative workflows."
    ),
    until: str | None = typer.Option(
        None,
        help="Last included day of the comp (YYYY-MM-DD, UTC). Omit for indefinite.",
    ),
    clear: bool = typer.Option(False, "--clear", help="Remove the exemption."),
    operator_ref: str = typer.Option(
        ..., help="Durable operator/ticket reference for this privileged change."
    ),
    reason: str = typer.Option(..., help="Why this exemption is being changed."),
) -> None:
    """Comp a practice (pilot / MCD / centrally billed): entitled without Stripe."""

    from app.services.practice_access import (
        PracticeAccessChangeError,
        set_billing_exemption,
    )

    with global_session():
        practice = _practice_by_selector(
            practice_id=practice_id,
            join_code=join_code,
        )
        if clear and until:
            raise typer.BadParameter("--until cannot be combined with --clear")
        # An inclusive calendar date becomes the following midnight as an
        # exclusive timestamp, avoiding an unexpected 00:00 start-of-day expiry.
        expires_at = (_utc_date(until) + timedelta(days=1)) if until else None
        try:
            audit = set_billing_exemption(
                practice,
                operator_ref=operator_ref,
                reason=reason,
                expires_at=expires_at,
                clear=clear,
            )
        except PracticeAccessChangeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(
            f"{practice.legal_name}: billing_exempt={practice.billing_exempt}"
            f" until={practice.billing_exempt_until or '—'} audit={audit.id}"
        )


@app.command("reconcile-stripe-checkout")
def reconcile_stripe_checkout(
    join_code: str | None = typer.Option(
        None, help="Legacy practice join code; mutually exclusive with --practice-id."
    ),
    practice_id: str | None = typer.Option(
        None, help="Stable practice TypeID; preferred for recovery workflows."
    ),
    confirm_absent: bool = typer.Option(
        False,
        "--confirm-absent",
        help=(
            "Advance only when the exact attempt is absent from the provider "
            "inventory captured by the supplied evidence."
        ),
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the displayed recovery action; omission is read-only.",
    ),
    operator_ref: str = typer.Option(
        ..., help="Durable operator/ticket reference for this privileged recovery."
    ),
    reason: str = typer.Option(..., help="Why Checkout recovery is being performed."),
    evidence_reference: str = typer.Option(
        ..., help="PHI-free reference to the retained Stripe inventory evidence."
    ),
    evidence_sha256: str = typer.Option(
        ..., help="SHA-256 of the retained Stripe inventory evidence."
    ),
) -> None:
    """Resolve one frozen Checkout attempt without blindly creating another."""

    from app.services.stripe_billing import (
        StripeCheckoutReconciliationRequired,
        StripeStateConflict,
        StripeStateInvalid,
        reconcile_frozen_checkout_request,
    )

    with global_session():
        practice = _practice_by_selector(
            practice_id=practice_id,
            join_code=join_code,
        )
        if confirm_absent and not apply:
            raise typer.BadParameter("--confirm-absent requires --apply")
        try:
            outcome, audit_id = reconcile_frozen_checkout_request(
                practice,
                operator_ref=operator_ref,
                reason=reason,
                evidence_reference=evidence_reference,
                evidence_sha256=evidence_sha256,
                confirm_absent=confirm_absent,
                apply=apply,
            )
        except (
            StripeCheckoutReconciliationRequired,
            StripeStateConflict,
            StripeStateInvalid,
        ) as exc:
            raise typer.BadParameter(str(exc) or type(exc).__name__) from exc
        mode = "applied" if apply else "dry-run"
        typer.echo(
            f"{practice.legal_name}: mode={mode} outcome={outcome} "
            f"audit={audit_id or '—'}"
        )


@app.command("set-negotiated-baa")
def set_negotiated_baa(
    join_code: str | None = typer.Option(
        None, help="Legacy practice join code; mutually exclusive with --practice-id."
    ),
    practice_id: str | None = typer.Option(
        None, help="Stable practice TypeID; preferred for administrative workflows."
    ),
    reference: str = typer.Option(
        ..., help="Document reference for the countersigned instrument."
    ),
    document_sha256: str | None = typer.Option(
        None,
        help="SHA-256 of the signed file (recommended; the file itself stays private).",
    ),
    effective: str | None = typer.Option(
        None, help="Effective date (YYYY-MM-DD, UTC). Defaults to today."
    ),
    operator_ref: str = typer.Option(
        ..., help="Durable operator/ticket reference for this legal change."
    ),
    reason: str = typer.Option(..., help="Why this negotiated BAA is being recorded."),
) -> None:
    """Record that a negotiated CN-BAA-CUST is on file (enterprise fallback).

    Satisfies PHI gating without clickwrap. Only set this once the
    countersigned instrument is actually on file.
    """

    from app.services.practice_access import (
        PracticeAccessChangeError,
        record_negotiated_baa,
    )

    with global_session():
        practice = _practice_by_selector(
            practice_id=practice_id,
            join_code=join_code,
        )
        effective_at = _utc_date(effective) if effective else datetime.now(timezone.utc)
        try:
            audit = record_negotiated_baa(
                practice,
                operator_ref=operator_ref,
                reason=reason,
                document_reference=reference,
                document_sha256=document_sha256,
                effective_at=effective_at,
            )
        except PracticeAccessChangeError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(
            f"{practice.legal_name}: negotiated BAA on file"
            f" ({practice.negotiated_baa_reference},"
            f" effective {practice.negotiated_baa_effective_date:%Y-%m-%d},"
            f" audit={audit.id})"
        )


def _practice_by_id(practice_id: str):
    from app.models.practice import Practice

    practice = Practice.get(id=_typeid(practice_id, "practice"))
    if practice is None:
        typer.echo("No practice has the supplied id.", err=True)
        raise typer.Exit(1)
    return practice


def _practice_by_selector(
    *,
    practice_id: str | None,
    join_code: str | None,
):
    if bool(practice_id) == bool(join_code):
        raise typer.BadParameter("supply exactly one of --practice-id or --join-code")
    if practice_id:
        return _practice_by_id(practice_id)
    assert join_code is not None
    return _practice_by_join_code(join_code)


def _doctor_by_id(doctor_id: str):
    from app.models.doctor import Doctor

    doctor = Doctor.get(id=_typeid(doctor_id, "doctor"))
    if doctor is None:
        typer.echo("No doctor has the supplied id.", err=True)
        raise typer.Exit(1)
    return doctor


def _organization_by_slug(slug: str):
    from app.models.organization_access import ProviderOrganization

    organization = ProviderOrganization.get(slug=slug.strip().lower())
    if organization is None:
        typer.echo("No organization has the supplied slug.", err=True)
        raise typer.Exit(1)
    return organization


def _validate_operator_change(operator_ref: str, reason: str) -> None:
    if not operator_ref.strip() or len(operator_ref.strip()) > 255:
        raise typer.BadParameter("operator-ref must contain 1 to 255 characters")
    if not reason.strip() or len(reason.strip()) > 512:
        raise typer.BadParameter("reason must contain 1 to 512 characters")


def _write_new_private_secret(path_value: str, secret: str) -> None:
    """Create a new 0600 file without following links or overwriting data."""

    import os
    from pathlib import Path

    path = Path(path_value)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise typer.BadParameter(
            "access-code-output must name a new writable file"
        ) from exc
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, f"{secret}\n".encode("ascii"))
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    else:
        os.close(descriptor)


@app.command("configure-provider-organization")
def configure_provider_organization_command(
    practice_id: str = typer.Option(..., help="Stable target practice TypeID."),
    legal_name: str = typer.Option(..., help="Organization's legal name."),
    slug: str = typer.Option(..., help="Stable lowercase organization slug."),
    email_domain: str = typer.Option(..., help="Exact approved email domain."),
    operator_ref: str = typer.Option(..., help="Durable operator/ticket reference."),
    reason: str = typer.Option(..., help="Reason for the privileged change."),
    access_code_output: str | None = typer.Option(
        None,
        help="New 0600 file that receives the one-time code; required with --apply.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the planned change."),
) -> None:
    """Configure one provider organization; dry-run unless --apply is supplied."""

    from app.services.organization_access import (
        OrganizationAccessError,
        configure_provider_organization,
    )

    from app.models.organization_access import ProviderOrganization
    from app.models.practice import Practice

    normalized_name = legal_name.strip()
    normalized_slug = slug.strip().lower()
    normalized_domain = email_domain.strip().lower().lstrip("@")
    _validate_operator_change(operator_ref, reason)
    with global_session():
        practice = _practice_by_id(practice_id)
        if not normalized_name or len(normalized_name) > 160:
            raise typer.BadParameter("legal-name must contain 1 to 160 characters")
        if (
            not normalized_slug
            or len(normalized_slug) > 64
            or any(
                char not in "abcdefghijklmnopqrstuvwxyz0123456789-"
                for char in normalized_slug
            )
        ):
            raise typer.BadParameter(
                "slug must be 1 to 64 lowercase letters, numbers, or hyphens"
            )
        if not normalized_domain or len(normalized_domain) > 253:
            raise typer.BadParameter("email-domain must contain 1 to 253 characters")
        if normalized_domain != practice.email_domain.strip().lower():
            raise typer.BadParameter(
                "email-domain must exactly match the target practice domain"
            )
        same_domain_ids = {
            str(item.id)
            for item in cast(
                list[Practice],
                list(Practice.where(Practice.email_domain == normalized_domain).all()),
            )
        }
        if same_domain_ids != {str(practice.id)}:
            raise typer.BadParameter(
                "single-practice setup requires exactly one practice for the domain"
            )
        if practice.organization_id is not None:
            raise typer.BadParameter("practice is already linked to an organization")
        if ProviderOrganization.get(slug=normalized_slug) is not None:
            raise typer.BadParameter("organization slug is already in use")
        if ProviderOrganization.get(email_domain=normalized_domain) is not None:
            raise typer.BadParameter("organization email domain is already in use")
        if not practice.negotiated_baa_in_force():
            raise typer.BadParameter("an in-force negotiated BAA is required")
        if not practice.billing_exemption_active():
            raise typer.BadParameter("an active billing exemption is required")
        if practice.stripe_checkout_session_id is not None:
            raise typer.BadParameter(
                "an existing Stripe checkout must be resolved before organization setup"
            )
        from app.services import stripe_billing

        if stripe_billing.has_nonterminal_subscription(practice):
            raise typer.BadParameter(
                "an existing Stripe subscription must be resolved before organization setup"
            )

        if not apply:
            typer.echo(
                "DRY RUN: provider organization can be configured "
                f"for practice_id={practice.id}; no access code was generated."
            )
            return
        if not access_code_output:
            raise typer.BadParameter("--apply requires --access-code-output")

        from pathlib import Path

        from app.services.organization_access import generate_access_code

        access_code = generate_access_code()
        _write_new_private_secret(access_code_output, access_code)
        try:
            organization, access_code, audit = configure_provider_organization(
                practice,
                legal_name=normalized_name,
                slug=normalized_slug,
                email_domain=normalized_domain,
                operator_ref=operator_ref,
                reason=reason,
                access_code=access_code,
            )
        except OrganizationAccessError as exc:
            Path(access_code_output).unlink(missing_ok=True)
            raise typer.BadParameter(str(exc)) from None
        except Exception:
            raise typer.BadParameter(
                "organization configuration outcome is ambiguous; the access-code "
                "output file was retained. Verify database state before retrying "
                "or rotating the organization code"
            ) from None
        typer.echo(
            "Provider organization configured: "
            f"organization_id={organization.id} practice_id={practice.id} "
            f"audit={audit.id}."
        )
        typer.echo("One-time access code written to the private output file.")


@app.command("rotate-organization-code")
def rotate_organization_code_command(
    organization_slug: str = typer.Option(..., help="Organization slug."),
    operator_ref: str = typer.Option(..., help="Durable operator/ticket reference."),
    reason: str = typer.Option(..., help="Reason for the privileged change."),
    access_code_output: str | None = typer.Option(
        None,
        help="New 0600 file that receives the one-time code; required with --apply.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the planned rotation."),
) -> None:
    """Rotate an organization access code; dry-run unless --apply is supplied."""

    from app.services.organization_access import (
        OrganizationAccessError,
        rotate_access_code,
    )

    from app.models.practice import Practice

    _validate_operator_change(operator_ref, reason)
    with global_session():
        provider_organization = _organization_by_slug(organization_slug)
        linked_practices = cast(
            list[Practice],
            list(
                Practice.where(
                    Practice.organization_id == provider_organization.id
                ).all()
            ),
        )
        if not linked_practices:
            raise typer.BadParameter("organization has no linked practices")
        linked_ids = sorted(str(practice.id) for practice in linked_practices)
        if not apply:
            typer.echo(
                "DRY RUN: organization access code can be rotated "
                f"for organization_id={provider_organization.id} "
                f"practices={','.join(linked_ids)}; "
                "no access code was generated."
            )
            return
        if not access_code_output:
            raise typer.BadParameter("--apply requires --access-code-output")

        from pathlib import Path

        from app.services.organization_access import generate_access_code

        access_code = generate_access_code()
        _write_new_private_secret(access_code_output, access_code)
        try:
            access_code, audits = rotate_access_code(
                provider_organization,
                operator_ref=operator_ref,
                reason=reason,
                access_code=access_code,
            )
        except OrganizationAccessError as exc:
            Path(access_code_output).unlink(missing_ok=True)
            raise typer.BadParameter(str(exc)) from None
        except Exception:
            raise typer.BadParameter(
                "organization code rotation outcome is ambiguous; the access-code "
                "output file was retained. Verify database state before retrying "
                "or rotating again"
            ) from None
        typer.echo(
            "Organization access code rotated: "
            f"organization_id={provider_organization.id} "
            f"practices={','.join(linked_ids)} "
            f"audits={','.join(sorted(str(audit.id) for audit in audits))}."
        )
        typer.echo("One-time access code written to the private output file.")


def _multi_practice_configuration_plan_sha256(
    assignments: list[Any],
    *,
    organization_slug: str,
    email_domain: str,
    legal_name: str,
    operator_ref: str,
    reason: str,
    source_ref: str,
    roster_sha256: str,
) -> str:
    """Hash the canonical, complete mutation plan without exposing identities."""

    from app.services.organization_access import (
        provider_organization_with_roster_plan_sha256,
    )

    return provider_organization_with_roster_plan_sha256(
        assignments,
        organization_slug=organization_slug,
        email_domain=email_domain,
        legal_name=legal_name,
        operator_ref=operator_ref,
        reason=reason,
        source_ref=source_ref,
        roster_sha256=roster_sha256,
    )


@app.command("configure-provider-organization-with-roster")
def configure_provider_organization_with_roster_command(
    manifest: str = typer.Option(
        ..., help="Private schema-version-2 multi-practice roster."
    ),
    legal_name: str = typer.Option(..., help="Organization's legal name."),
    operator_ref: str = typer.Option(..., help="Durable operator/ticket reference."),
    reason: str = typer.Option(..., help="Reason for the privileged cutover."),
    source_ref: str = typer.Option(
        ..., help="Stable reference for the approved roster."
    ),
    confirm_roster_sha256: str | None = typer.Option(
        None,
        "--confirm-roster-sha256",
        help="Raw manifest SHA-256 printed by dry-run; required with --apply.",
    ),
    confirm_plan_sha256: str | None = typer.Option(
        None,
        "--confirm-plan-sha256",
        help="Canonical plan SHA-256 printed by dry-run; required with --apply.",
    ),
    access_code_output: str | None = typer.Option(
        None,
        help="New 0600 file receiving the shared code; required with --apply.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the atomic cutover."),
) -> None:
    """Atomically link multiple practices and import their explicit roster."""

    from pathlib import Path

    from app.services.organization_access import (
        OrganizationAccessError,
        configure_provider_organization_with_roster,
        generate_access_code,
        validate_provider_organization_with_roster,
    )

    from sqlalchemy.exc import SQLAlchemyError

    assignments, slug, domain, source_sha256 = _read_multi_practice_roster_manifest(
        manifest
    )
    _validate_operator_change(operator_ref, reason)
    normalized_name = legal_name.strip()
    if not normalized_name or len(normalized_name) > 160:
        raise typer.BadParameter("legal-name must contain 1 to 160 characters")
    source = source_ref.strip()
    if not source or len(source) > 255:
        raise typer.BadParameter("source-ref must contain 1 to 255 characters")
    practice_ids = sorted({str(item.target_practice_id) for item in assignments})
    assignment_counts = {
        practice_id: sum(
            str(assignment.target_practice_id) == practice_id
            for assignment in assignments
        )
        for practice_id in practice_ids
    }
    assignment_counts_output = ",".join(
        f"{practice_id}:{assignment_counts[practice_id]}"
        for practice_id in practice_ids
    )
    plan_sha256 = _multi_practice_configuration_plan_sha256(
        assignments,
        organization_slug=slug,
        email_domain=domain,
        legal_name=normalized_name,
        operator_ref=operator_ref,
        reason=reason,
        source_ref=source,
        roster_sha256=source_sha256,
    )

    with global_session():
        try:
            practices = [_practice_by_id(practice_id) for practice_id in practice_ids]
            validate_provider_organization_with_roster(
                practices,
                assignments,
                legal_name=normalized_name,
                slug=slug,
                email_domain=domain,
                operator_ref=operator_ref,
                reason=reason,
                source_ref=source,
                source_sha256=source_sha256,
            )
        except OrganizationAccessError:
            # Multi-practice validation errors can contain an address. Keep the
            # operational output aggregate-only even on failure.
            raise typer.BadParameter(
                "multi-practice roster validation failed; no identities were printed"
            ) from None
        except SQLAlchemyError:
            raise typer.BadParameter(
                "multi-practice database validation failed; no identities were printed"
            ) from None

        if not apply:
            typer.echo(
                "DRY RUN: atomic multi-practice organization cutover is valid; "
                f"practices={','.join(practice_ids)} members={len(assignments)} "
                f"assignments_by_practice={assignment_counts_output} "
                f"roster_sha256={source_sha256} plan_sha256={plan_sha256}."
            )
            return
        confirmed_plan_sha256 = (confirm_plan_sha256 or "").strip().lower()
        if (
            confirm_roster_sha256 or ""
        ).strip().lower() != source_sha256 or confirmed_plan_sha256 != plan_sha256:
            raise typer.BadParameter(
                "--apply requires --confirm-roster-sha256 and "
                "--confirm-plan-sha256 matching the dry-run digests"
            )
        if not access_code_output:
            raise typer.BadParameter("--apply requires --access-code-output")

        access_code = generate_access_code()
        _write_new_private_secret(access_code_output, access_code)
        try:
            organization, _, roster_result, audits = (
                configure_provider_organization_with_roster(
                    practices,
                    assignments,
                    legal_name=normalized_name,
                    slug=slug,
                    email_domain=domain,
                    operator_ref=operator_ref,
                    reason=reason,
                    source_ref=source,
                    source_sha256=source_sha256,
                    plan_sha256=confirmed_plan_sha256,
                    access_code=access_code,
                )
            )
        except OrganizationAccessError:
            Path(access_code_output).unlink(missing_ok=True)
            raise typer.BadParameter(
                "multi-practice cutover failed; no identities were printed"
            ) from None
        except Exception:
            raise typer.BadParameter(
                "multi-practice cutover outcome is ambiguous; the access-code "
                "output file was retained. Verify database state before retrying "
                "or rotating the organization code; no identities were printed"
            ) from None
        typer.echo(
            "Atomic multi-practice organization cutover applied: "
            f"organization_id={organization.id} "
            f"practices={','.join(practice_ids)} "
            f"created={roster_result.created} unchanged={roster_result.unchanged} "
            f"created_by_practice={assignment_counts_output} "
            f"audits={len(audits)} roster_sha256={source_sha256} "
            f"plan_sha256={plan_sha256}."
        )
        typer.echo("Shared organization code written to the private output file.")


def _read_private_json_manifest(path_value: str):
    """Read one bounded 0600 JSON object without following symbolic links."""

    import os
    import stat
    from pathlib import Path

    path = Path(path_value)
    if path.is_symlink():
        raise typer.BadParameter("manifest must not be a symbolic link")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise typer.BadParameter("manifest could not be read") from exc
    try:
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise typer.BadParameter("manifest must be a regular file")
            if metadata.st_mode & 0o077:
                raise typer.BadParameter(
                    "manifest must not be readable or writable by group or others "
                    "(use chmod 600)"
                )
            raw = handle.read(1_048_577)
        if len(raw) > 1_048_576:
            raise typer.BadParameter("manifest must not exceed 1 MiB")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise typer.BadParameter("manifest must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise typer.BadParameter("manifest must be a JSON object")
    return payload, raw


def _read_roster_manifest(path_value: str):
    """Read a private JSON manifest and return entries plus its raw-file digest."""

    import hashlib

    from app.services.organization_access import (
        OrganizationAccessError,
        RosterApprovalInput,
        normalize_email,
    )

    payload, raw = _read_private_json_manifest(path_value)
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "members"}:
        raise typer.BadParameter(
            "manifest must contain exactly schema_version and members"
        )
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise typer.BadParameter("manifest schema_version must be 1")
    members = payload["members"]
    if not isinstance(members, list) or not members:
        raise typer.BadParameter("manifest members must be a non-empty list")

    entries = []
    normalized_emails: set[str] = set()
    for member in members:
        if not isinstance(member, dict) or set(member) != {"email"}:
            raise typer.BadParameter("each member must contain exactly email")
        if not isinstance(member["email"], str):
            raise typer.BadParameter("each member email must be a string")
        try:
            email = normalize_email(member["email"])
        except OrganizationAccessError as exc:
            raise typer.BadParameter("manifest contains an invalid email") from exc
        if email in normalized_emails:
            raise typer.BadParameter("manifest contains a duplicate email")
        normalized_emails.add(email)

        entries.append(RosterApprovalInput(email=email))

    return entries, hashlib.sha256(raw).hexdigest()


def _read_multi_practice_roster_manifest(path_value: str):
    """Read an exact v2 roster with an explicit target for every identity."""

    import hashlib

    from app.services.organization_access import (
        OrganizationAccessError,
        RosterAssignmentInput,
        normalize_email,
    )

    payload, raw = _read_private_json_manifest(path_value)
    expected_keys = {
        "schema_version",
        "organization_slug",
        "email_domain",
        "members",
    }
    if set(payload) != expected_keys:
        raise typer.BadParameter(
            "v2 manifest must contain exactly schema_version, organization_slug, "
            "email_domain, and members"
        )
    if payload["schema_version"] != 2 or isinstance(payload["schema_version"], bool):
        raise typer.BadParameter("manifest schema_version must be 2")
    if not isinstance(payload["organization_slug"], str):
        raise typer.BadParameter("organization_slug must be a string")
    slug = payload["organization_slug"].strip().lower()
    if (
        not slug
        or len(slug) > 64
        or slug != payload["organization_slug"]
        or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in slug)
    ):
        raise typer.BadParameter("organization_slug is not normalized or valid")
    if not isinstance(payload["email_domain"], str):
        raise typer.BadParameter("email_domain must be a string")
    domain = payload["email_domain"].strip().lower().lstrip("@")
    if (
        not domain
        or len(domain) > 253
        or domain != payload["email_domain"]
        or "@" in domain
        or "." not in domain
    ):
        raise typer.BadParameter("email_domain is not normalized or valid")
    members = payload["members"]
    if not isinstance(members, list) or not members:
        raise typer.BadParameter("manifest members must be a non-empty list")

    assignments = []
    normalized_emails: set[str] = set()
    target_ids: set[str] = set()
    for member in members:
        if not isinstance(member, dict) or set(member) != {
            "email",
            "target_practice_id",
        }:
            raise typer.BadParameter(
                "each v2 member must contain exactly email and target_practice_id"
            )
        if not isinstance(member["email"], str) or not isinstance(
            member["target_practice_id"], str
        ):
            raise typer.BadParameter("v2 member values must be strings")
        try:
            email = normalize_email(member["email"])
        except OrganizationAccessError as exc:
            raise typer.BadParameter("manifest contains an invalid email") from exc
        if email != member["email"]:
            raise typer.BadParameter("manifest emails must already be normalized")
        if email in normalized_emails:
            raise typer.BadParameter("manifest contains a duplicate email")
        normalized_emails.add(email)
        target_id = _typeid(member["target_practice_id"], "practice")
        target_ids.add(str(target_id))
        assignments.append(
            RosterAssignmentInput(
                email=email,
                target_practice_id=target_id,
            )
        )
    if len(target_ids) < 2:
        raise typer.BadParameter(
            "multi-practice manifest must target at least two practices"
        )
    return assignments, slug, domain, hashlib.sha256(raw).hexdigest()


@app.command("bootstrap-provider-practice")
def bootstrap_provider_practice_command(
    manifest: str = typer.Option(..., help="Private schema-version-1 JSON roster."),
    owner_doctor_id: str = typer.Option(
        ..., help="Existing Doctor TypeID for the explicitly designated owner."
    ),
    legal_name: str = typer.Option(..., help="Covered entity's legal name."),
    email_domain: str = typer.Option(..., help="Exact approved email domain."),
    bootstrap_ref: str = typer.Option(
        ..., help="Stable unique reference used for idempotent retries."
    ),
    operator_ref: str = typer.Option(..., help="Durable operator/ticket reference."),
    reason: str = typer.Option(..., help="Reason for the privileged bootstrap."),
    confirm_roster_sha256: str | None = typer.Option(
        None,
        "--confirm-roster-sha256",
        help="Raw roster digest printed by dry-run; required with --apply.",
    ),
    confirm_plan_sha256: str | None = typer.Option(
        None,
        "--confirm-plan-sha256",
        help="Canonical plan digest printed by dry-run; required with --apply.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the reviewed plan."),
) -> None:
    """Create a closed practice shell and one owner; dry-run by default.

    This command never records a BAA, grants entitlement, imports roster
    authorization, calls Stripe, or prints an email address or join code.
    """

    from app.services.clerk_identity import verified_primary_email
    from app.services.practice_bootstrap import (
        ProviderPracticeBootstrapError,
        bootstrap_provider_practice,
        plan_provider_practice_bootstrap,
    )

    from sqlalchemy.exc import SQLAlchemyError

    entries, roster_sha256 = _read_roster_manifest(manifest)
    owner_id = _typeid(owner_doctor_id, "doctor")
    # Always prompt so the confirming address is absent from shell history,
    # process listings, audit rows, and normal command output.
    owner_email = typer.prompt("Exact owner account email", hide_input=True)
    try:
        with global_session():
            owner = _doctor_by_id(owner_doctor_id)
            live_primary_email = verified_primary_email(owner.clerk_id)
            plan = plan_provider_practice_bootstrap(
                entries=entries,
                roster_sha256=roster_sha256,
                owner_doctor_id=owner_id,
                owner_email_confirmation=owner_email,
                verified_primary_email=live_primary_email,
                legal_name=legal_name,
                email_domain=email_domain,
                bootstrap_ref=bootstrap_ref,
                operator_ref=operator_ref,
                reason=reason,
            )
            if not apply:
                state = "already applied" if plan.already_applied else "valid"
                typer.echo(
                    "DRY RUN: provider practice bootstrap "
                    f"{state}; owner_doctor_id={plan.owner_doctor_id} "
                    f"members={plan.roster_member_count} "
                    f"roster_sha256={plan.roster_sha256} "
                    f"plan_sha256={plan.plan_sha256}."
                )
                if plan.already_applied:
                    typer.echo(
                        "Existing bootstrap evidence: "
                        f"practice_id={plan.practice_id} member_id={plan.member_id} "
                        f"audit={plan.audit_id}."
                    )
                return
            if not confirm_roster_sha256 or not confirm_plan_sha256:
                raise typer.BadParameter(
                    "--apply requires --confirm-roster-sha256 and --confirm-plan-sha256"
                )
            result = bootstrap_provider_practice(
                entries=entries,
                roster_sha256=roster_sha256,
                owner_doctor_id=owner_id,
                owner_email_confirmation=owner_email,
                verified_primary_email=live_primary_email,
                legal_name=legal_name,
                email_domain=email_domain,
                bootstrap_ref=bootstrap_ref,
                operator_ref=operator_ref,
                reason=reason,
                confirm_roster_sha256=confirm_roster_sha256,
                confirm_plan_sha256=confirm_plan_sha256,
            )
            state = "already applied" if result.already_applied else "applied"
            typer.echo(
                "Provider practice bootstrap "
                f"{state}: practice_id={result.practice.id} "
                f"owner_member_id={result.owner_member.id} audit={result.audit.id} "
                f"roster_sha256={result.roster_sha256} "
                f"plan_sha256={result.plan_sha256}."
            )
            typer.echo(
                "Practice remains closed until negotiated BAA, entitlement, "
                "organization roster, and code claim are all in force."
            )
    except ProviderPracticeBootstrapError as exc:
        raise typer.BadParameter(str(exc)) from None
    except SQLAlchemyError:
        if apply:
            raise typer.BadParameter(
                "provider practice bootstrap outcome is ambiguous; verify database "
                "state before retrying; no identities were printed"
            ) from None
        raise typer.BadParameter(
            "provider practice bootstrap database validation failed; no identities "
            "were printed"
        ) from None


@app.command("grant-pilot-access")
def grant_pilot_access_command(
    source_ref: str = typer.Option(
        ..., help="Opaque reference for the approved Clerk cohort export."
    ),
    source_sha256: str = typer.Option(
        ...,
        "--source-sha256",
        help=(
            "Canonical Clerk-subject and normalized-email pair digest of the "
            "approved whole-instance cohort for this environment."
        ),
    ),
    operator_ref: str = typer.Option(..., help="Durable operator/ticket reference."),
    reason: str = typer.Option(..., help="Reason for the grandfather access grant."),
    confirm_plan_sha256: str | None = typer.Option(
        None,
        "--confirm-plan-sha256",
        help="Canonical plan digest printed by dry-run; required with --apply.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the reviewed plan."),
) -> None:
    """Grant the exact verified environment cohort; dry-run by default."""

    from app.services.clerk_cohort import resolve_live_clerk_cohort
    from app.services.pilot_access_grandfather import (
        GrandfatherGrantInput,
        grant_pilot_access_grants,
        plan_pilot_access_grants,
        required_pilot_access_grandfather_account_count,
    )

    if apply and not confirm_plan_sha256:
        raise typer.BadParameter("--apply requires --confirm-plan-sha256")
    try:
        expected_count = required_pilot_access_grandfather_account_count()
        identities = resolve_live_clerk_cohort(
            expected_count=expected_count,
            expected_pair_sha256=source_sha256,
        )
        entries = [
            GrandfatherGrantInput(
                clerk_id=identity.clerk_id,
                email=identity.email,
            )
            for identity in identities
        ]
        plan = plan_pilot_access_grants(
            entries,
            source_ref=source_ref,
            source_sha256=source_sha256,
            operator_ref=operator_ref,
            reason=reason,
        )
        if not apply:
            state = "already applied" if plan.already_applied else "deployable"
            typer.echo(
                "DRY RUN: pilot access grandfather plan; "
                f"state={state} accounts={plan.account_count} "
                f"existing_doctors={plan.existing_doctor_count} "
                f"prelogin_accounts={plan.prelogin_account_count} "
                f"already_granted={plan.already_granted_count} "
                f"source_sha256={source_sha256.strip().lower()} "
                f"plan_sha256={plan.plan_sha256}."
            )
            return
        result = grant_pilot_access_grants(
            entries,
            source_ref=source_ref,
            source_sha256=source_sha256,
            operator_ref=operator_ref,
            reason=reason,
            confirm_plan_sha256=cast(str, confirm_plan_sha256),
        )
    except Exception:
        outcome = "outcome is ambiguous" if apply else "validation failed"
        raise typer.BadParameter(
            f"pilot access grant {outcome}; verify aggregate database state; "
            "no identities were printed"
        ) from None

    state = "already applied" if result.created_count == 0 else "applied"
    typer.echo(
        "Pilot access grandfather grant "
        f"{state}: accounts={len(result.grants)} created={result.created_count} "
        f"unchanged={result.unchanged_count} "
        f"source_sha256={source_sha256.strip().lower()} "
        f"plan_sha256={result.plan_sha256}."
    )


@app.command("amend-pilot-continuity")
def amend_pilot_continuity_command(
    manifest: str = typer.Option(
        ...,
        help=(
            "Private mode-0600, environment-specific continuity manifest. "
            "Identity values are never printed."
        ),
    ),
    source_ref: str = typer.Option(
        ..., help="Opaque reference for the reviewed omission source."
    ),
    operator_ref: str = typer.Option(..., help="Durable operator/ticket reference."),
    reason: str = typer.Option(..., help="Reason for the continuity amendment."),
    confirm_plan_sha256: str | None = typer.Option(
        None,
        "--confirm-plan-sha256",
        help="Canonical plan digest printed by dry-run; required with --apply.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the reviewed amendment."),
) -> None:
    """Add reviewed omissions without changing the fixed base cohort."""

    from app.services.continuity_reconciliation import (
        apply_continuity_amendment,
        plan_continuity_amendment,
        read_private_continuity_manifest,
    )

    if apply and not confirm_plan_sha256:
        raise typer.BadParameter("--apply requires --confirm-plan-sha256")
    try:
        parsed = read_private_continuity_manifest(manifest)
        plan = plan_continuity_amendment(
            parsed,
            source_ref=source_ref,
            operator_ref=operator_ref,
            reason=reason,
        )
        if not apply:
            state = "already applied" if plan.already_applied else "deployable"
            typer.echo(
                "DRY RUN: pilot continuity amendment; "
                f"state={state} accounts={plan.account_count} "
                f"existing_doctors={plan.existing_doctor_count} "
                f"manifest_sha256={plan.manifest_sha256} "
                f"plan_sha256={plan.plan_sha256}."
            )
            return
        result = apply_continuity_amendment(
            parsed,
            source_ref=source_ref,
            operator_ref=operator_ref,
            reason=reason,
            confirm_plan_sha256=cast(str, confirm_plan_sha256),
        )
    except Exception:
        outcome = "outcome is ambiguous" if apply else "validation failed"
        raise typer.BadParameter(
            f"pilot continuity amendment {outcome}; verify aggregate database state; "
            "no identities were printed"
        ) from None

    state = "already applied" if result.created_count == 0 else "applied"
    typer.echo(
        "Pilot continuity amendment "
        f"{state}: accounts={result.account_count} created={result.created_count} "
        f"unchanged={result.unchanged_count} "
        f"manifest_sha256={result.manifest_sha256} "
        f"plan_sha256={result.plan_sha256}."
    )


@app.command("revoke-pilot-access")
def revoke_pilot_access_command(
    operator_ref: str = typer.Option(..., help="Durable operator/ticket reference."),
    reason: str = typer.Option(..., help="Reason for the privileged revocation."),
    confirm_plan_sha256: str | None = typer.Option(
        None,
        "--confirm-plan-sha256",
        help="Canonical plan digest printed by dry-run; required with --apply.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the revocation."),
) -> None:
    """Revoke one grandfather grant; identity inputs are prompted privately."""

    import hashlib
    import hmac

    from app.services.organization_access import normalize_email
    from app.services.pilot_access_grandfather import (
        pilot_access_revoke_plan_sha256,
        revoke_pilot_access_grants,
    )
    from app.services.superadmin_capability import (
        get_active_runtime_superadmin_capability_kinds,
    )

    from activemodel.session_manager import get_session
    from app.models.doctor import Doctor
    from app.models.pilot_access_grandfather_grant import (
        PilotAccessGrandfatherGrant,
    )

    if apply and not confirm_plan_sha256:
        raise typer.BadParameter("--apply requires --confirm-plan-sha256")
    clerk_id = typer.prompt("Exact Clerk subject", hide_input=True)
    confirmed_email = typer.prompt("Exact account email", hide_input=True)
    try:
        email = normalize_email(confirmed_email)
        plan_sha256 = pilot_access_revoke_plan_sha256(
            [clerk_id],
            operator_ref=operator_ref,
            reason=reason,
        )
        with global_session():
            grants = cast(
                list[PilotAccessGrandfatherGrant],
                list(
                    PilotAccessGrandfatherGrant.where(
                        PilotAccessGrandfatherGrant.clerk_id == clerk_id.strip()
                    ).all()
                ),
            )
            doctor = Doctor.get(clerk_id=clerk_id.strip())
        active = [grant for grant in grants if grant.revoked_at is None]
        target = (
            active[0]
            if len(active) == 1
            else max(
                (grant for grant in grants if grant.revoked_at is not None),
                key=lambda grant: cast(datetime, grant.revoked_at),
                default=None,
            )
        )
        email_sha256 = hashlib.sha256(email.encode("utf-8")).hexdigest()
        if (
            target is None
            or len(active) > 1
            or target.email_snapshot != email
            or not hmac.compare_digest(target.email_sha256, email_sha256)
        ):
            raise ValueError("grant confirmation failed")
        if target.revoked_at is not None and (
            target.revoked_by_ref != operator_ref.strip()
            or target.revocation_reason != reason.strip()
            or target.revocation_plan_sha256 is None
            or not hmac.compare_digest(
                target.revocation_plan_sha256,
                plan_sha256,
            )
        ):
            raise ValueError("revocation plan conflicts with retained evidence")
        if doctor is not None and doctor.deleted_at is None:
            with get_session() as session:
                protected_capabilities = get_active_runtime_superadmin_capability_kinds(
                    session,
                    subject=doctor.clerk_id,
                    doctor_id=doctor.id,
                )
            if protected_capabilities:
                raise ValueError(
                    "active superadmin capabilities require a separate lifecycle"
                )
        if not apply:
            state = "already revoked" if target.revoked_at is not None else "revocable"
            typer.echo(
                "DRY RUN: pilot access grant "
                f"state={state}; accounts=1 plan_sha256={plan_sha256}; "
                "no clinical or content records will be deleted."
            )
            return
        result = revoke_pilot_access_grants(
            [clerk_id],
            operator_ref=operator_ref,
            reason=reason,
            confirm_plan_sha256=cast(str, confirm_plan_sha256),
        )
    except Exception:
        outcome = "outcome is ambiguous" if apply else "validation failed"
        raise typer.BadParameter(
            f"pilot access revocation {outcome}; verify aggregate database state; "
            "no identities were printed"
        ) from None

    retain_until = result.grants[0].retain_until
    state = "already revoked" if result.revoked_count == 0 else "revoked"
    typer.echo(
        f"Pilot access grant {state}: "
        f"accounts=1 revoked={result.revoked_count} "
        f"unchanged={result.unchanged_count} "
        f"retain_until={retain_until.isoformat() if retain_until else 'missing'} "
        f"plan_sha256={result.plan_sha256}; no clinical or content records "
        "were deleted."
    )


@app.command("audit-pilot-access")
def audit_pilot_access_command() -> None:
    """Report aggregate base, amendment, revocation, and rebind integrity."""

    from app.services.pilot_access_audit import audit_pilot_access_state

    try:
        report = audit_pilot_access_state()
    except Exception:
        raise typer.BadParameter(
            "pilot access audit failed; no identities were printed"
        ) from None
    typer.echo(json.dumps(report.payload, separators=(",", ":"), sort_keys=True))
    if not report.valid:
        raise typer.Exit(code=1)


@app.command("import-organization-roster")
def import_organization_roster_command(
    manifest: str = typer.Option(..., help="Private schema-version-1 JSON roster."),
    practice_id: str = typer.Option(..., help="Stable target practice TypeID."),
    operator_ref: str = typer.Option(..., help="Durable operator/ticket reference."),
    reason: str = typer.Option(..., help="Reason for the privileged change."),
    source_ref: str = typer.Option(
        ..., help="Stable reference for the approved roster."
    ),
    confirm_sha256: str | None = typer.Option(
        None,
        "--confirm-sha256",
        help="Raw manifest SHA-256 printed by dry-run; required with --apply.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the roster approvals."),
) -> None:
    """Validate or import exact-email roster approvals without printing emails."""

    from app.services.organization_access import (
        email_matches_exact_domain,
        import_roster_approvals,
    )

    from app.models.organization_access import ProviderOrganization

    entries, source_sha256 = _read_roster_manifest(manifest)
    _validate_operator_change(operator_ref, reason)
    if not source_ref.strip() or len(source_ref.strip()) > 255:
        raise typer.BadParameter("source-ref must contain 1 to 255 characters")
    with global_session():
        practice = _practice_by_id(practice_id)
        if practice.organization_id is None:
            raise typer.BadParameter("practice is not linked to an organization")
        organization = ProviderOrganization.get(id=practice.organization_id)
        if organization is None or organization.status != "active":
            raise typer.BadParameter("practice organization is not active")
        if not practice.negotiated_baa_in_force():
            raise typer.BadParameter("an in-force negotiated BAA is required")
        if not practice.billing_exemption_active():
            raise typer.BadParameter("an active billing exemption is required")
        if any(
            not email_matches_exact_domain(entry.email, organization.email_domain)
            for entry in entries
        ):
            raise typer.BadParameter(
                "one or more roster emails do not match the organization domain"
            )

        if not apply:
            typer.echo(
                "DRY RUN: roster is valid; "
                f"practice_id={practice.id} members={len(entries)} "
                f"sha256={source_sha256}."
            )
            return
        confirmed = (confirm_sha256 or "").strip().lower()
        if confirmed != source_sha256:
            raise typer.BadParameter(
                "--apply requires --confirm-sha256 matching the dry-run digest"
            )
        try:
            result = import_roster_approvals(
                practice,
                entries,
                operator_ref=operator_ref,
                reason=reason,
                source_ref=source_ref,
                source_sha256=source_sha256,
            )
        except Exception:
            # Service and database errors can include an address; do not echo them.
            raise typer.BadParameter(
                "roster import failed validation; no email addresses were printed"
            ) from None
        typer.echo(
            "Roster import applied: "
            f"practice_id={practice.id} created={result.created} "
            f"unchanged={result.unchanged} sha256={source_sha256}."
        )


@app.command("offboard-practice-member")
def offboard_practice_member_command(
    practice_id: str = typer.Option(..., help="Stable target practice TypeID."),
    doctor_id: str = typer.Option(..., help="Stable doctor TypeID."),
    confirm_email: str = typer.Option(
        ..., help="Exact account email confirmation; never written to audit output."
    ),
    operator_ref: str = typer.Option(..., help="Durable operator/ticket reference."),
    reason: str = typer.Option(..., help="Reason for the privileged change."),
    apply: bool = typer.Option(False, "--apply", help="Apply the revocation."),
) -> None:
    """Revoke access and retain its authorization evidence for seven years."""

    from app.services.organization_access import (
        OrganizationAccessError,
        normalize_email,
        offboard_member,
    )

    from app.models.practice import PracticeMember

    _validate_operator_change(operator_ref, reason)
    with global_session():
        practice = _practice_by_id(practice_id)
        doctor = _doctor_by_id(doctor_id)
        try:
            confirmed_email = normalize_email(confirm_email)
            stored_email = normalize_email(doctor.email or "")
        except OrganizationAccessError as exc:
            raise typer.BadParameter("email confirmation is invalid") from exc
        if confirmed_email != stored_email:
            raise typer.BadParameter("email confirmation does not match the account")
        member = PracticeMember.get(practice_id=practice.id, doctor_id=doctor.id)
        if member is None:
            raise typer.BadParameter("doctor is not a member of the target practice")
        if (
            member.access_revoked_at is not None
            and member.access_records_retain_until is None
        ):
            raise typer.BadParameter("revoked member is missing its retention hold")

        if not apply:
            state = "already revoked" if member.access_revoked_at else "can be revoked"
            typer.echo(
                "DRY RUN: practice member "
                f"member_id={member.id} {state}; no records will be deleted."
            )
            return
        try:
            member, retain_until, audit = offboard_member(
                practice,
                doctor,
                operator_ref=operator_ref,
                reason=reason,
            )
        except OrganizationAccessError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(
            "Practice member access revoked: "
            f"member_id={member.id} retain_until={retain_until.isoformat()} "
            f"audit={audit.id}; no clinical or content records were deleted."
        )


@app.command("revoke-organization-roster-access")
def revoke_organization_roster_access_command(
    practice_id: str = typer.Option(..., help="Stable target practice TypeID."),
    approved_email: str | None = typer.Option(
        None,
        help=(
            "Exact roster email. Omit this option to enter it interactively and "
            "keep it out of shell history."
        ),
    ),
    operator_ref: str = typer.Option(..., help="Durable operator/ticket reference."),
    reason: str = typer.Option(..., help="Reason for the privileged revocation."),
    apply: bool = typer.Option(False, "--apply", help="Apply the revocation."),
) -> None:
    """Revoke claimed or unclaimed roster access; dry-run unless --apply."""

    from app.services.organization_access import (
        OrganizationAccessError,
        normalize_email,
        revoke_roster_access,
    )

    from app.models.organization_access import OrganizationRosterApproval

    _validate_operator_change(operator_ref, reason)
    raw_email = approved_email or typer.prompt("Exact approved roster email")
    try:
        email = normalize_email(raw_email)
    except OrganizationAccessError as exc:
        raise typer.BadParameter("approved email is invalid") from exc

    with global_session():
        practice = _practice_by_id(practice_id)
        if practice.organization_id is None:
            raise typer.BadParameter("practice is not linked to an organization")
        approvals = cast(
            list[OrganizationRosterApproval],
            list(
                OrganizationRosterApproval.where(
                    OrganizationRosterApproval.organization_id
                    == practice.organization_id,
                    OrganizationRosterApproval.target_practice_id == practice.id,
                    OrganizationRosterApproval.approved_email == email,
                ).all()
            ),
        )
        if not approvals:
            raise typer.BadParameter(
                "no roster approval matches the target practice and email"
            )
        active = [approval for approval in approvals if approval.revoked_at is None]
        target = (
            active[0]
            if active
            else max(
                approvals,
                key=lambda row: cast(datetime, row.created_at),
            )
        )
        if not apply:
            state = (
                "already revoked"
                if target.revoked_at is not None
                else "claimed"
                if target.claimed_doctor_id is not None
                else "unclaimed"
            )
            typer.echo(
                "DRY RUN: roster approval "
                f"approval_id={target.id} state={state}; no records will be deleted."
            )
            return
        try:
            result = revoke_roster_access(
                practice,
                email,
                operator_ref=operator_ref,
                reason=reason,
            )
        except OrganizationAccessError as exc:
            raise typer.BadParameter(str(exc)) from exc
        member_ref = str(result.member.id) if result.member is not None else "unclaimed"
        typer.echo(
            "Organization roster access revoked: "
            f"approval_id={result.approval.id} member_id={member_ref} "
            f"retain_until={result.retain_until.isoformat()} "
            f"audit={result.audit.id}; no clinical or content records were deleted."
        )


@app.command("grant-superadmin-capabilities")
def grant_superadmin_capabilities_command(
    manifest: Path | None = typer.Option(
        None,
        "--manifest",
        help=(
            "Owner-only mode-0600 JSON manifest. Omit to enter one account's "
            "identity fields in hidden prompts."
        ),
    ),
    source_ref: str = typer.Option(
        ...,
        help="Opaque controlled-manifest reference; never enter an email or name.",
    ),
    operator_ref: str = typer.Option(
        ...,
        help="Opaque operator/ticket reference; never enter an email or name.",
    ),
    reason_code: str = typer.Option(
        ...,
        help="Non-sensitive lowercase audit reason code.",
    ),
    confirm_plan_sha256: str | None = typer.Option(
        None,
        "--confirm-plan-sha256",
        help="Exact digest printed by the dry run; required with --apply.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the reviewed plan."),
) -> None:
    """Grant exact identity-bound capabilities; dry-run by default."""

    from app.services.superadmin_capability import (
        grant_superadmin_capabilities,
        plan_superadmin_capability_grants,
    )

    if apply and not confirm_plan_sha256:
        raise typer.BadParameter("--apply requires --confirm-plan-sha256")
    try:
        private_manifest = _superadmin_capability_manifest_from_input(manifest)
        plan = plan_superadmin_capability_grants(
            private_manifest,
            source_ref=source_ref,
            operator_ref=operator_ref,
            reason_code=reason_code,
        )
        if not apply:
            state = "already_applied" if plan.already_applied else "deployable"
            typer.echo(
                "DRY RUN: superadmin capability grant "
                f"state={state} environment={private_manifest.environment.value} "
                f"accounts={plan.account_count} capabilities={plan.capability_count} "
                f"platform_admin={plan.platform_admin_count} "
                f"view_as={plan.view_as_count} "
                f"already_applied={plan.already_applied_count} "
                f"source_sha256={plan.source_sha256} "
                f"plan_sha256={plan.plan_sha256}."
            )
            return
        result = grant_superadmin_capabilities(
            private_manifest,
            source_ref=source_ref,
            operator_ref=operator_ref,
            reason_code=reason_code,
            confirm_plan_sha256=cast(str, confirm_plan_sha256),
        )
    except Exception:
        outcome = "outcome is ambiguous" if apply else "validation failed"
        raise typer.BadParameter(
            f"superadmin capability grant {outcome}; verify aggregate database "
            "state; no identities were printed"
        ) from None
    state = "already_applied" if result.created_count == 0 else "applied"
    typer.echo(
        "Superadmin capability grant "
        f"state={state} capabilities={result.capability_count} "
        f"created={result.created_count} unchanged={result.unchanged_count} "
        f"source_sha256={result.source_sha256} "
        f"plan_sha256={result.plan_sha256}."
    )


@app.command("revoke-superadmin-capabilities")
def revoke_superadmin_capabilities_command(
    manifest: Path | None = typer.Option(
        None,
        "--manifest",
        help=(
            "Owner-only mode-0600 JSON manifest. Omit to enter one account's "
            "identity fields in hidden prompts."
        ),
    ),
    source_ref: str = typer.Option(
        ...,
        help="Opaque controlled-manifest reference; never enter an email or name.",
    ),
    operator_ref: str = typer.Option(
        ...,
        help="Opaque operator/ticket reference; never enter an email or name.",
    ),
    reason_code: str = typer.Option(
        ...,
        help="Non-sensitive lowercase audit reason code.",
    ),
    confirm_plan_sha256: str | None = typer.Option(
        None,
        "--confirm-plan-sha256",
        help="Exact digest printed by the dry run; required with --apply.",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the revocation."),
) -> None:
    """Revoke exact capabilities without depending on Clerk availability."""

    from app.services.superadmin_capability import (
        plan_superadmin_capability_revocations,
        revoke_superadmin_capabilities,
    )

    if apply and not confirm_plan_sha256:
        raise typer.BadParameter("--apply requires --confirm-plan-sha256")
    try:
        private_manifest = _superadmin_capability_manifest_from_input(manifest)
        plan = plan_superadmin_capability_revocations(
            private_manifest,
            source_ref=source_ref,
            operator_ref=operator_ref,
            reason_code=reason_code,
        )
        if not apply:
            state = "already_revoked" if plan.already_applied else "revocable"
            typer.echo(
                "DRY RUN: superadmin capability revocation "
                f"state={state} environment={private_manifest.environment.value} "
                f"accounts={plan.account_count} capabilities={plan.capability_count} "
                f"platform_admin={plan.platform_admin_count} "
                f"view_as={plan.view_as_count} "
                f"already_revoked={plan.already_applied_count} "
                f"source_sha256={plan.source_sha256} "
                f"plan_sha256={plan.plan_sha256}."
            )
            return
        result = revoke_superadmin_capabilities(
            private_manifest,
            source_ref=source_ref,
            operator_ref=operator_ref,
            reason_code=reason_code,
            confirm_plan_sha256=cast(str, confirm_plan_sha256),
        )
    except Exception:
        outcome = "outcome is ambiguous" if apply else "validation failed"
        raise typer.BadParameter(
            f"superadmin capability revocation {outcome}; verify aggregate "
            "database state; no identities were printed"
        ) from None
    state = "already_revoked" if result.revoked_count == 0 else "revoked"
    typer.echo(
        "Superadmin capability revocation "
        f"state={state} capabilities={result.capability_count} "
        f"revoked={result.revoked_count} unchanged={result.unchanged_count} "
        f"source_sha256={result.source_sha256} "
        f"plan_sha256={result.plan_sha256}; no records were deleted."
    )


@app.command("list-superadmin-capabilities")
def list_superadmin_capabilities_command() -> None:
    """Print only aggregate capability counts and a pseudonymous digest."""

    from app.services.superadmin_capability import (
        configured_superadmin_capability_environment,
        list_superadmin_capability_summary,
    )

    try:
        summary = list_superadmin_capability_summary(
            configured_superadmin_capability_environment()
        )
    except Exception:
        raise typer.BadParameter(
            "superadmin capability listing failed; no identities were printed"
        ) from None
    typer.echo(
        json.dumps(
            {
                "active_binding_sha256": summary.active_binding_sha256,
                "active_count": summary.active_count,
                "audit_count": summary.audit_count,
                "environment": summary.environment.value,
                "platform_admin_active_count": (summary.platform_admin_active_count),
                "revoked_count": summary.revoked_count,
                "total_count": summary.total_count,
                "view_as_active_count": summary.view_as_active_count,
            },
            sort_keys=True,
        )
    )


@app.command("restore-visit-note-version")
def restore_visit_note_version_command(
    note_id: str | None = typer.Option(
        None,
        "--note-id",
        help=(
            "Superseded visit-note TypeID to restore. Omit to enter it in a "
            "hidden prompt and keep it out of shell history."
        ),
    ),
    operator_ref: str = typer.Option(
        ...,
        help="Opaque operator or ticket reference; do not enter a name or email.",
    ),
    reason_code: str = typer.Option(
        ...,
        help=(
            "One of: operator_selected_prior_version, clinical_correction, "
            "incident_recovery."
        ),
    ),
    confirm_plan_sha256: str | None = typer.Option(
        None,
        help="Exact digest printed by the dry run; required with --apply.",
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Apply the current-version swap."
    ),
) -> None:
    """Restore one retained note version without deleting any version."""

    from app.services.ai_visit_note_versions import (
        VisitNoteRestoreReason,
        VisitNoteVersionError,
        plan_visit_note_restore,
        restore_visit_note_version,
    )

    raw_note_id = note_id or typer.prompt(
        "Superseded visit-note TypeID", hide_input=True
    )
    try:
        target_note_id = _typeid(raw_note_id, "visit_note")
        reason = VisitNoteRestoreReason(reason_code)
    except (typer.BadParameter, ValueError) as exc:
        raise typer.BadParameter(
            "note id or reason code is invalid; no clinical identifiers were printed"
        ) from exc

    if apply and not confirm_plan_sha256:
        raise typer.BadParameter("--apply requires --confirm-plan-sha256")

    try:
        with global_session():
            if not apply:
                plan = plan_visit_note_restore(
                    target_note_id=target_note_id,
                    operator_ref=operator_ref,
                    reason=reason,
                )
                typer.echo(
                    "DRY RUN: retained note version can be restored; "
                    f"versions={plan.version_count} "
                    f"current_ref={plan.current_note_ref} "
                    f"target_ref={plan.target_note_ref} "
                    f"plan_sha256={plan.plan_sha256}; no records changed."
                )
                return

            result = restore_visit_note_version(
                target_note_id=target_note_id,
                operator_ref=operator_ref,
                reason=reason,
                confirm_plan_sha256=cast(str, confirm_plan_sha256),
            )
    except VisitNoteVersionError:
        typer.echo(
            "Restore refused: note-version state or confirmation is invalid; "
            "no clinical identifiers were printed.",
            err=True,
        )
        raise typer.Exit(1) from None
    except Exception:
        if apply:
            typer.echo(
                "Restore outcome unknown: verify the append-only audit and "
                "current note state before retrying; no clinical identifiers "
                "or database details were printed.",
                err=True,
            )
        else:
            typer.echo(
                "Restore plan unavailable due to an unexpected failure; no "
                "clinical identifiers or database details were printed.",
                err=True,
            )
        raise typer.Exit(1) from None

    state = "applied" if result.changed else "already applied"
    typer.echo(
        "Visit-note restore "
        f"{state}: audit={result.audit_id} "
        f"plan_sha256={result.plan_sha256}; no note versions were deleted."
    )


if __name__ == "__main__":
    app()
