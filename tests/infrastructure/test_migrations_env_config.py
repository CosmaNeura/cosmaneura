import ast
from pathlib import Path

from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]


def _encoded_database_url() -> str:
    return (
        "postgresql+psycopg://user:local-test-only@db.example.test/database"
        "?sslmode=verify-full&sslrootcert=%2Fetc%2Fssl%2Fcerts%2Fca.pem"
    )


def test_migration_url_preserves_percent_encoding_through_alembic_config() -> None:
    source = (ROOT / "migrations" / "env.py").read_text()
    tree = ast.parse(source)
    statement = next(
        node
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "set_main_option"
    )

    expected = _encoded_database_url()
    config = Config()
    namespace = {"config": config, "database_url": lambda: expected}
    exec(
        compile(
            ast.Module(body=[statement], type_ignores=[]),
            "migrations/env.py",
            "exec",
        ),
        namespace,
    )

    assert config.get_main_option("sqlalchemy.url") == expected
    section = config.get_section(config.config_ini_section)
    assert section is not None
    assert section["sqlalchemy.url"] == expected


def test_old_candidate_compatibility_value_allows_exactly_one_interpolation() -> None:
    raw = _encoded_database_url()
    compatibility_value = raw.replace("%", "%%")

    old_candidate_config = Config()
    old_candidate_config.set_main_option("sqlalchemy.url", compatibility_value)
    assert old_candidate_config.get_main_option("sqlalchemy.url") == raw

    permanently_fixed_config = Config()
    permanently_fixed_config.set_main_option(
        "sqlalchemy.url", compatibility_value.replace("%", "%%")
    )
    assert permanently_fixed_config.get_main_option("sqlalchemy.url") != raw


def test_recovery_image_can_restart_with_percent_free_rotation_url() -> None:
    raw = (
        "postgresql+psycopg://user:AZaz09-_.~@db.example.test/database?sslmode=require"
    )
    assert "%" not in raw

    recovery_config = Config()
    recovery_config.set_main_option("sqlalchemy.url", raw)

    assert recovery_config.get_main_option("sqlalchemy.url") == raw
