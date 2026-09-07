from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PYTHON_JUST = ROOT / "just" / "python.just"
WORKFLOWS = (
    ROOT / ".github/workflows/pull_request_checks.yml",
    ROOT / ".github/workflows/build_and_publish_manual.yml",
    ROOT / ".github/workflows/deploy_staging_candidate.yml",
)
SHARDS = (
    "application",
    "infrastructure-general",
    "infrastructure-controller",
    "infrastructure-receipt-io",
    "infrastructure-sanitizer-foundation",
    "infrastructure-sanitizer-source-state-authority",
    "infrastructure-sanitizer-source-state-context",
    "infrastructure-sanitizer-source-state-integrity",
    "infrastructure-sanitizer-evidence",
    "infrastructure-sanitizer-evidence-integrity",
    "infrastructure-sanitizer-executor",
    "infrastructure-sanitizer-data-contract-executor",
    "infrastructure-sanitizer-source-snapshot",
    "infrastructure-restore",
    "integration",
)
FILE_SHARDS = tuple(
    shard for shard in SHARDS if shard != "infrastructure-sanitizer-evidence-integrity"
)
CONTROLLER_GLOB = "tests/infrastructure/test_staging_controller_receipt_v2*.py"
RESTORE_GLOB = "tests/infrastructure/test_staging_restore_*.py"
CONTROLLER_BASE = CONTROLLER_GLOB[:-4]
CONTROLLER_SELECTOR = (
    f"{CONTROLLER_BASE}.py "
    f"{CONTROLLER_BASE}_{{arm_observation,database_observation}}.py"
)
RECEIPT_IO_SELECTOR = f"{CONTROLLER_BASE}_{{builder,writer}}.py"
SANITIZER_FOUNDATION_SELECTOR = "tests/infrastructure/test_staging_restore_sanitizer.py"
SANITIZER_SOURCE_STATE_GLOB = (
    "tests/infrastructure/test_staging_restore_fixed_catalog_source_state*.py"
)
SANITIZER_SOURCE_STATE_AUTHORITY_SELECTOR = (
    "tests/infrastructure/test_staging_restore_fixed_catalog_source_state_authority.py"
)
SANITIZER_SOURCE_STATE_CONTEXT_SELECTOR = (
    "tests/infrastructure/test_staging_restore_fixed_catalog_source_state_context.py"
)
SANITIZER_SOURCE_STATE_INTEGRITY_SELECTOR = (
    "tests/infrastructure/test_staging_restore_fixed_catalog_source_state_integrity.py"
)
SANITIZER_SOURCE_STATE_SELECTORS = (
    SANITIZER_SOURCE_STATE_AUTHORITY_SELECTOR,
    SANITIZER_SOURCE_STATE_CONTEXT_SELECTOR,
    SANITIZER_SOURCE_STATE_INTEGRITY_SELECTOR,
)
RESTORE_RECEIPT_SELECTOR = (
    f"{CONTROLLER_BASE}_{{isolation_inventory,private_reader,production_authority}}.py"
)
CONTROLLER_PATHS = frozenset(
    Path(path)
    for path in (
        f"{CONTROLLER_BASE}.py",
        f"{CONTROLLER_BASE}_arm_observation.py",
        f"{CONTROLLER_BASE}_database_observation.py",
    )
)
RECEIPT_IO_PATHS = frozenset(
    Path(path)
    for path in (
        f"{CONTROLLER_BASE}_builder.py",
        f"{CONTROLLER_BASE}_writer.py",
    )
)
RESTORE_RECEIPT_PATHS = frozenset(
    Path(path)
    for path in (
        f"{CONTROLLER_BASE}_isolation_inventory.py",
        f"{CONTROLLER_BASE}_private_reader.py",
        f"{CONTROLLER_BASE}_production_authority.py",
    )
)
SANITIZER_RUNTIME_GLOB = "tests/infrastructure/test_staging_restore_sanitizer_*.py"
SANITIZER_EVIDENCE_SELECTOR = (
    "tests/infrastructure/test_staging_restore_sanitizer_evidence.py"
)
SANITIZER_EVIDENCE_PRIMARY_SELECTORS = (
    f"{SANITIZER_EVIDENCE_SELECTOR}::test_policy_is_hash_pinned_closed_world_and_dark",
    f"{SANITIZER_EVIDENCE_SELECTOR}::test_trusted_evidence_policy_mutations_fail_closed",
    f"{SANITIZER_EVIDENCE_SELECTOR}::test_every_evidence_digest_is_bound_to_the_controller_intent",
    f"{SANITIZER_EVIDENCE_SELECTOR}::test_exact_api_rejects_mappings_partials_subclasses_and_forged_seals",
    f"{SANITIZER_EVIDENCE_SELECTOR}::test_module_has_no_runtime_or_mutation_surface",
)
SANITIZER_EVIDENCE_INTEGRITY_SELECTORS = (
    f"{SANITIZER_EVIDENCE_SELECTOR}::test_real_chain_joins_final_evidence_after_historical_expiry",
    f"{SANITIZER_EVIDENCE_SELECTOR}::test_source_restore_and_run_context_cannot_be_substituted",
    f"{SANITIZER_EVIDENCE_SELECTOR}::test_mixed_real_chains_and_tampered_outputs_refuse",
    f"{SANITIZER_EVIDENCE_SELECTOR}::test_clock_shape_regression_and_target_expiry_refuse",
    f"{SANITIZER_EVIDENCE_SELECTOR}::test_output_cannot_be_constructed_or_serialized",
)
SANITIZER_EXECUTOR_SELECTOR = (
    "tests/infrastructure/test_staging_restore_sanitizer_executor.py"
)
SANITIZER_TRANSACTION_PLAN_SELECTOR = (
    "tests/infrastructure/test_staging_restore_sanitizer_transaction_plan.py"
)
SANITIZER_DATABASE_CAPABILITY_SELECTOR = (
    "tests/infrastructure/test_staging_restore_sanitizer_database_capability.py"
)
SANITIZER_DATA_CONTRACT_EXECUTOR_SELECTOR = (
    "tests/infrastructure/test_staging_restore_sanitizer_data_contract_executor.py"
)
SANITIZER_SOURCE_SNAPSHOT_SELECTOR = (
    "tests/infrastructure/test_staging_restore_sanitizer_source_snapshot.py"
)
SANITIZER_POST_PROJECTION_SELECTOR = "tests/infrastructure/test_staging_restore_sanitizer_fixed_catalog_post_projection.py"
SANITIZER_RUNTIME_SELECTORS = (
    SANITIZER_EVIDENCE_SELECTOR,
    SANITIZER_DATABASE_CAPABILITY_SELECTOR,
    SANITIZER_EXECUTOR_SELECTOR,
    SANITIZER_TRANSACTION_PLAN_SELECTOR,
    SANITIZER_DATA_CONTRACT_EXECUTOR_SELECTOR,
    SANITIZER_SOURCE_SNAPSHOT_SELECTOR,
    SANITIZER_POST_PROJECTION_SELECTOR,
)


def _infrastructure_shard(path: Path) -> str:
    if path.parent == Path("tests/infrastructure"):
        if path == Path(SANITIZER_SOURCE_STATE_AUTHORITY_SELECTOR):
            return "infrastructure-sanitizer-source-state-authority"
        if path == Path(SANITIZER_SOURCE_STATE_CONTEXT_SELECTOR):
            return "infrastructure-sanitizer-source-state-context"
        if path == Path(SANITIZER_SOURCE_STATE_INTEGRITY_SELECTOR):
            return "infrastructure-sanitizer-source-state-integrity"
        if path.name.startswith("test_staging_restore_fixed_catalog_source_state"):
            return "unassigned"
        if path == Path(SANITIZER_FOUNDATION_SELECTOR):
            return "infrastructure-sanitizer-foundation"
        if path == Path(SANITIZER_EVIDENCE_SELECTOR):
            return "infrastructure-sanitizer-evidence"
        if path in map(
            Path,
            (
                SANITIZER_DATABASE_CAPABILITY_SELECTOR,
                SANITIZER_EXECUTOR_SELECTOR,
                SANITIZER_TRANSACTION_PLAN_SELECTOR,
            ),
        ):
            return "infrastructure-sanitizer-executor"
        if path == Path(SANITIZER_SOURCE_SNAPSHOT_SELECTOR):
            return "infrastructure-sanitizer-source-snapshot"
        if path in map(
            Path,
            (
                SANITIZER_DATA_CONTRACT_EXECUTOR_SELECTOR,
                SANITIZER_POST_PROJECTION_SELECTOR,
            ),
        ):
            return "infrastructure-sanitizer-data-contract-executor"
        if path.name.startswith("test_staging_restore_sanitizer_"):
            return "unassigned"
        if path in RECEIPT_IO_PATHS:
            return "infrastructure-receipt-io"
        if path in RESTORE_RECEIPT_PATHS:
            return "infrastructure-restore"
        if path in CONTROLLER_PATHS:
            return "infrastructure-controller"
        if path.name.startswith("test_staging_controller_receipt_v2"):
            return "unassigned"
        if path.name.startswith("test_staging_restore_"):
            return "infrastructure-restore"
    return "infrastructure-general"


def _workflow(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _run_commands(job: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        step["run"]
        for step in job["steps"]
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    )


def _named_step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def _collect_nodeids(*selectors: str) -> tuple[str, ...]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--confcutdir=tests/infrastructure",
            *selectors,
        ],
        check=False,
        capture_output=True,
        cwd=ROOT,
        env=os.environ
        | {
            "PYTHONPATH": str(ROOT),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHON_ENV": "test",
        },
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return tuple(
        line
        for line in completed.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    )


def test_ci_recipes_are_explicit_disjoint_and_exhaustive() -> None:
    source = PYTHON_JUST.read_text(encoding="utf-8")
    py_setup_start = source.index("py_setup:")
    py_setup_end = source.index("\n# clean entire py project", py_setup_start)
    py_setup_recipe = source[py_setup_start:py_setup_end]
    assert "playwright install chromium --only-shell" not in py_setup_recipe
    assert "uv run playwright install chromium" in py_setup_recipe

    expected_commands = {
        "application": (
            "uv run pytest tests --verbosity=2 \\\n"
            "\t\t--ignore=tests/infrastructure \\\n"
            "\t\t--ignore=tests/integration \\\n"
        ),
        "infrastructure-general": (
            "uv run pytest tests/infrastructure --verbosity=2 \\\n"
            f"\t\t--ignore-glob='{CONTROLLER_GLOB}' \\\n"
            f"\t\t--ignore-glob='{RESTORE_GLOB}' \\\n"
        ),
        "infrastructure-controller": (
            f"uv run pytest {CONTROLLER_SELECTOR} --verbosity=2"
        ),
        "infrastructure-receipt-io": (
            f"uv run pytest {RECEIPT_IO_SELECTOR} --verbosity=2"
        ),
        "infrastructure-sanitizer-foundation": (
            f"uv run pytest {SANITIZER_FOUNDATION_SELECTOR} --verbosity=2"
        ),
        "infrastructure-sanitizer-source-state-authority": (
            f"uv run pytest {SANITIZER_SOURCE_STATE_AUTHORITY_SELECTOR} --verbosity=2"
        ),
        "infrastructure-sanitizer-source-state-context": (
            f"uv run pytest {SANITIZER_SOURCE_STATE_CONTEXT_SELECTOR} --verbosity=2"
        ),
        "infrastructure-sanitizer-source-state-integrity": (
            f"uv run pytest {SANITIZER_SOURCE_STATE_INTEGRITY_SELECTOR} --verbosity=2"
        ),
        "infrastructure-sanitizer-evidence": (
            "uv run pytest \\\n"
            + "".join(
                f"\t\t{selector} \\\n"
                for selector in SANITIZER_EVIDENCE_PRIMARY_SELECTORS
            )
            + "\t\t--verbosity=2"
        ),
        "infrastructure-sanitizer-evidence-integrity": (
            "uv run pytest \\\n"
            + "".join(
                f"\t\t{selector} \\\n"
                for selector in SANITIZER_EVIDENCE_INTEGRITY_SELECTORS
            )
            + "\t\t--verbosity=2"
        ),
        "infrastructure-sanitizer-executor": (
            "uv run pytest tests/infrastructure/"
            "test_staging_restore_sanitizer_{database_capability,executor,transaction_plan}.py --verbosity=2"
        ),
        "infrastructure-sanitizer-data-contract-executor": (
            "uv run pytest tests/infrastructure/"
            "test_staging_restore_sanitizer_{data_contract_executor,fixed_catalog_post_projection}.py --verbosity=2"
        ),
        "infrastructure-sanitizer-source-snapshot": (
            f"uv run pytest {SANITIZER_SOURCE_SNAPSHOT_SELECTOR} --verbosity=2"
        ),
        "infrastructure-restore": (
            f"restore_tests=({RESTORE_GLOB})\n"
            "\tdedicated_restore_tests=("
            f"{SANITIZER_FOUNDATION_SELECTOR} {SANITIZER_SOURCE_STATE_GLOB} "
            f"{SANITIZER_RUNTIME_GLOB})\n"
            '\trestore_tests=("${(@)restore_tests:|dedicated_restore_tests}")\n'
            '\tuv run pytest "${restore_tests[@]}" '
            f"{RESTORE_RECEIPT_SELECTOR} --verbosity=2"
        ),
        "integration": "uv run pytest tests/integration --verbosity=2",
    }
    for shard, command in expected_commands.items():
        assert source.count(f"py_test-ci-{shard}:") == 1
        assert f"py_test-ci-{shard} is CI-only" in source
        assert command in source

    application_start = source.index("py_test-ci-application:")
    infrastructure_general_start = source.index("py_test-ci-infrastructure-general:")
    infrastructure_controller_start = source.index(
        "py_test-ci-infrastructure-controller:"
    )
    infrastructure_receipt_io_start = source.index(
        "py_test-ci-infrastructure-receipt-io:"
    )
    infrastructure_sanitizer_foundation_start = source.index(
        "py_test-ci-infrastructure-sanitizer-foundation:"
    )
    infrastructure_sanitizer_source_state_authority_start = source.index(
        "py_test-ci-infrastructure-sanitizer-source-state-authority:"
    )
    infrastructure_sanitizer_source_state_context_start = source.index(
        "py_test-ci-infrastructure-sanitizer-source-state-context:"
    )
    infrastructure_sanitizer_source_state_integrity_start = source.index(
        "py_test-ci-infrastructure-sanitizer-source-state-integrity:"
    )
    infrastructure_sanitizer_evidence_start = source.index(
        "py_test-ci-infrastructure-sanitizer-evidence:"
    )
    infrastructure_sanitizer_evidence_integrity_start = source.index(
        "py_test-ci-infrastructure-sanitizer-evidence-integrity:"
    )
    infrastructure_sanitizer_executor_start = source.index(
        "py_test-ci-infrastructure-sanitizer-executor:"
    )
    infrastructure_sanitizer_data_contract_executor_start = source.index(
        "py_test-ci-infrastructure-sanitizer-data-contract-executor:"
    )
    infrastructure_sanitizer_source_snapshot_start = source.index(
        "py_test-ci-infrastructure-sanitizer-source-snapshot:"
    )
    infrastructure_restore_start = source.index("py_test-ci-infrastructure-restore:")
    application_recipe = source[application_start:infrastructure_general_start]
    integration_start = source.index("py_test-ci-integration:")
    integration_end = source.index("\n# run tests with", integration_start)
    integration_recipe = source[integration_start:integration_end]
    infrastructure_general_recipe = source[
        infrastructure_general_start:infrastructure_controller_start
    ]
    infrastructure_controller_recipe = source[
        infrastructure_controller_start:infrastructure_receipt_io_start
    ]
    infrastructure_receipt_io_recipe = source[
        infrastructure_receipt_io_start:infrastructure_sanitizer_foundation_start
    ]
    infrastructure_sanitizer_foundation_recipe = source[
        infrastructure_sanitizer_foundation_start:infrastructure_sanitizer_source_state_authority_start
    ]
    infrastructure_sanitizer_source_state_authority_recipe = source[
        infrastructure_sanitizer_source_state_authority_start:infrastructure_sanitizer_source_state_context_start
    ]
    infrastructure_sanitizer_source_state_context_recipe = source[
        infrastructure_sanitizer_source_state_context_start:infrastructure_sanitizer_source_state_integrity_start
    ]
    infrastructure_sanitizer_source_state_integrity_recipe = source[
        infrastructure_sanitizer_source_state_integrity_start:infrastructure_sanitizer_evidence_start
    ]
    infrastructure_sanitizer_evidence_recipe = source[
        infrastructure_sanitizer_evidence_start:infrastructure_sanitizer_evidence_integrity_start
    ]
    infrastructure_sanitizer_evidence_integrity_recipe = source[
        infrastructure_sanitizer_evidence_integrity_start:infrastructure_sanitizer_executor_start
    ]
    infrastructure_sanitizer_executor_recipe = source[
        infrastructure_sanitizer_executor_start:infrastructure_sanitizer_data_contract_executor_start
    ]
    infrastructure_sanitizer_data_contract_executor_recipe = source[
        infrastructure_sanitizer_data_contract_executor_start:infrastructure_sanitizer_source_snapshot_start
    ]
    infrastructure_sanitizer_source_snapshot_recipe = source[
        infrastructure_sanitizer_source_snapshot_start:infrastructure_restore_start
    ]
    infrastructure_restore_recipe = source[
        infrastructure_restore_start:integration_start
    ]
    infrastructure_recipes = source[infrastructure_general_start:integration_start]
    shard_recipes = source[application_start:integration_end]
    for recipe in (application_recipe, integration_recipe):
        assert "just py_js-build" in recipe
    assert "just py_js-build" not in infrastructure_recipes
    assert infrastructure_general_recipe.count("--ignore-glob=") == 2
    assert "--ignore-glob=" not in infrastructure_controller_recipe
    assert "--ignore-glob=" not in infrastructure_receipt_io_recipe
    assert "--ignore-glob=" not in infrastructure_sanitizer_foundation_recipe
    assert (
        "--ignore-glob=" not in infrastructure_sanitizer_source_state_authority_recipe
    )
    assert "--ignore-glob=" not in infrastructure_sanitizer_source_state_context_recipe
    assert (
        "--ignore-glob=" not in infrastructure_sanitizer_source_state_integrity_recipe
    )
    assert "--ignore-glob=" not in infrastructure_sanitizer_evidence_recipe
    assert "--ignore-glob=" not in infrastructure_sanitizer_evidence_integrity_recipe
    assert "--ignore-glob=" not in infrastructure_sanitizer_executor_recipe
    assert (
        "--ignore-glob=" not in infrastructure_sanitizer_data_contract_executor_recipe
    )
    assert "--ignore-glob=" not in infrastructure_sanitizer_source_snapshot_recipe
    assert "--ignore-glob=" not in infrastructure_restore_recipe
    assert ":|dedicated_restore_tests" in infrastructure_restore_recipe
    assert ":#" not in infrastructure_restore_recipe
    assert "playwright install chromium --only-shell" not in application_recipe
    assert "localias" not in application_recipe.lower()
    assert "playwright install chromium --only-shell" not in integration_recipe
    assert "playwright install chromium --only-shell" not in shard_recipes

    test_files = {
        path.relative_to(ROOT)
        for pattern in ("test_*.py", "*_test.py")
        for path in (ROOT / "tests").rglob(pattern)
    }
    infrastructure_files = {
        path for path in test_files if path.parts[1] == "infrastructure"
    }
    source_state_files = {
        path
        for path in infrastructure_files
        if path.name.startswith("test_staging_restore_fixed_catalog_source_state")
    }
    assert source_state_files == {
        Path(selector) for selector in SANITIZER_SOURCE_STATE_SELECTORS
    }
    selected = {
        shard: {
            path
            for path in infrastructure_files
            if _infrastructure_shard(path) == shard
        }
        for shard in FILE_SHARDS
        if shard.startswith("infrastructure-")
    }
    selected["integration"] = {
        path for path in test_files if path.parts[1] == "integration"
    }
    selected["application"] = (
        test_files - infrastructure_files - selected["integration"]
    )

    assert all(selected.values())
    assert set().union(*selected.values()) == test_files
    for left_index, left in enumerate(FILE_SHARDS):
        for right in FILE_SHARDS[left_index + 1 :]:
            assert selected[left].isdisjoint(selected[right])


@pytest.mark.parametrize(
    ("path", "expected_shard"),
    [
        (
            Path("tests/infrastructure/test_staging_controller_receipt_v2_builder.py"),
            "infrastructure-receipt-io",
        ),
        (
            Path("tests/infrastructure/test_staging_controller_receipt_v2_writer.py"),
            "infrastructure-receipt-io",
        ),
        (
            Path("tests/infrastructure/test_staging_restore_sanitizer.py"),
            "infrastructure-sanitizer-foundation",
        ),
        (
            Path(
                "tests/infrastructure/"
                "test_staging_restore_fixed_catalog_source_state_authority.py"
            ),
            "infrastructure-sanitizer-source-state-authority",
        ),
        (
            Path(
                "tests/infrastructure/"
                "test_staging_restore_fixed_catalog_source_state_context.py"
            ),
            "infrastructure-sanitizer-source-state-context",
        ),
        (
            Path(
                "tests/infrastructure/"
                "test_staging_restore_fixed_catalog_source_state_integrity.py"
            ),
            "infrastructure-sanitizer-source-state-integrity",
        ),
        (
            Path(
                "tests/infrastructure/"
                "test_staging_restore_fixed_catalog_source_state_future.py"
            ),
            "unassigned",
        ),
        (
            Path("tests/infrastructure/test_staging_restore_sanitizer_evidence.py"),
            "infrastructure-sanitizer-evidence",
        ),
        (
            Path("tests/infrastructure/test_staging_restore_sanitizer_executor.py"),
            "infrastructure-sanitizer-executor",
        ),
        (
            Path(SANITIZER_TRANSACTION_PLAN_SELECTOR),
            "infrastructure-sanitizer-executor",
        ),
        (
            Path(SANITIZER_DATABASE_CAPABILITY_SELECTOR),
            "infrastructure-sanitizer-executor",
        ),
        (
            Path(SANITIZER_DATA_CONTRACT_EXECUTOR_SELECTOR),
            "infrastructure-sanitizer-data-contract-executor",
        ),
        (
            Path(SANITIZER_SOURCE_SNAPSHOT_SELECTOR),
            "infrastructure-sanitizer-source-snapshot",
        ),
        (
            Path(SANITIZER_POST_PROJECTION_SELECTOR),
            "infrastructure-sanitizer-data-contract-executor",
        ),
        (
            Path("tests/infrastructure/test_staging_restore_sanitizer_future.py"),
            "unassigned",
        ),
        (
            Path("tests/infrastructure/test_staging_controller_receipt_v2_consumer.py"),
            "unassigned",
        ),
        (
            Path("tests/infrastructure/test_staging_controller_receipt_v2_bogus.py"),
            "unassigned",
        ),
        (
            Path(
                "tests/infrastructure/test_staging_controller_receipt_v2_adversarial.py"
            ),
            "unassigned",
        ),
        (
            Path(
                "tests/infrastructure/test_staging_controller_receipt_v2_databaseish.py"
            ),
            "unassigned",
        ),
        (
            Path(
                "tests/infrastructure/test_staging_controller_receipt_v2_impossible.py"
            ),
            "unassigned",
        ),
        (
            Path(
                "tests/infrastructure/test_staging_controller_receipt_v2_providerish.py"
            ),
            "unassigned",
        ),
        (
            Path(
                "tests/infrastructure/test_staging_controller_receipt_v2_writerlike.py"
            ),
            "unassigned",
        ),
        (
            Path("tests/infrastructure/test_future_platform_contract.py"),
            "infrastructure-general",
        ),
    ],
)
def test_future_semantic_test_families_route_without_file_allowlists(
    path: Path, expected_shard: str
) -> None:
    assert _infrastructure_shard(path) == expected_shard


def test_source_state_shards_partition_all_36_collected_cases_once() -> None:
    authority = _collect_nodeids(SANITIZER_SOURCE_STATE_AUTHORITY_SELECTOR)
    context = _collect_nodeids(SANITIZER_SOURCE_STATE_CONTEXT_SELECTOR)
    integrity = _collect_nodeids(SANITIZER_SOURCE_STATE_INTEGRITY_SELECTOR)
    family = _collect_nodeids(*SANITIZER_SOURCE_STATE_SELECTORS)

    assert authority
    assert context
    assert integrity
    assert len(authority) == 7
    assert len(context) == 18
    assert len(integrity) == 11
    assert len(family) == 36
    assert len(authority) == len(set(authority))
    assert len(context) == len(set(context))
    assert len(integrity) == len(set(integrity))
    assert len(family) == len(set(family))
    assert set(authority).isdisjoint(context)
    assert set(authority).isdisjoint(integrity)
    assert set(context).isdisjoint(integrity)
    assert set(authority) | set(context) | set(integrity) == set(family)


def test_sanitizer_evidence_shards_partition_all_29_collected_cases_once() -> None:
    primary = _collect_nodeids(*SANITIZER_EVIDENCE_PRIMARY_SELECTORS)
    integrity = _collect_nodeids(*SANITIZER_EVIDENCE_INTEGRITY_SELECTORS)
    family = _collect_nodeids(SANITIZER_EVIDENCE_SELECTOR)

    assert primary
    assert integrity
    assert len(primary) == 19
    assert len(integrity) == 10
    assert len(family) == 29
    assert len(primary) == len(set(primary))
    assert len(integrity) == len(set(integrity))
    assert len(family) == len(set(family))
    assert set(primary).isdisjoint(integrity)
    assert set(primary) | set(integrity) == set(family)


def test_sanitizer_runtime_shards_partition_the_complete_family_once() -> None:
    evidence = _collect_nodeids(SANITIZER_EVIDENCE_SELECTOR)
    database_capability = _collect_nodeids(SANITIZER_DATABASE_CAPABILITY_SELECTOR)
    executor = _collect_nodeids(SANITIZER_EXECUTOR_SELECTOR)
    transaction_plan = _collect_nodeids(SANITIZER_TRANSACTION_PLAN_SELECTOR)
    data_contract_executor = _collect_nodeids(SANITIZER_DATA_CONTRACT_EXECUTOR_SELECTOR)
    source_snapshot = _collect_nodeids(SANITIZER_SOURCE_SNAPSHOT_SELECTOR)
    post_projection = _collect_nodeids(SANITIZER_POST_PROJECTION_SELECTOR)
    family = _collect_nodeids(*SANITIZER_RUNTIME_SELECTORS)

    assert evidence
    assert database_capability
    assert executor
    assert data_contract_executor
    assert len(evidence) == 29
    assert len(database_capability) == 63
    assert len(executor) == 21
    assert len(transaction_plan) == 29
    assert len(data_contract_executor) == 32
    assert len(source_snapshot) == 43
    assert len(post_projection) == 35
    assert len(family) == 252
    assert len(evidence) == len(set(evidence))
    assert len(database_capability) == len(set(database_capability))
    assert len(executor) == len(set(executor))
    assert len(transaction_plan) == len(set(transaction_plan))
    assert len(data_contract_executor) == len(set(data_contract_executor))
    assert len(source_snapshot) == len(set(source_snapshot))
    assert len(post_projection) == len(set(post_projection))
    assert len(family) == len(set(family))
    assert set(evidence).isdisjoint(database_capability)
    assert set(database_capability).isdisjoint(executor)
    assert set(database_capability).isdisjoint(transaction_plan)
    assert set(database_capability).isdisjoint(data_contract_executor)
    assert set(database_capability).isdisjoint(source_snapshot)
    assert set(database_capability).isdisjoint(post_projection)
    assert set(evidence).isdisjoint(executor)
    assert set(evidence).isdisjoint(data_contract_executor)
    assert set(executor).isdisjoint(data_contract_executor)
    assert set(transaction_plan).isdisjoint(
        set(evidence)
        | set(database_capability)
        | set(executor)
        | set(data_contract_executor)
    )
    assert set(source_snapshot).isdisjoint(
        set(evidence)
        | set(database_capability)
        | set(executor)
        | set(transaction_plan)
        | set(data_contract_executor)
    )
    assert set(post_projection).isdisjoint(
        set(evidence)
        | set(database_capability)
        | set(executor)
        | set(data_contract_executor)
        | set(source_snapshot)
    )
    assert set(evidence) | set(database_capability) | set(executor) | set(
        transaction_plan
    ) | set(data_contract_executor) | set(source_snapshot) | set(
        post_projection
    ) == set(family)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_backend_workers_are_parallel_isolated_and_fail_closed(path: Path) -> None:
    jobs = _workflow(path)["jobs"]
    quality = jobs["backend-quality"]
    tests = jobs["backend-tests"]
    aggregator = jobs["app"]

    assert quality["name"] in {"Backend quality", "Candidate backend quality"}
    assert quality["timeout-minutes"] == 20
    assert tests["timeout-minutes"] == 20
    assert tests["strategy"]["fail-fast"] is False
    matrix = tests["strategy"]["matrix"]["include"]
    assert [entry["shard"] for entry in matrix] == list(SHARDS)
    assert [entry["apt-packages"] for entry in matrix] == [
        "zsh",
        "zsh",
        "zsh",
        "zsh",
        "zsh",
        "zsh",
        "zsh",
        "zsh",
        "zsh",
        "zsh",
        "zsh",
        "zsh",
        "zsh",
        "zsh",
        "zsh libnss3-tools",
    ]
    assert "needs" not in quality or quality["needs"] == "authorize"
    assert "needs" not in tests or tests["needs"] == "authorize"

    quality_commands = _run_commands(quality)
    test_commands = _run_commands(tests)
    for commands in (quality_commands, test_commands):
        assert "just up --fast" in commands
        assert "just py_setup" in commands
        assert "just db_migrate" in commands
    assert "just py_lint" in quality_commands
    assert "just db_lint" in quality_commands
    assert "just py_lint" not in test_commands
    assert "just db_lint" not in test_commands
    assert "just py_test-ci-${{ matrix.shard }}" in test_commands

    localias_install = _named_step(tests, "Install SHA-verified Localias")
    localias_start_name = (
        "Start exact candidate SHA-verified local test routing"
        if path.name == "deploy_staging_candidate.yml"
        else "Start SHA-verified Localias"
    )
    localias_start = _named_step(tests, localias_start_name)
    chromium_install = _named_step(tests, "Install integration Chromium headless shell")
    for step in (localias_install, chromium_install, localias_start):
        assert step["if"] == "${{ matrix.shard == 'integration' }}"
    assert chromium_install["run"] == "uv run playwright install chromium --only-shell"
    steps = tests["steps"]
    assert (
        next(
            index
            for index, step in enumerate(steps)
            if step.get("run") == "just py_setup"
        )
        < steps.index(chromium_install)
        < steps.index(localias_start)
    )
    assert str(tests).count("playwright install chromium --only-shell") == 1
    assert not any("localias" in command.lower() for command in quality_commands)
    assert not any("playwright install" in command for command in quality_commands)

    artifact = _named_step(
        tests,
        (
            "Upload candidate backend shard test artifacts"
            if path.name == "deploy_staging_candidate.yml"
            else "Upload shard test artifacts"
        ),
    )
    assert artifact["if"] == "${{ failure() }}"
    assert "${{ matrix.shard }}" in artifact["with"]["name"]
    assert "${{ github.run_id }}" in artifact["with"]["name"]
    assert "${{ github.run_attempt }}" in artifact["with"]["name"]

    assert aggregator["name"] == (
        "Candidate Backend"
        if path.name == "deploy_staging_candidate.yml"
        else "Backend"
    )
    assert aggregator["needs"] == ["backend-quality", "backend-tests"]
    assert aggregator["if"] == "${{ always() }}"
    assert aggregator["timeout-minutes"] == 5
    assert "continue-on-error" not in quality
    assert "continue-on-error" not in tests
    assert "continue-on-error" not in aggregator


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
@pytest.mark.parametrize(
    ("quality_result", "tests_result", "expected_status"),
    [
        ("success", "success", 0),
        ("failure", "success", 1),
        ("cancelled", "success", 1),
        ("skipped", "success", 1),
        ("success", "failure", 1),
        ("success", "cancelled", 1),
        ("success", "skipped", 1),
    ],
)
def test_backend_aggregator_rejects_every_non_success_dependency(
    path: Path,
    quality_result: str,
    tests_result: str,
    expected_status: int,
) -> None:
    aggregator = _workflow(path)["jobs"]["app"]
    command = _named_step(aggregator, "Require every backend prerequisite to succeed")[
        "run"
    ]
    environment = os.environ | {
        "BACKEND_QUALITY_RESULT": quality_result,
        "BACKEND_TESTS_RESULT": tests_result,
    }
    completed = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{command}"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    assert completed.returncode == expected_status


def test_candidate_backend_workers_reverify_sha_without_shared_caches() -> None:
    jobs = _workflow(ROOT / ".github/workflows/deploy_staging_candidate.yml")["jobs"]
    for job_name in ("backend-quality", "backend-tests"):
        job = jobs[job_name]
        checkout = _named_step(job, "Check out exact candidate")
        verification = _named_step(job, "Verify exact candidate checkout")
        toolchain = _named_step(
            job, "Install exact candidate toolchain without shared caches"
        )
        assert checkout["with"]["ref"] == (
            "${{ needs.authorize.outputs.verified_revision }}"
        )
        assert checkout["with"]["persist-credentials"] is False
        assert 'test "$(git rev-parse HEAD)" = "$CANDIDATE_SHA"' in verification["run"]
        assert toolchain["with"]["cache"] == "false"


def test_shards_do_not_weaken_or_recombine_coverage() -> None:
    recipe_source = PYTHON_JUST.read_text(encoding="utf-8")
    shard_source = recipe_source[
        recipe_source.index("py_test-ci-application:") : recipe_source.index(
            "\n# run tests with"
        )
    ]
    assert "--cov-append" not in shard_source
    assert "xdist" not in shard_source
    assert "--deselect" not in shard_source
    assert " -k " not in shard_source

    for path in WORKFLOWS:
        tests = _workflow(path)["jobs"]["backend-tests"]
        assert "continue-on-error" not in tests
        assert "download-artifact" not in str(tests)
