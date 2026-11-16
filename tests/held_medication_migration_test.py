"""The additive status value and patient rows survive the supported recovery rung."""

from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from typeid import TypeID

from activemodel.session_manager import get_engine
from app.models.patient_medication import MedicationStatus, PatientMedication
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.factories import DoctorFactory, PatientFactory


def test_enum_expand_recovery_retains_held_and_legacy_patient_rows(tmp_path: Path):
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    rows = [
        PatientMedication(
            doctor_id=doctor.id,
            patient_id=patient.id,
            name="Synthetic enum recovery medication",
            status=value,
            confirmed=True,
        ).save()
        for value in MedicationStatus
    ]
    expected = {str(row.id): row.status for row in rows}
    # This focused predecessor fixture must never rewind a later retained
    # evidence migration in the application database. Only the byte-preserved
    # 312 migration runs, on an independent connection and private schema.
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("312b217ebba4")
    assert revision is not None
    assert revision.down_revision == "d4de1ee4bb02"
    fixture = tmp_path / "held-only-migrations"
    versions = fixture / "versions"
    versions.mkdir(parents=True)
    (versions / "312_held.py").write_bytes(Path(revision.path).read_bytes())
    assert (versions / "312_held.py").read_bytes() == Path(revision.path).read_bytes()
    (versions / "d4_synthetic_anchor.py").write_text(
        '"""Synthetic predecessor marker; not the production D4 migration."""\n'
        'revision="d4de1ee4bb02"\ndown_revision=None\n'
        "def upgrade(): pass\ndef downgrade(): pass\n"
    )
    (fixture / "env.py").write_text(
        "from alembic import context\n"
        "context.configure(connection=context.config.attributes['connection'], "
        "version_table_schema=context.config.attributes['schema'], "
        "transaction_per_migration=True)\n"
        "with context.begin_transaction():\n    context.run_migrations()\n"
    )
    config = Config()
    config.set_main_option("script_location", str(fixture))
    schema = "held_enum_recovery_" + uuid4().hex
    engine = create_engine(get_engine().url, poolclass=NullPool)
    snapshot_sql = text(
        "SELECT (SELECT version_num FROM public.alembic_version), "
        "(SELECT count(*) FROM public.pilot_access_practice_binding), "
        "(SELECT tgenabled FROM pg_trigger WHERE tgrelid='public.pilot_access_practice_binding'::regclass "
        "AND tgname='pilot_access_practice_binding_reject_mutation'), "
        "(SELECT array_agg(enumlabel ORDER BY enumsortorder) FROM pg_enum "
        "WHERE enumtypid='public.medicationstatus'::regtype)"
    )
    created = False
    before = None
    try:
        with engine.connect() as connection:
            before = tuple(connection.execute(snapshot_sql).one())
            assert before[0] == script.get_current_head()
            assert before[2:] == ("O", ["active", "stopped", "held"])
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.execute(
                text("CREATE TYPE medicationstatus AS ENUM ('active','stopped')")
            )
            connection.execute(
                text(
                    "CREATE TABLE patient_medication (id UUID PRIMARY KEY, status medicationstatus NOT NULL)"
                )
            )
            insert = text(
                "INSERT INTO patient_medication(id,status) VALUES (:id,:status)"
            )
            for row in rows:
                if row.status != MedicationStatus.held:
                    connection.execute(
                        insert,
                        {
                            "id": TypeID.from_string(str(row.id)).uuid,
                            "status": row.status.value,
                        },
                    )
            connection.commit()
            config.attributes.update(connection=connection, schema=schema)
            command.stamp(config, "d4de1ee4bb02")

            def catalog():
                return (
                    connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one(),
                    connection.execute(
                        text(
                            "SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_type.oid=enumtypid "
                            "JOIN pg_namespace ON pg_namespace.oid=typnamespace "
                            "WHERE typname='medicationstatus' AND nspname=:schema ORDER BY enumsortorder"
                        ),
                        {"schema": schema},
                    )
                    .scalars()
                    .all(),
                )

            def retained():
                statuses = {
                    key: value
                    for key, value in connection.execute(
                        text("SELECT id,status FROM patient_medication")
                    ).all()
                }
                return {
                    str(row.id): statuses[TypeID.from_string(str(row.id)).uuid]
                    for row in rows
                }

            assert catalog() == ("d4de1ee4bb02", ["active", "stopped"])
            connection.commit()
            command.upgrade(config, "312b217ebba4")
            held = next(row for row in rows if row.status == MedicationStatus.held)
            connection.execute(
                insert,
                {
                    "id": TypeID.from_string(str(held.id)).uuid,
                    "status": held.status.value,
                },
            )
            connection.commit()
            assert catalog() == ("312b217ebba4", ["active", "stopped", "held"])
            assert retained() == expected
            connection.commit()
            command.downgrade(config, "d4de1ee4bb02")
            assert catalog() == ("d4de1ee4bb02", ["active", "stopped", "held"])
            assert retained() == expected
            connection.commit()
            command.upgrade(config, "312b217ebba4")
            assert catalog() == ("312b217ebba4", ["active", "stopped", "held"])
            assert retained() == expected
            assert (
                connection.execute(
                    text("SELECT count(*) FROM patient_medication")
                ).scalar_one()
                == 3
            )
            assert tuple(connection.execute(snapshot_sql).one()) == before
    finally:
        try:
            if created:
                with engine.begin() as cleanup:
                    owner = cleanup.execute(
                        text(
                            "SELECT pg_get_userbyid(nspowner)=current_user FROM pg_namespace WHERE nspname=:schema"
                        ),
                        {"schema": schema},
                    ).scalar_one_or_none()
                    if owner is not None:
                        assert owner is True
                        cleanup.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                    assert tuple(cleanup.execute(snapshot_sql).one()) == before
        finally:
            engine.dispose()
    assert {
        str(row.id): PatientMedication.one(id=row.id).status for row in rows
    } == expected
    assert (
        PatientMedication.where(PatientMedication.patient_id == patient.id).count() == 3
    )
