"""Exercise reset guards without importing the global identity-cleanup hooks."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest


def _reset_namespace(environment: str = "test"):
    source = Path(__file__).parents[1] / "conftest.py"
    parsed = ast.parse(source.read_text())
    selected: list[ast.stmt] = [
        node
        for node in parsed.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "_APPEND_ONLY_TEST_TRIGGERS"
                for target in node.targets
            )
        )
        or (
            isinstance(node, ast.FunctionDef)
            and node.name == "database_reset_truncate_for_tests"
        )
    ]
    assert len(selected) == 2
    connection = Mock()
    engine = Mock()
    transaction = MagicMock()
    transaction.__enter__.return_value = connection
    engine.begin.return_value = transaction
    manager = Mock()
    manager.get_instance.return_value.get_engine.return_value = engine
    reset = Mock()
    namespace = {
        "os": SimpleNamespace(environ={"PYTHON_ENV": environment}),
        "SessionManager": manager,
        "text": str,
        "database_reset_truncate": reset,
    }
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"),
        namespace,
    )
    return namespace, connection, manager, reset


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_reset_restores_every_named_guard_even_when_cleanup_fails(cleanup_fails):
    namespace, connection, manager, reset = _reset_namespace()
    triggers = namespace["_APPEND_ONLY_TEST_TRIGGERS"]
    assert (
        triggers["billing_header_prepared_operation"]
        == "billing_header_operation_immutable"
    )
    assert triggers["billing_header_action"] == "billing_header_action_guard"

    def cleanup():
        assert connection.execute.call_count == len(triggers)
        if cleanup_fails:
            raise RuntimeError("synthetic cleanup failure")

    reset.side_effect = cleanup
    if cleanup_fails:
        with pytest.raises(RuntimeError, match="synthetic cleanup failure"):
            namespace["database_reset_truncate_for_tests"]()
    else:
        namespace["database_reset_truncate_for_tests"]()

    statements = [call.args[0] for call in connection.execute.call_args_list]
    expected = [
        f"ALTER TABLE {table} {operation} TRIGGER {trigger}"
        for operation in ("DISABLE", "ENABLE")
        for table, trigger in triggers.items()
    ]
    assert statements == expected
    reset.assert_called_once_with()
    assert (
        manager.get_instance.return_value.get_engine.return_value.begin.call_count == 2
    )


def test_reset_refuses_non_test_environment_before_database_access():
    namespace, connection, manager, reset = _reset_namespace("production")
    with pytest.raises(AssertionError):
        namespace["database_reset_truncate_for_tests"]()
    manager.get_instance.assert_not_called()
    connection.execute.assert_not_called()
    reset.assert_not_called()
