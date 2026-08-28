import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations/versions/2026_08_27_e7a9c3d5f102_add_public_onboarding_evidence.py"
)
SEAT_MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations/versions/2026_08_27_d0a4c8e2f611_add_clinician_seat_state.py"
)


def _migration_module(path: Path = MIGRATION_PATH):
    spec = importlib.util.spec_from_file_location(
        "public_onboarding_evidence_guard_migration",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class _Connection:
    def __init__(self, populated_query_fragment: str | None = None) -> None:
        self.populated_query_fragment = populated_query_fragment
        self.statements: list[str] = []

    def execute(self, statement) -> _Result:
        rendered = str(statement)
        self.statements.append(rendered)
        return _Result(
            int(
                self.populated_query_fragment is not None
                and self.populated_query_fragment in rendered
            )
        )


def test_public_onboarding_downgrade_fails_closed_offline() -> None:
    migration = _migration_module()
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=True),
            get_bind=lambda: pytest.fail("offline guard must not request a connection"),
        ),
    )

    with pytest.raises(RuntimeError, match="unsupported in offline mode"):
        migration._refuse_populated_downgrade()


@pytest.mark.parametrize(
    "fragment, expected",
    [
        ("FROM public_legal_acceptance", "legal_acceptances=1"),
        ("FROM practice_invitation", "practice_invitations=1"),
        ("FROM practice WHERE", "public_practices=1"),
    ],
)
def test_public_onboarding_downgrade_refuses_material_state(
    fragment: str,
    expected: str,
) -> None:
    migration = _migration_module()
    connection = _Connection(fragment)
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
        ),
    )

    with pytest.raises(RuntimeError, match=expected):
        migration._refuse_populated_downgrade()
    assert connection.statements[0].startswith("LOCK TABLE")


def test_public_onboarding_downgrade_allows_only_empty_online_schema() -> None:
    migration = _migration_module()
    connection = _Connection()
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
        ),
    )

    migration._refuse_populated_downgrade()
    assert connection.statements[0].startswith("LOCK TABLE")
    assert len(connection.statements) == 4


def test_public_acquisition_fence_is_neutral_and_npi_free() -> None:
    source = MIGRATION_PATH.read_text()

    assert 'server_default="managed"' in source
    assert "server_default=None" in source
    assert "ck_practice_acquisition_source" in source
    assert "ck_practice_public_self_service_no_npi" in source
    assert "authorization-neutral" in source
    assert "credential_state" not in source
    assert "credential_review" not in source


def test_seat_state_downgrade_refuses_offline_and_inactive_state() -> None:
    migration = _migration_module(SEAT_MIGRATION_PATH)
    setattr(
        migration,
        "op",
        SimpleNamespace(get_context=lambda: SimpleNamespace(as_sql=True)),
    )
    with pytest.raises(RuntimeError, match="unsupported offline"):
        migration._refuse_inactive_membership_downgrade()

    connection = _Connection("seat_state IS DISTINCT FROM 'active'")
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
        ),
    )
    with pytest.raises(RuntimeError, match="inactive_memberships=1"):
        migration._refuse_inactive_membership_downgrade()
    assert connection.statements[0].startswith("LOCK TABLE practice_member")


def test_seat_state_downgrade_allows_all_active_memberships() -> None:
    migration = _migration_module(SEAT_MIGRATION_PATH)
    connection = _Connection()
    setattr(
        migration,
        "op",
        SimpleNamespace(
            get_context=lambda: SimpleNamespace(as_sql=False),
            get_bind=lambda: connection,
        ),
    )
    migration._refuse_inactive_membership_downgrade()
    assert len(connection.statements) == 2
