from __future__ import annotations

import pytest

from app.configuration.webhooks import validate_recall_webhook_secret


def test_recall_signing_secret_accepts_absent_outside_deployed_environments() -> None:
    assert validate_recall_webhook_secret("") == ""


def test_recall_signing_secret_accepts_valid_syntax() -> None:
    value = "whsec_dGVzdC1vbmx5LWtleS0xNg=="
    assert validate_recall_webhook_secret(value) == value


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_recall_signing_secret_is_required_at_api_startup(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("PYTHON_ENV", environment)

    with pytest.raises(ValueError, match="required for this environment"):
        validate_recall_webhook_secret("")


@pytest.mark.parametrize(
    "value",
    [
        "not-whsec",
        "whsec_",
        "whsec_not-base64!",
        " whsec_dGVzdA==",
        "whsec_dGVzdA==",
        "whsec_c3RhdGljLXRlc3Qta2V5",
        "whsec_AB==",
        "whsec_AAB=",
    ],
)
def test_recall_signing_secret_rejects_invalid_syntax_without_echoing_value(
    value: str,
) -> None:
    with pytest.raises(ValueError) as error:
        validate_recall_webhook_secret(value)

    assert value not in str(error.value)
