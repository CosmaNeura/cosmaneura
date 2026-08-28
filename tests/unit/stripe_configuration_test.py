from __future__ import annotations

import pytest

import app.configuration.stripe as stripe_config


def _configure_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stripe_config, "STRIPE_INTEGRATION_ENABLED", True)
    monkeypatch.setattr(stripe_config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(stripe_config, "STRIPE_ACCOUNT_ID", "acct_test_x")
    monkeypatch.setattr(stripe_config, "STRIPE_PRICE_ID", "price_test_x")
    monkeypatch.setattr(stripe_config, "STRIPE_WEBHOOK_SECRET", "whsec_test_x")
    monkeypatch.setattr(stripe_config, "STRIPE_API_VERSION", "2025-09-30.clover")
    monkeypatch.setattr(stripe_config, "STRIPE_LIVE_MODE", False)


def test_portal_configuration_requires_pinned_bpc_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_runtime(monkeypatch)
    monkeypatch.setattr(stripe_config, "STRIPE_PORTAL_CONFIGURATION_ID", "bpc_test_x")

    assert stripe_config.stripe_portal_configured()


@pytest.mark.parametrize("configuration_id", ["", "pc_test_x", " bpc_test_x"])
def test_portal_configuration_rejects_missing_or_malformed_id(
    monkeypatch: pytest.MonkeyPatch, configuration_id: str
) -> None:
    _configure_runtime(monkeypatch)
    monkeypatch.setattr(
        stripe_config, "STRIPE_PORTAL_CONFIGURATION_ID", configuration_id
    )

    assert not stripe_config.stripe_portal_configured()


def test_portal_configuration_does_not_enable_disabled_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_runtime(monkeypatch)
    monkeypatch.setattr(stripe_config, "STRIPE_INTEGRATION_ENABLED", False)
    monkeypatch.setattr(stripe_config, "STRIPE_PORTAL_CONFIGURATION_ID", "bpc_test_x")

    assert not stripe_config.stripe_portal_configured()


def test_portal_configuration_requires_webhook_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_runtime(monkeypatch)
    monkeypatch.setattr(stripe_config, "STRIPE_PORTAL_CONFIGURATION_ID", "bpc_test_x")
    monkeypatch.setattr(stripe_config, "STRIPE_WEBHOOK_SECRET", "")

    assert not stripe_config.stripe_portal_configured()


def test_acquisition_requires_the_pinned_portal_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_runtime(monkeypatch)
    monkeypatch.setattr(stripe_config, "STRIPE_BILLING_ENABLED", True)
    monkeypatch.setattr(stripe_config, "STRIPE_WEBHOOK_SECRET", "whsec_test_x")
    monkeypatch.setattr(stripe_config, "STRIPE_PORTAL_CONFIGURATION_ID", "")

    assert not stripe_config.stripe_billing_configured()

    monkeypatch.setattr(stripe_config, "STRIPE_PORTAL_CONFIGURATION_ID", "bpc_test_x")
    assert stripe_config.stripe_billing_configured()
