import os
import threading
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import ForeignKeyViolation, UniqueViolation

POSTGRES_URL = os.environ.get("EHR_MAPPING_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    POSTGRES_URL is None,
    reason="requires a disposable migrated PostgreSQL mapping database",
)


def test_partial_unique_fence_serializes_concurrent_active_provisioning() -> None:
    """Only one of two racing operator writes can become active."""

    assert POSTGRES_URL is not None
    postgres_url = POSTGRES_URL
    doctor_id = uuid4()
    patient_id = uuid4()
    designation_id = uuid4()
    record_ref = f"a-195900.E-{uuid4().hex}"
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    with psycopg.connect(postgres_url) as connection:
        connection.execute(
            """
            INSERT INTO doctor (id, clerk_id, email, biography)
            VALUES (%s, %s, %s, NULL)
            """,
            (
                doctor_id,
                f"user_mapping_race_{doctor_id.hex}",
                f"mapping-race-{doctor_id.hex}@example.test",
            ),
        )
        connection.execute(
            """
            INSERT INTO patient (id, doctor_id, name, date_of_birth)
            VALUES (%s, %s, 'Synthetic Race Fixture', %s)
            """,
            (patient_id, doctor_id, datetime(2000, 1, 1, tzinfo=UTC)),
        )
        connection.execute(
            """
            INSERT INTO ehr_sandbox_patient_designation (
                id, doctor_id, patient_id, adapter, environment, practice_ref,
                synthetic_only, designated_by
            ) VALUES (
                %s, %s, %s, 'athena', 'preview', '195900', TRUE,
                'operator_race_designator'
            )
            """,
            (designation_id, doctor_id, patient_id),
        )
        connection.commit()

    def provision(mapping_id) -> None:
        with psycopg.connect(postgres_url) as connection:
            barrier.wait(timeout=5)
            try:
                connection.execute(
                    """
                    INSERT INTO ehr_sandbox_record_mapping (
                        id, doctor_id, patient_id, designation_id, adapter, environment,
                        practice_ref, record_ref, synthetic_only, provisioned_by
                    ) VALUES (
                        %s, %s, %s, %s, 'athena', 'preview', '195900', %s,
                        TRUE, 'operator_race_fixture'
                    )
                    """,
                    (mapping_id, doctor_id, patient_id, designation_id, record_ref),
                )
                connection.commit()
                outcomes.append("active")
            except UniqueViolation:
                connection.rollback()
                outcomes.append("conflict")

    threads = [
        threading.Thread(target=provision, args=(uuid4(),)),
        threading.Thread(target=provision, args=(uuid4(),)),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads)
        assert sorted(outcomes) == ["active", "conflict"]

        with psycopg.connect(postgres_url) as connection:
            assert connection.execute(
                """
                SELECT adapter, environment, practice_ref, synthetic_only, count(*)
                FROM ehr_sandbox_record_mapping
                WHERE doctor_id = %s AND patient_id = %s AND revoked_at IS NULL
                GROUP BY adapter, environment, practice_ref, synthetic_only
                """,
                (doctor_id, patient_id),
            ).fetchone() == ("athena", "preview", "195900", True, 1)
    finally:
        with psycopg.connect(postgres_url) as connection:
            connection.execute(
                "DELETE FROM ehr_sandbox_record_mapping WHERE doctor_id = %s",
                (doctor_id,),
            )
            connection.execute(
                "DELETE FROM ehr_sandbox_patient_designation WHERE doctor_id = %s",
                (doctor_id,),
            )
            connection.execute("DELETE FROM patient WHERE id = %s", (patient_id,))
            connection.execute("DELETE FROM doctor WHERE id = %s", (doctor_id,))
            connection.commit()


def test_database_fences_designation_and_mapping_to_exact_doctor_patient_scope() -> (
    None
):
    """Independent valid foreign keys cannot be recombined across scopes."""

    assert POSTGRES_URL is not None
    doctor_a = uuid4()
    doctor_b = uuid4()
    patient_a = uuid4()
    patient_b = uuid4()
    designation_a = uuid4()
    designation_b = uuid4()

    try:
        with psycopg.connect(POSTGRES_URL) as connection:
            for doctor_id, suffix in ((doctor_a, "a"), (doctor_b, "b")):
                connection.execute(
                    """
                    INSERT INTO doctor (id, clerk_id, email, biography)
                    VALUES (%s, %s, %s, NULL)
                    """,
                    (
                        doctor_id,
                        f"user_mapping_scope_{suffix}_{doctor_id.hex}",
                        f"mapping-scope-{suffix}-{doctor_id.hex}@example.test",
                    ),
                )
            connection.execute(
                """
                INSERT INTO patient (id, doctor_id, name, date_of_birth)
                VALUES
                    (%s, %s, 'Synthetic Scope A', %s),
                    (%s, %s, 'Synthetic Scope B', %s)
                """,
                (
                    patient_a,
                    doctor_a,
                    datetime(2000, 1, 1, tzinfo=UTC),
                    patient_b,
                    doctor_b,
                    datetime(2000, 1, 1, tzinfo=UTC),
                ),
            )
            connection.commit()

            with pytest.raises(ForeignKeyViolation):
                connection.execute(
                    """
                    INSERT INTO ehr_sandbox_patient_designation (
                        id, doctor_id, patient_id, designated_by
                    ) VALUES (%s, %s, %s, 'operator_scope_fixture')
                    """,
                    (uuid4(), doctor_a, patient_b),
                )
                connection.commit()
            connection.rollback()

            for designation_id, doctor_id, patient_id in (
                (designation_a, doctor_a, patient_a),
                (designation_b, doctor_b, patient_b),
            ):
                connection.execute(
                    """
                    INSERT INTO ehr_sandbox_patient_designation (
                        id, doctor_id, patient_id, designated_by
                    ) VALUES (%s, %s, %s, 'operator_scope_fixture')
                    """,
                    (designation_id, doctor_id, patient_id),
                )
            connection.commit()

            with pytest.raises(ForeignKeyViolation):
                connection.execute(
                    """
                    INSERT INTO ehr_sandbox_record_mapping (
                        id, doctor_id, patient_id, designation_id, record_ref,
                        provisioned_by
                    ) VALUES (
                        %s, %s, %s, %s, 'a-195900.E-scope',
                        'operator_scope_fixture'
                    )
                    """,
                    (uuid4(), doctor_a, patient_a, designation_b),
                )
                connection.commit()
            connection.rollback()
    finally:
        with psycopg.connect(POSTGRES_URL) as connection:
            connection.execute(
                "DELETE FROM ehr_sandbox_record_mapping WHERE doctor_id IN (%s, %s)",
                (doctor_a, doctor_b),
            )
            connection.execute(
                "DELETE FROM ehr_sandbox_patient_designation "
                "WHERE doctor_id IN (%s, %s)",
                (doctor_a, doctor_b),
            )
            connection.execute(
                "DELETE FROM patient WHERE id IN (%s, %s)",
                (patient_a, patient_b),
            )
            connection.execute(
                "DELETE FROM doctor WHERE id IN (%s, %s)",
                (doctor_a, doctor_b),
            )
            connection.commit()


def test_database_never_reuses_a_vendor_record_after_revocation() -> None:
    """A still-valid grant can never point at a newly reassigned vendor record."""

    assert POSTGRES_URL is not None
    doctor_id = uuid4()
    first_patient_id = uuid4()
    second_patient_id = uuid4()
    first_designation_id = uuid4()
    second_designation_id = uuid4()
    record_ref = f"a-195900.E-history-{uuid4().hex}"

    try:
        with psycopg.connect(POSTGRES_URL) as connection:
            connection.execute(
                """
                INSERT INTO doctor (id, clerk_id, email, biography)
                VALUES (%s, %s, %s, NULL)
                """,
                (
                    doctor_id,
                    f"user_mapping_history_{doctor_id.hex}",
                    f"mapping-history-{doctor_id.hex}@example.test",
                ),
            )
            connection.execute(
                """
                INSERT INTO patient (id, doctor_id, name, date_of_birth)
                VALUES
                    (%s, %s, 'Synthetic History One', %s),
                    (%s, %s, 'Synthetic History Two', %s)
                """,
                (
                    first_patient_id,
                    doctor_id,
                    datetime(2000, 1, 1, tzinfo=UTC),
                    second_patient_id,
                    doctor_id,
                    datetime(2000, 1, 1, tzinfo=UTC),
                ),
            )
            for designation_id, patient_id in (
                (first_designation_id, first_patient_id),
                (second_designation_id, second_patient_id),
            ):
                connection.execute(
                    """
                    INSERT INTO ehr_sandbox_patient_designation (
                        id, doctor_id, patient_id, designated_by
                    ) VALUES (%s, %s, %s, 'operator_history_fixture')
                    """,
                    (designation_id, doctor_id, patient_id),
                )
            connection.execute(
                """
                INSERT INTO ehr_sandbox_record_mapping (
                    id, doctor_id, patient_id, designation_id, record_ref,
                    provisioned_by, revoked_at, revoked_by
                ) VALUES (
                    %s, %s, %s, %s, %s, 'operator_history_fixture',
                    now(), 'operator_history_revoker'
                )
                """,
                (
                    uuid4(),
                    doctor_id,
                    first_patient_id,
                    first_designation_id,
                    record_ref,
                ),
            )
            connection.commit()

            with pytest.raises(UniqueViolation):
                connection.execute(
                    """
                    INSERT INTO ehr_sandbox_record_mapping (
                        id, doctor_id, patient_id, designation_id, record_ref,
                        provisioned_by
                    ) VALUES (
                        %s, %s, %s, %s, %s, 'operator_history_reuser'
                    )
                    """,
                    (
                        uuid4(),
                        doctor_id,
                        second_patient_id,
                        second_designation_id,
                        record_ref,
                    ),
                )
                connection.commit()
            connection.rollback()
    finally:
        with psycopg.connect(POSTGRES_URL) as connection:
            connection.execute(
                "DELETE FROM ehr_sandbox_record_mapping WHERE doctor_id = %s",
                (doctor_id,),
            )
            connection.execute(
                "DELETE FROM ehr_sandbox_patient_designation WHERE doctor_id = %s",
                (doctor_id,),
            )
            connection.execute(
                "DELETE FROM patient WHERE doctor_id = %s",
                (doctor_id,),
            )
            connection.execute("DELETE FROM doctor WHERE id = %s", (doctor_id,))
            connection.commit()
