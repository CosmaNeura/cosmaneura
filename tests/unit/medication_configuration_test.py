import pytest

from app.configuration.medication import held_medication_writes_enabled


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ("production", False),
        ("staging", True),
        ("test", True),
        ("development", True),
        ("unknown", False),
    ],
    ids=["production", "staging", "test", "development", "unknown"],
)
def test_new_holds_default_off_until_production_reader_activation(
    monkeypatch: pytest.MonkeyPatch, environment: str, expected: bool
):
    monkeypatch.setenv("PYTHON_ENV", environment)
    monkeypatch.delenv("HELD_MEDICATION_WRITES_ENABLED", raising=False)
    assert held_medication_writes_enabled() is expected


@pytest.mark.parametrize("environment", ["production", "staging"])
@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("false", False)],
    ids=["enabled", "disabled"],
)
def test_explicit_hold_release_control_uses_normal_boolean_parser(
    monkeypatch: pytest.MonkeyPatch, environment: str, value: str, expected: bool
):
    monkeypatch.setenv("PYTHON_ENV", environment)
    monkeypatch.setenv("HELD_MEDICATION_WRITES_ENABLED", value)
    assert held_medication_writes_enabled() is expected


def test_invalid_hold_release_control_does_not_enable_writes(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HELD_MEDICATION_WRITES_ENABLED", "not-a-boolean")
    with pytest.raises(ValueError):
        held_medication_writes_enabled()
