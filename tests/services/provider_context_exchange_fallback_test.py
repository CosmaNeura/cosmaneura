from contextlib import nullcontext

import pytest

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.session_prep import SessionPrepProviderSettings
from app.services import provider_context_exchange as exchange
from app.services.provider_record_authority import (
    ProviderRecordScopeDenied,
    SessionPrepProviderScopeUnavailable,
)
from app.services.superadmin_capability import SuperadminCapabilityEnvironment


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prep_error: Exception,
    calls: list[str],
    generic_error: Exception | None = None,
) -> bool:
    transaction = object()
    monkeypatch.setattr(exchange, "get_session", lambda: nullcontext(transaction))
    monkeypatch.setattr(
        exchange.session_prep_scope,
        "synthetic_session_prep_context_ref",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        exchange.session_prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(enabled=True),
    )
    monkeypatch.setattr(
        exchange,
        "configured_superadmin_capability_environment",
        lambda: SuperadminCapabilityEnvironment.staging,
    )

    def prep_lock(**kwargs: object) -> None:
        assert kwargs == {"session": transaction, "clerk_subject": "user_test"}
        calls.append("prep")
        raise prep_error

    def generic_lock(**kwargs: object) -> None:
        assert kwargs == {"session": transaction, "clerk_subject": "user_test"}
        calls.append("generic_read")
        if generic_error is not None:
            raise generic_error

    monkeypatch.setattr(exchange, "lock_session_prep_record_owner", prep_lock)
    monkeypatch.setattr(exchange, "lock_provider_record_owner", generic_lock)
    monkeypatch.setattr(
        exchange,
        "context_token_exchange_blocked_by_view_as",
        lambda *_args, **_kwargs: calls.append("view_as") or False,
    )
    result = exchange.provider_exchange_blocked(
        issuer="https://test.clerk.invalid",
        subject="user_test",
        clerk_session_id="session_test",
        requested_surface="provider_assistant",
        record_ref="appointment_test",
        settings=AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
            clerk_issuer="https://test.clerk.invalid",
        ),
    )
    return result


def test_session_prep_unavailable_falls_back_to_generic_read(monkeypatch) -> None:
    calls: list[str] = []
    assert (
        _invoke(
            monkeypatch,
            prep_error=SessionPrepProviderScopeUnavailable(),
            calls=calls,
        )
        is False
    )
    assert calls == ["prep", "generic_read", "view_as"]


def test_expired_generic_read_denial_propagates(monkeypatch) -> None:
    expired = ProviderRecordScopeDenied("synthetic export period expired")
    calls: list[str] = []
    with pytest.raises(ProviderRecordScopeDenied) as exc_info:
        _invoke(
            monkeypatch,
            prep_error=SessionPrepProviderScopeUnavailable(),
            calls=calls,
            generic_error=expired,
        )
    assert exc_info.value is expired
    assert calls == ["prep", "generic_read"]


def test_unrelated_record_denial_never_falls_back(monkeypatch) -> None:
    record_error = ProviderRecordScopeDenied()
    calls: list[str] = []
    with pytest.raises(ProviderRecordScopeDenied) as exc_info:
        _invoke(monkeypatch, prep_error=record_error, calls=calls)
    assert exc_info.value is record_error
    assert calls == ["prep"]
