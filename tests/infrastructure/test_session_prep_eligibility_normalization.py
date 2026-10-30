"""Secretless normalization checks; no app imports or database execution."""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import pytest

from scripts import session_prep_production_eligibility_preflight as preflight

ROOT = Path(__file__).resolve().parents[2]


def _runtime_normalizers() -> dict[str, Any]:
    """Execute only the reviewed pure functions, without application startup."""

    organization = ast.parse((ROOT / "app/services/organization_access.py").read_text())
    practice = ast.parse((ROOT / "app/models/practice.py").read_text())
    functions = [
        node
        for node in organization.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"normalize_email", "email_matches_exact_domain"}
    ]
    practice_class = next(
        node
        for node in practice.body
        if isinstance(node, ast.ClassDef) and node.name == "Practice"
    )
    methods: list[ast.stmt] = [
        node
        for node in practice_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        in {
            "billing_exemption_active",
            "has_authoritative_stripe_subscription_binding",
            "entitled",
        }
    ]
    declarations = [
        node
        for node in practice.body
        if (isinstance(node, ast.ClassDef) and node.name == "PracticeAcquisitionSource")
        or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id
                in {"ENTITLED_SUBSCRIPTION_STATUSES", "KNOWN_SUBSCRIPTION_STATUSES"}
                for target in node.targets
            )
        )
    ]
    declarations.append(
        ast.ClassDef(
            name="Practice", bases=[], keywords=[], body=methods, decorator_list=[]
        )
    )
    module = ast.fix_missing_locations(
        ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__", names=[ast.alias(name="annotations")], level=0
                ),
                *declarations,
                *functions,
            ],
            type_ignores=[],
        )
    )
    namespace: dict[str, Any] = {
        "Enum": Enum,
        "OrganizationAccessError": ValueError,
        "datetime": datetime,
    }
    exec(compile(module, "reviewed-pure-normalizers", "exec"), namespace)
    return namespace


def _public_practice(subscription_id: str, customer_id: str) -> Any:
    practice = _runtime_normalizers()["Practice"]()
    practice.stripe_subscription_id = subscription_id
    practice.stripe_customer_id = customer_id
    practice.stripe_state_version = 1
    practice.subscription_status = "active"
    practice.acquisition_source = "public_self_service"
    practice.billing_exempt = False
    practice.billing_exempt_until = None
    practice.trial_ends_at = None
    return practice


def _sql_space_binding(subscription_id: str, customer_id: str) -> bool:
    # The documented default btrim removes only U+0020, unlike Python strip.
    return (
        subscription_id.startswith("sub_")
        and len(subscription_id) > 4
        and subscription_id.strip(" ") == subscription_id
        and customer_id.startswith("cus_")
        and len(customer_id) > 4
        and customer_id.strip(" ") == customer_id
    )


@pytest.mark.parametrize(
    "suffix", ["\t", "\n", "\r", "\v", "\f", "\x1c", "\u00a0", "\u2003", "\u3000"]
)
@pytest.mark.parametrize("field", ["subscription", "customer"])
def test_real_runtime_denies_binding_that_space_only_sql_accepts(
    suffix: str,
    field: str,
) -> None:
    subscription = "sub_fictional" + (suffix if field == "subscription" else "")
    customer = "cus_fictional" + (suffix if field == "customer" else "")
    practice = _public_practice(subscription, customer)
    assert _sql_space_binding(subscription, customer)
    assert not practice.has_authoritative_stripe_subscription_binding()
    assert not practice.entitled(at=datetime(2026, 10, 30, tzinfo=UTC))


@pytest.mark.parametrize(
    "space", ["\t", "\x1f", "\u0085", "\u00a0", "\u2003", "\u3000"]
)
def test_real_email_and_domain_contract_also_exposes_space_only_sql_drift(
    space: str,
) -> None:
    runtime = _runtime_normalizers()
    email = space + "FICTIONAL@EXAMPLE.COM" + space
    domain = space + "@@EXAMPLE.COM" + space
    assert runtime["normalize_email"](email) == "fictional@example.com"
    assert runtime["email_matches_exact_domain"](email, domain)
    assert email.strip(" ").lower() != "fictional@example.com"
    assert domain.strip(" ").lower().lstrip("@") != "example.com"


def test_printable_ascii_is_a_bounded_strip_and_case_contract() -> None:
    runtime = _runtime_normalizers()
    for codepoint in range(32, 127):
        character = chr(codepoint)
        for value in (character + "sub_fictional", "sub_fictional" + character):
            assert value.strip() == value.strip(" ")
        email = " " + character + "Fictional@EXAMPLE.COM "
        sql_email = email.strip(" ").lower()
        try:
            normalized = runtime["normalize_email"](email)
        except ValueError:
            assert sql_email.count("@") != 1
        else:
            assert normalized == sql_email
        domain = " " + character + "EXAMPLE.COM "
        assert runtime["email_matches_exact_domain"](
            "fictional@" + domain.strip(" ").lower().lstrip("@"), domain
        ) == ("@" not in domain.strip(" ").lower().lstrip("@"))


def test_guard_excludes_every_python_whitespace_difference_and_unicode_case() -> None:
    unsupported = re.compile(r"[^ -~]")
    for codepoint in range(0x110000):
        character = chr(codepoint)
        if character.isspace() and character != " ":
            assert unsupported.search(character)
    assert unsupported.search("\u0130")  # Python lower expands I-with-dot.
    assert unsupported.search("\u00c9")
    assert unsupported.search("\x7f")
    assert unsupported.search("\u200b")
    assert not unsupported.search(" FICTIONAL@EXAMPLE.COM ")


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None


class _Connection:
    def __init__(self, violations: int = 0) -> None:
        self.violations = violations
        self.queries: list[str] = []

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _Rows:
        self.queries.append(query)
        if query == preflight.INTEGRITY_QUERY:
            return _Rows(
                [
                    (
                        label,
                        self.violations
                        if label == "unsupported_identity_or_billing_normalization"
                        else 0,
                    )
                    for label in preflight.INTEGRITY_CHECKS
                ]
            )
        if query == preflight.MIGRATION_HEAD_QUERY:
            return _Rows([("current_head",)])
        if query == preflight.ELIGIBILITY_QUERY:
            assert params[1] == preflight.BAA_CURRENT_REFERENCE
            return _Rows([("eligible", 2), ("entitlement_not_in_force", 1)])
        raise AssertionError("unexpected query")


def _collect(connection: _Connection) -> dict[str, Any]:
    return preflight.collect_inventory(
        connection,
        observed_at=datetime(2026, 10, 30, tzinfo=UTC),
        rollout_enabled=False,
        migration_contract=preflight.RepositoryMigrationContract(
            head="current_head",
            ancestors=frozenset(
                {"current_head", *preflight.REQUIRED_SESSION_PREP_ANCESTORS}
            ),
        ),
    )


@pytest.mark.parametrize("violations", [1, 7])
def test_unsupported_input_refuses_whole_report_before_classification(
    violations: int,
) -> None:
    connection = _Connection(violations)
    with pytest.raises(preflight.InventoryFailure):
        _collect(connection)
    assert connection.queries == [preflight.INTEGRITY_QUERY]


def test_supported_report_keeps_fixed_labels_and_entire_total() -> None:
    report = _collect(_Connection())
    assert report["total"] == 3
    assert report["eligible"] == 2
    assert report["denial_reason_counts"]["entitlement_not_in_force"] == 1
    assert set(report["denial_reason_counts"]) == set(preflight.DENIAL_REASONS)
    assert "unsupported_identity_or_billing_normalization" not in report


def test_sql_guard_covers_all_inputs_and_uses_binary_ascii_casing() -> None:
    assert "WHERE value COLLATE \"C\" ~ '[^ -~]'" in preflight.INTEGRITY_QUERY
    for projection in (
        "SELECT email AS value FROM doctor",
        "SELECT approved_email FROM organization_roster_approval",
        "SELECT email_domain FROM provider_organization",
        "SELECT stripe_subscription_id FROM practice",
        "SELECT stripe_customer_id FROM practice",
    ):
        assert projection in preflight.INTEGRITY_QUERY
    # No locale-dependent lower call remains at any email or domain use.
    for call in re.findall(r"lower\([^\n]*", preflight.ELIGIBILITY_QUERY):
        assert 'COLLATE "C"' in call
    assert 'GROUP BY approval.approved_email COLLATE "C"' in preflight.ELIGIBILITY_QUERY
    assert 'GROUP BY approved_email COLLATE "C"' in preflight.INTEGRITY_QUERY


def _copy_contract_sources(root: Path) -> None:
    for path, _, _, _ in preflight.NORMALIZATION_SOURCE_CONTRACTS:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / path).read_bytes())


@pytest.mark.parametrize("contract", preflight.NORMALIZATION_SOURCE_CONTRACTS)
def test_changed_normalizer_refuses_without_app_import(
    tmp_path: Path,
    contract: tuple[str, str | None, str, str],
) -> None:
    _copy_contract_sources(tmp_path)
    preflight.validate_runtime_normalization_contract(tmp_path)
    path, _, name, _ = contract
    source = (tmp_path / path).read_text()
    source = source.replace(f"def {name}(", f"def changed_{name}(", 1)
    (tmp_path / path).write_text(source)
    with pytest.raises(preflight.InventoryFailure):
        preflight.validate_runtime_normalization_contract(tmp_path)


def test_source_guard_ignores_formatting_but_rejects_duplicate_definition(
    tmp_path: Path,
) -> None:
    _copy_contract_sources(tmp_path)
    path = tmp_path / "app/services/organization_access.py"
    path.write_text("# harmless comment\n" + path.read_text())
    preflight.validate_runtime_normalization_contract(tmp_path)
    path.write_text(
        path.read_text() + "\ndef normalize_email(value):\n    return value\n"
    )
    with pytest.raises(preflight.InventoryFailure):
        preflight.validate_runtime_normalization_contract(tmp_path)


@pytest.mark.parametrize("contract", preflight.NORMALIZATION_SOURCE_CONTRACTS)
def test_normalization_body_change_requires_new_review(
    tmp_path: Path,
    contract: tuple[str, str | None, str, str],
) -> None:
    _copy_contract_sources(tmp_path)
    path, _, _, _ = contract
    source = (tmp_path / path).read_text()
    (tmp_path / path).write_text(source.replace(".strip()", '.strip(" ")'))
    with pytest.raises(preflight.InventoryFailure):
        preflight.validate_runtime_normalization_contract(tmp_path)


def test_source_drift_prevents_any_inventory_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    connection = _Connection()
    with pytest.raises(preflight.InventoryFailure):
        _collect(connection)
    assert connection.queries == []


def test_command_source_refusal_is_generic_and_precedes_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    monkeypatch.setenv("PYTHON_ENV", "production")
    monkeypatch.setenv(
        "DATABASE_URL",
        (
            "postgresql://fictional:fictional@"
            "cosmaneura-prod-postgres.postgres.database.azure.com:5432/production"
            "?sslmode=verify-full&sslrootcert=%2Fetc%2Fssl%2Fcerts%2Fca-certificates.crt"
        ),
    )
    monkeypatch.setattr(
        preflight.psycopg,
        "connect",
        lambda *a, **k: pytest.fail("connection must not be attempted"),
    )
    assert preflight.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Session Prep production eligibility preflight failed.\n"
