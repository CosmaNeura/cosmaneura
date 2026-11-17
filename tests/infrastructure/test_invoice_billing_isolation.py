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
    expected = {
        ".envrc": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ".env.staging.backend": "8e4b1da0b048183002645ae1e347d85d29c604414cfcbe050d8d2c8095898b61",
        ".env.production.backend": "1f7799577a34804b1b5fb8093cc8b0df03d57b934670b2e7b3d0d9512bff6534",
        ".env.test": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ".env.dev.local-example": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ".env.local-example": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ".env": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ".env.dev": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ".env.staging.frontend": "49eb84b5841d78b93093ac835190545f5481b48ca1845c4a1a7204961c676f45",
        ".env.production.frontend": "2cf5a24b04e3ad160f49fba813ecb2e6bd75f2bd0af08ce4db495685a7a60fe3",
        ".env.shared": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    }
    for name, digest in expected.items():
        source = (ROOT / name).read_text()
        flags = [
            line
            for line in source.splitlines()
            if re.match(r"(?:export\s+)?\w+_ENABLED=", line)
        ]
        assert hashlib.sha256("\n".join(flags).encode()).hexdigest() == digest, name
    backend_sources = {
        ".env.production.backend": "c4a4c2dfd892e48fc9a121b161669fa90bd89f0a742c510e01bdc5788ea53b01",
        ".env.staging.backend": "ec5501d1eb5759678a019d0239f54a1d72e799ed9ad54c98be44057c7eb22ef3",
    }
    for name, digest in backend_sources.items():
        lines = (ROOT / name).read_text().splitlines(keepends=True)
        additions = [
            line
            for line in lines
            if line.startswith(
                ("export STRIPE_MCD_PRICE_ID=", "export STRIPE_LEGACY_PRICE_IDS=")
            )
        ]
        assert len(additions) == 2
        assert all("op://" in line for line in additions)
        original = "".join(line for line in lines if line not in additions)
        assert hashlib.sha256(original.encode()).hexdigest() == digest


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
    assert [call.func.attr for call in calls if call.func.attr != "f"] == [
        "create_table"
    ]
    assert calls[0].args[0].value == "practice_invoice_billing"
    assert not any(
        word in paths[0].read_text().lower()
        for word in ["update practice", "insert into", "backfill", "alter_column"]
    )
