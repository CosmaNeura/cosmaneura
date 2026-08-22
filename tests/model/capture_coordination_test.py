"""PostgreSQL constraint tests for provider-neutral capture coordination.

Run with ``CAPTURE_COORDINATION_TEST_DATABASE_URL`` pointed at a disposable,
fully migrated PostgreSQL database, or use the ordinary ``TEST_DATABASE_URL``
provided by CI. The normal test transaction fixture cannot exercise
concurrent/trigger behavior through independent connections.
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

POSTGRES_URL = os.environ.get(
    "CAPTURE_COORDINATION_TEST_DATABASE_URL"
) or os.environ.get("TEST_DATABASE_URL")
if POSTGRES_URL is not None:
    POSTGRES_URL = POSTGRES_URL.replace("postgresql+psycopg://", "postgresql://")
pytestmark = pytest.mark.skipif(
    POSTGRES_URL is None,
    reason="requires a disposable migrated PostgreSQL capture database",
)


def _insert_scope(connection) -> tuple:
    doctor_id = uuid4()
    practice_id = uuid4()
    patient_id = uuid4()
    appointment_id = uuid4()

    connection.execute(
        """
        INSERT INTO doctor (id, clerk_id, email, biography)
        VALUES (%s, %s, %s, NULL)
        """,
        (
            doctor_id,
            f"user_capture_{doctor_id.hex}",
            f"capture-{doctor_id.hex}@example.test",
        ),
    )
    connection.execute(
        """
        INSERT INTO practice (id, legal_name, join_code, email_domain)
        VALUES (%s, 'Synthetic Capture Practice', %s, 'example.test')
        """,
        (practice_id, f"C{practice_id.hex[:7]}"),
    )
    connection.execute(
        """
        INSERT INTO practice_member (id, practice_id, doctor_id, role)
        VALUES (%s, %s, %s, 'owner')
        """,
        (uuid4(), practice_id, doctor_id),
    )
    connection.execute(
        """
        INSERT INTO patient (id, doctor_id, name, date_of_birth)
        VALUES (%s, %s, 'Synthetic Capture Patient', %s)
        """,
        (patient_id, doctor_id, datetime(2000, 1, 1, tzinfo=UTC)),
    )
    connection.execute(
        """
        INSERT INTO appointment (id, doctor_id, patient_id, appointment_date)
        VALUES (%s, %s, %s, %s)
        """,
        (appointment_id, doctor_id, patient_id, datetime.now(UTC)),
    )
    return practice_id, doctor_id, appointment_id


def _insert_session(
    connection,
    *,
    practice_id,
    doctor_id,
    appointment_id,
    source_kind: str = "desktop_companion",
):
    session_id = uuid4()
    connection.execute(
        """
        INSERT INTO capture_session (
            id, practice_id, actor_id, appointment_id, source_kind
        ) VALUES (%s, %s, %s, %s, %s)
        """,
        (session_id, practice_id, doctor_id, appointment_id, source_kind),
    )
    return session_id


def _insert_lease(
    connection,
    *,
    session_id,
    practice_id,
    doctor_id,
    appointment_id,
    generation: int = 0,
    digest_character: str = "a",
    expired: bool = False,
):
    lease_id = uuid4()
    now = datetime.now(UTC)
    acquired_at = now - timedelta(minutes=10) if expired else now
    deadline = now - timedelta(minutes=5) if expired else now + timedelta(minutes=1)
    connection.execute(
        """
        INSERT INTO capture_source_lease (
            id, session_id, practice_id, actor_id, appointment_id,
            source_kind, lease_token_digest, generation, acquired_at,
            control_deadline_at
        ) VALUES (%s, %s, %s, %s, %s, 'desktop_companion', %s, %s, %s, %s)
        """,
        (
            lease_id,
            session_id,
            practice_id,
            doctor_id,
            appointment_id,
            digest_character * 64,
            generation,
            acquired_at,
            deadline,
        ),
    )
    return lease_id


def test_exact_scope_and_expired_lease_still_block_overlap() -> None:
    assert POSTGRES_URL is not None
    with psycopg.connect(POSTGRES_URL) as connection:
        practice_a, doctor_a, appointment_a = _insert_scope(connection)
        practice_b, doctor_b, appointment_b = _insert_scope(connection)

        with pytest.raises(ForeignKeyViolation):
            with connection.transaction():
                _insert_session(
                    connection,
                    practice_id=practice_a,
                    doctor_id=doctor_b,
                    appointment_id=appointment_a,
                )

        session_a = _insert_session(
            connection,
            practice_id=practice_a,
            doctor_id=doctor_a,
            appointment_id=appointment_a,
        )
        expired_lease = _insert_lease(
            connection,
            session_id=session_a,
            practice_id=practice_a,
            doctor_id=doctor_a,
            appointment_id=appointment_a,
            expired=True,
        )

        with pytest.raises(UniqueViolation):
            with connection.transaction():
                _insert_lease(
                    connection,
                    session_id=session_a,
                    practice_id=practice_a,
                    doctor_id=doctor_a,
                    appointment_id=appointment_a,
                    generation=1,
                    digest_character="b",
                )

        proved_at = datetime.now(UTC)
        connection.execute(
            """
            UPDATE capture_source_lease
            SET state = 'closed', stop_requested_at = %s,
                never_started_proved_at = %s, closed_at = %s
            WHERE id = %s
            """,
            (proved_at, proved_at, proved_at, expired_lease),
        )
        replacement = _insert_lease(
            connection,
            session_id=session_a,
            practice_id=practice_a,
            doctor_id=doctor_a,
            appointment_id=appointment_a,
            generation=1,
            digest_character="b",
        )
        assert replacement is not None

        # A valid session from another scope does not weaken either composite FK.
        assert practice_a != practice_b
        assert doctor_a != doctor_b
        assert appointment_a != appointment_b
        connection.rollback()


def test_segment_capability_is_short_lived_one_use_and_start_is_immutable() -> None:
    assert POSTGRES_URL is not None
    with psycopg.connect(POSTGRES_URL) as connection:
        practice_id, doctor_id, appointment_id = _insert_scope(connection)
        session_id = _insert_session(
            connection,
            practice_id=practice_id,
            doctor_id=doctor_id,
            appointment_id=appointment_id,
        )
        segment_id = uuid4()
        connection.execute(
            """
            INSERT INTO capture_segment (
                id, session_id, practice_id, actor_id, appointment_id,
                ordinal, channel
            ) VALUES (%s, %s, %s, %s, %s, 0, 'microphone')
            """,
            (segment_id, session_id, practice_id, doctor_id, appointment_id),
        )

        issued_at = datetime.now(UTC)
        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    """
                    UPDATE capture_segment
                    SET capability_id = %s, capability_digest = %s,
                        encrypted_capability = 'ciphertext',
                        capability_issued_at = %s, capability_expires_at = %s
                    WHERE id = %s
                    """,
                    (
                        uuid4(),
                        "c" * 64,
                        issued_at,
                        issued_at + timedelta(minutes=5, seconds=1),
                        segment_id,
                    ),
                )

        capability_id = uuid4()
        connection.execute(
            """
            UPDATE capture_segment
            SET capability_id = %s, capability_digest = %s,
                encrypted_capability = 'ciphertext',
                capability_issued_at = %s, capability_expires_at = %s,
                physical_started_at = %s, state = 'capturing', state_version = 1
            WHERE id = %s
            """,
            (
                capability_id,
                "c" * 64,
                issued_at,
                issued_at + timedelta(minutes=5),
                issued_at,
                segment_id,
            ),
        )
        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    "UPDATE capture_segment SET encrypted_capability = NULL "
                    "WHERE id = %s",
                    (segment_id,),
                )

        consumed_at = datetime.now(UTC)
        connection.execute(
            """
            UPDATE capture_segment
            SET capability_consumed_at = %s, encrypted_capability = NULL,
                capability_purged_at = %s
            WHERE id = %s
            """,
            (consumed_at, consumed_at, segment_id),
        )

        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    "UPDATE capture_segment SET capability_consumed_at = %s "
                    "WHERE id = %s",
                    (consumed_at + timedelta(seconds=1), segment_id),
                )
        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    "UPDATE capture_segment SET physical_started_at = %s WHERE id = %s",
                    (issued_at + timedelta(seconds=1), segment_id),
                )

        expired_segment_id = uuid4()
        expired_at = issued_at + timedelta(minutes=1)
        connection.execute(
            """
            INSERT INTO capture_segment (
                id, session_id, practice_id, actor_id, appointment_id,
                ordinal, channel, capability_id, capability_digest,
                encrypted_capability, capability_issued_at, capability_expires_at
            ) VALUES (%s, %s, %s, %s, %s, 1, 'microphone', %s, %s, %s, %s, %s)
            """,
            (
                expired_segment_id,
                session_id,
                practice_id,
                doctor_id,
                appointment_id,
                uuid4(),
                "7" * 64,
                "expired-ciphertext",
                issued_at,
                expired_at,
            ),
        )
        connection.execute(
            """
            UPDATE capture_segment
            SET encrypted_capability = NULL, capability_purged_at = %s
            WHERE id = %s
            """,
            (expired_at, expired_segment_id),
        )
        purged_ciphertext = connection.execute(
            "SELECT encrypted_capability, capability_purged_at "
            "FROM capture_segment WHERE id = %s",
            (expired_segment_id,),
        ).fetchone()
        assert purged_ciphertext == (None, expired_at)
        connection.rollback()


def test_launch_ticket_defers_pkce_and_is_bound_to_one_exact_lease() -> None:
    assert POSTGRES_URL is not None
    with psycopg.connect(POSTGRES_URL) as connection:
        practice_id, doctor_id, appointment_id = _insert_scope(connection)
        session_id = _insert_session(
            connection,
            practice_id=practice_id,
            doctor_id=doctor_id,
            appointment_id=appointment_id,
        )
        lease_id = _insert_lease(
            connection,
            session_id=session_id,
            practice_id=practice_id,
            doctor_id=doctor_id,
            appointment_id=appointment_id,
        )
        authorization_id = uuid4()
        now = datetime.now(UTC)
        connection.execute(
            """
            INSERT INTO capture_launch_authorization (
                id, created_at, session_id, practice_id, actor_id, appointment_id,
                source_lease_id, actor_binding_digest, launch_ticket_digest,
                ticket_expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                authorization_id,
                now,
                session_id,
                practice_id,
                doctor_id,
                appointment_id,
                lease_id,
                "d" * 64,
                "e" * 64,
                now + timedelta(minutes=2),
            ),
        )

        issued_at = datetime.now(UTC)
        connection.execute(
            """
            UPDATE capture_launch_authorization
            SET state = 'code_issued', state_version = 1,
                ticket_consumed_at = %s,
                installation_thumbprint_digest = %s,
                redirect_uri_digest = %s,
                pkce_code_challenge = %s, pkce_method = 'S256',
                authorization_code_digest = %s,
                authorization_code_issued_at = %s,
                authorization_code_expires_at = %s
            WHERE id = %s
            """,
            (
                issued_at,
                "f" * 64,
                "1" * 64,
                "A" * 43,
                "2" * 64,
                issued_at,
                issued_at + timedelta(minutes=5),
                authorization_id,
            ),
        )
        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    "UPDATE capture_launch_authorization "
                    "SET ticket_consumed_at = %s WHERE id = %s",
                    (issued_at + timedelta(seconds=1), authorization_id),
                )
        token_issued_at = issued_at + timedelta(seconds=1)
        connection.execute(
            """
            UPDATE capture_launch_authorization
            SET state = 'token_issued', state_version = 2,
                authorization_code_consumed_at = %s,
                desktop_token_digest = %s,
                desktop_token_issued_at = %s,
                desktop_token_expires_at = %s
            WHERE id = %s
            """,
            (
                token_issued_at,
                "3" * 64,
                token_issued_at,
                token_issued_at + timedelta(hours=6),
                authorization_id,
            ),
        )
        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    "UPDATE capture_launch_authorization "
                    "SET desktop_token_expires_at = %s WHERE id = %s",
                    (
                        token_issued_at + timedelta(hours=6, microseconds=1),
                        authorization_id,
                    ),
                )
        connection.rollback()


def test_operation_idempotency_and_events_are_database_enforced() -> None:
    assert POSTGRES_URL is not None
    with psycopg.connect(POSTGRES_URL) as connection:
        practice_id, doctor_id, appointment_id = _insert_scope(connection)
        session_id = _insert_session(
            connection,
            practice_id=practice_id,
            doctor_id=doctor_id,
            appointment_id=appointment_id,
        )
        operation_id = uuid4()
        operation_values = (
            operation_id,
            session_id,
            practice_id,
            doctor_id,
            appointment_id,
            "desktop_companion",
            "3" * 64,
            "start_segment",
            "4" * 64,
            "5" * 64,
        )
        connection.execute(
            """
            INSERT INTO capture_operation (
                id, session_id, practice_id, actor_id, appointment_id,
                caller_scope, caller_principal_digest, operation,
                idempotency_key_digest, request_digest
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            operation_values,
        )
        replay_expires_at = datetime.now(UTC) + timedelta(minutes=1)
        replay_purged_at = replay_expires_at
        connection.execute(
            """
            UPDATE capture_operation
            SET replay_response_digest = %s,
                encrypted_replay_response = %s,
                replay_response_expires_at = %s
            WHERE id = %s
            """,
            ("8" * 64, "encrypted-replay", replay_expires_at, operation_id),
        )
        connection.execute(
            """
            UPDATE capture_operation
            SET encrypted_replay_response = NULL,
                replay_response_purged_at = %s
            WHERE id = %s
            """,
            (replay_purged_at, operation_id),
        )
        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    "UPDATE capture_operation SET replay_response_purged_at = %s "
                    "WHERE id = %s",
                    (replay_purged_at + timedelta(seconds=1), operation_id),
                )
        with pytest.raises(UniqueViolation):
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO capture_operation (
                        id, session_id, practice_id, actor_id, appointment_id,
                        caller_scope, caller_principal_digest, operation,
                        idempotency_key_digest, request_digest
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (uuid4(), *operation_values[1:9], "6" * 64),
                )

        event_id = uuid4()
        forged_recorded_at = datetime(2000, 1, 1, tzinfo=UTC)
        connection.execute(
            """
            INSERT INTO capture_event (
                id, session_id, practice_id, actor_id, appointment_id,
                operation_id, local_sequence, event_type, state_version,
                session_state, recorded_at
            ) VALUES (%s, %s, %s, %s, %s, %s, 0, 'session_reserved', 0,
                      'awaiting_consent', %s)
            """,
            (
                event_id,
                session_id,
                practice_id,
                doctor_id,
                appointment_id,
                operation_id,
                forged_recorded_at,
            ),
        )
        recorded_at = connection.execute(
            "SELECT recorded_at FROM capture_event WHERE id = %s",
            (event_id,),
        ).fetchone()
        assert recorded_at is not None
        assert recorded_at[0] > datetime.now(UTC) - timedelta(minutes=1)
        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    "UPDATE capture_event SET event_type = 'tampered' WHERE id = %s",
                    (event_id,),
                )
        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    "DELETE FROM capture_event WHERE id = %s", (event_id,)
                )
        connection.rollback()
