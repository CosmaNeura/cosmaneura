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
        practice = _practice_by_join_code(join_code)
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
        except Exception as exc:
            Path(access_code_output).unlink(missing_ok=True)
            if isinstance(exc, OrganizationAccessError):
                raise typer.BadParameter(str(exc)) from exc
            raise
        typer.echo(
            "Provider organization configured: "
            f"organization_id={organization.id} practice_id={practice.id} "
            f"audit={audit.id}."
        )
        typer.echo("One-time access code written to the private output file.")


@app.command("rotate-organization-code")
def rotate_organization_code_command(
    organization_slug: str = typer.Option(..., help="Organization slug."),
    practice_id: str = typer.Option(..., help="Stable target practice TypeID."),
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

    _validate_operator_change(operator_ref, reason)
    with global_session():
        practice = _practice_by_id(practice_id)
        provider_organization = _organization_by_slug(organization_slug)
        if practice.organization_id != provider_organization.id:
            raise typer.BadParameter(
                "target practice is not linked to the supplied organization"
            )
        if not apply:
            typer.echo(
                "DRY RUN: organization access code can be rotated "
                f"for organization_id={provider_organization.id}; "
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
            access_code, audit = rotate_access_code(
                provider_organization,
                practice=practice,
                operator_ref=operator_ref,
                reason=reason,
                access_code=access_code,
            )
        except Exception as exc:
            Path(access_code_output).unlink(missing_ok=True)
            if isinstance(exc, OrganizationAccessError):
                raise typer.BadParameter(str(exc)) from exc
            raise
        typer.echo(
            "Organization access code rotated: "
            f"organization_id={provider_organization.id} audit={audit.id}."
        )
        typer.echo("One-time access code written to the private output file.")


def _read_roster_manifest(path_value: str):
    """Read a private JSON manifest and return entries plus its raw-file digest."""

    import hashlib
    import os
    import stat
    from pathlib import Path

    from app.services.organization_access import (
        OrganizationAccessError,
        RosterApprovalInput,
        normalize_email,
    )

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

    from app.services.organization_access import import_roster_approvals
    from app.utils.email_domain import email_matches_domain

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
            not email_matches_domain(entry.email, organization.email_domain)
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
        except Exception as exc:
            # Service and database errors can include an address; do not echo them.
            raise typer.BadParameter(
                "roster import failed validation; no email addresses were printed"
            ) from exc
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


if __name__ == "__main__":
    app()
