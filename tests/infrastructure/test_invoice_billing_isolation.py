"""Source and schema fences for the invoice-only lane and immutable env flags."""

import ast
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_access_modules_do_not_reference_invoice_billing_model():
    for path in (
        "app/models/practice.py",
        "app/services/doctor_access.py",
        "app/routes/dependencies/practice_gate.py",
    ):
        source = (ROOT / path).read_text()
        assert "invoice_billing" not in source
        assert "PracticeInvoiceBilling" not in source


def test_env_files_flag_values_unchanged():
    # Golden hashes of flag declarations on F1's master base. This test does
    # not depend on remote refs being present in a shallow CI checkout.
    names = ".env .env.dev .env.dev.local-example .env.local-example .env.production.backend .env.production.frontend .env.shared .env.staging.backend .env.staging.frontend .env.test .envrc".split()
    evidence = "\n".join(
        f"{name}:"
        + "\n".join(
            re.findall(
                r"^(?:export\s+)?\w+_ENABLED=.*$", (ROOT / name).read_text(), re.M
            )
        )
        for name in names
    )
    expected = "6c05265decf395bfba70e0f14ece7854bfbb459698ca4f66c34cf4de45fe6b91"
    assert hashlib.sha256(evidence.encode()).hexdigest() == expected


def test_invoice_table_migration_is_add_only_without_backfill():
    paths = list(
        (ROOT / "migrations/versions").glob("*_add_practice_invoice_billing.py")
    )
    assert len(paths) == 1
    tree = ast.parse(paths[0].read_text())
    upgrade = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    calls = [
        node
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    ]
    assert [
        getattr(call.func, "attr")
        for call in calls
        if getattr(call.func, "attr") != "f"
    ] == ["create_table"]
    assert isinstance(calls[0].args[0], ast.Constant)
    assert calls[0].args[0].value == "practice_invoice_billing"
    assert not any(
        word in paths[0].read_text().lower()
        for word in ["update practice", "insert into", "backfill", "alter_column"]
    )
