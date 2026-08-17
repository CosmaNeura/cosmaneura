import os
import threading
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import UniqueViolation

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
        connection.commit()

    def provision(mapping_id) -> None:
        with psycopg.connect(postgres_url) as connection:
            barrier.wait(timeout=5)
            try:
                connection.execute(
                    """
                    INSERT INTO ehr_sandbox_record_mapping (
                        id, doctor_id, patient_id, adapter, environment,
                        practice_ref, record_ref, synthetic_only, provisioned_by
                    ) VALUES (
                        %s, %s, %s, 'athena', 'preview', '195900', %s,
                        TRUE, 'operator_race_fixture'
                    )
                    """,
                    (mapping_id, doctor_id, patient_id, record_ref),
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
            connection.execute("DELETE FROM patient WHERE id = %s", (patient_id,))
            connection.execute("DELETE FROM doctor WHERE id = %s", (doctor_id,))
            connection.commit()
