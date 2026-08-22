import json
from datetime import datetime, timedelta, timezone
from typing import Literal, cast

import typer
from fastapi.routing import APIRoute
from typeid import TypeID

from activemodel.session_manager import global_session
from activemodel.types import TypeIDType

app = typer.Typer()


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
    join_code: str = typer.Option(..., help="The practice's join code."),
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
        practice = _practice_by_join_code(join_code)
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


@app.command("set-negotiated-baa")
def set_negotiated_baa(
    join_code: str = typer.Option(..., help="The practice's join code."),
    reference: str = typer.Option(
        ..., help="Document reference for the countersigned instrument."
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
        practice = _practice_by_join_code(join_code)
        effective_at = _utc_date(effective) if effective else datetime.now(timezone.utc)
        try:
            audit = record_negotiated_baa(
                practice,
                operator_ref=operator_ref,
                reason=reason,
                document_reference=reference,
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


if __name__ == "__main__":
    app()
