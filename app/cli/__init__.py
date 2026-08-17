import json
from typing import cast

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


if __name__ == "__main__":
    app()
