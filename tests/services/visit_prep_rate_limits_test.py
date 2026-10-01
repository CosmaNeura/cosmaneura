"""Unit tests for public Visit Prep Redis rate-limit boundaries."""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from uuid import uuid4

import pytest

import app.services.visit_prep_rate_limits as rate_limits

_DEFAULT_REPLY = object()


class _Redis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}
        self.eval_calls: list[tuple[str, int, tuple[object, ...]]] = []
        self.reply: object = _DEFAULT_REPLY
        self.error: Exception | None = None

    def eval(self, script: str, key_count: int, *args: object) -> object:
        self.eval_calls.append((script, key_count, args))
        if self.error is not None:
            raise self.error
        if self.reply is not _DEFAULT_REPLY:
            return self.reply

        keys = args[:key_count]
        window = args[key_count]
        result: list[int] = []
        for key in keys:
            assert isinstance(key, str)
            count = self.counts.get(key, 0) + 1
            self.counts[key] = count
            if count == 1:
                self.expirations[key] = int(str(window))
            result.append(count)
        return result


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _Redis:
    instance = _Redis()
    monkeypatch.setattr(rate_limits, "get_rate_limit_redis", lambda: instance)
    return instance


@pytest.fixture
def recorded_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def digest(value: str, *, context: str) -> str:
        calls.append((value, context))
        return hashlib.sha256(f"{context}\0{value}".encode()).hexdigest()

    monkeypatch.setattr(rate_limits, "pseudonymous_digest", digest)
    return calls


def _token(byte: int = 1) -> str:
    body = base64.urlsafe_b64encode(bytes([byte]) * 32).rstrip(b"=").decode()
    return f"vp1_{body}"


def _keys(call: tuple[str, int, tuple[object, ...]]) -> tuple[str, ...]:
    _, key_count, args = call
    return tuple(str(key) for key in args[:key_count])


def test_network_stage_is_one_atomic_fixed_window_counter(
    redis: _Redis,
    recorded_digest: list[tuple[str, str]],
) -> None:
    network_signal = "203.0.113.8"

    rate_limits.enforce_network_rate_limit(network_signal=network_signal)

    assert len(redis.eval_calls) == 1
    script, key_count, args = redis.eval_calls[0]
    assert key_count == 1
    assert args[-1] == str(rate_limits.WINDOW_SECONDS)
    assert "redis.call('INCR', key)" in script
    assert "if count == 1 then" in script
    assert "redis.call('EXPIRE', key, window)" in script
    assert network_signal not in _keys(redis.eval_calls[0])[0]
    assert recorded_digest == [(network_signal, rate_limits._NETWORK_CONTEXT)]
    assert set(redis.expirations.values()) == {rate_limits.WINDOW_SECONDS}


def test_network_limit_allows_300_and_rejects_301(
    redis: _Redis,
    recorded_digest: list[tuple[str, str]],
) -> None:
    for _ in range(rate_limits.NETWORK_LIMIT):
        rate_limits.enforce_network_rate_limit(network_signal="198.51.100.4")

    with pytest.raises(rate_limits.RateLimited) as raised:
        rate_limits.enforce_network_rate_limit(network_signal="198.51.100.4")

    assert str(raised.value) == "rate_limited"
    assert raised.value.retry_after_seconds == 15 * 60
    assert len(redis.expirations) == 1
    assert set(redis.expirations.values()) == {15 * 60}


def test_atomic_fixed_window_script_against_real_redis(
    owned_rate_limit_redis, monkeypatch
) -> None:
    client: Any = owned_rate_limit_redis
    monkeypatch.setattr(rate_limits, "get_rate_limit_redis", lambda: client)
    namespace = uuid4().hex
    keys = (
        f"visit-prep:rate:test:{namespace}:first",
        f"visit-prep:rate:test:{namespace}:second",
    )
    client.delete(*keys)
    try:
        rate_limits._increment(keys, (2, 3))
        assert [int(value) for value in client.mget(keys)] == [1, 1]

        # Give each existing bucket a distinctive shorter TTL. The second Lua
        # invocation must increment atomically without refreshing either one.
        assert client.expire(keys[0], 37)
        assert client.expire(keys[1], 41)
        rate_limits._increment(keys, (2, 3))
        assert [int(value) for value in client.mget(keys)] == [2, 2]
        assert 0 < client.ttl(keys[0]) <= 37
        assert 0 < client.ttl(keys[1]) <= 41

        with pytest.raises(rate_limits.RateLimited):
            rate_limits._increment(keys, (2, 3))
        assert [int(value) for value in client.mget(keys)] == [3, 3]
    finally:
        client.delete(*keys)


def test_canonical_token_stage_uses_pseudonymous_context_separated_keys(
    redis: _Redis,
    recorded_digest: list[tuple[str, str]],
) -> None:
    token = _token()
    network_signal = "192.0.2.44"

    rate_limits.enforce_credential_rate_limits(
        network_signal=network_signal,
        access_token=token,
        confirmation=True,
    )

    assert len(redis.eval_calls) == 1
    _, key_count, args = redis.eval_calls[0]
    assert key_count == 4
    keys = _keys(redis.eval_calls[0])
    assert keys[0].startswith("visit-prep:rate:v1:credential-network:")
    assert keys[1].startswith("visit-prep:rate:v1:token:")
    assert keys[2].startswith("visit-prep:rate:v1:confirmation-network:")
    assert keys[3].startswith("visit-prep:rate:v1:confirmation:")
    assert len(set(keys)) == 4
    assert token not in "".join(keys)
    assert network_signal not in "".join(keys)
    assert args[-1] == str(rate_limits.WINDOW_SECONDS)
    assert recorded_digest == [
        (network_signal, rate_limits._CREDENTIAL_NETWORK_CONTEXT),
        (token, rate_limits._TOKEN_CONTEXT),
        (network_signal, rate_limits._CONFIRMATION_NETWORK_CONTEXT),
        (token, rate_limits._TOKEN_CONFIRMATION_CONTEXT),
    ]


def test_canonical_token_and_confirmation_limits_are_enforced(
    redis: _Redis,
    recorded_digest: list[tuple[str, str]],
) -> None:
    token = _token(2)
    for _ in range(rate_limits.CONFIRMATION_LIMIT):
        rate_limits.enforce_credential_rate_limits(
            network_signal="203.0.113.9",
            access_token=token,
            confirmation=True,
        )

    with pytest.raises(rate_limits.RateLimited):
        rate_limits.enforce_credential_rate_limits(
            network_signal="203.0.113.9",
            access_token=token,
            confirmation=True,
        )

    confirmation_key = next(key for key in redis.counts if ":confirmation:" in key)
    token_key = next(key for key in redis.counts if ":token:" in key)
    assert redis.counts[confirmation_key] == rate_limits.CONFIRMATION_LIMIT + 1
    assert redis.counts[token_key] == rate_limits.CONFIRMATION_LIMIT + 1

    redis.counts[token_key] = rate_limits.TOKEN_LIMIT
    with pytest.raises(rate_limits.RateLimited):
        rate_limits.enforce_credential_rate_limits(
            network_signal="203.0.113.9",
            access_token=token,
        )


def test_malformed_credentials_share_network_scoped_buckets(
    redis: _Redis,
    recorded_digest: list[tuple[str, str]],
) -> None:
    network_signal = "2001:db8::4"
    supplied_credentials = (
        None,
        "malformed",
        "attacker-controlled-" + ("x" * 20_000),
    )
    keys: list[str] = []

    for credential in supplied_credentials:
        rate_limits.enforce_credential_rate_limits(
            network_signal=network_signal,
            access_token=credential,
            confirmation=True,
        )
        keys.extend(_keys(redis.eval_calls[-1]))

    assert len(set(keys)) == 2
    assert all(network_signal not in key for key in keys)
    assert all(
        not credential or credential not in "".join(keys)
        for credential in supplied_credentials
    )
    assert recorded_digest == [
        item
        for _ in supplied_credentials
        for item in (
            (network_signal, rate_limits._CREDENTIAL_NETWORK_CONTEXT),
            (network_signal, rate_limits._CONFIRMATION_NETWORK_CONTEXT),
        )
    ]

    rate_limits.enforce_credential_rate_limits(
        network_signal="2001:db8::5",
        access_token="malformed",
        confirmation=True,
    )
    assert set(_keys(redis.eval_calls[-1])).isdisjoint(set(keys))


def test_canonical_candidate_buckets_never_depend_on_database_existence(
    redis: _Redis,
    recorded_digest: list[tuple[str, str]],
) -> None:
    first = _token(3)
    second = _token(4)

    for token in (first, second):
        rate_limits.enforce_credential_rate_limits(
            network_signal="192.0.2.55",
            access_token=token,
        )

    first_keys = _keys(redis.eval_calls[0])
    second_keys = _keys(redis.eval_calls[1])
    assert len(first_keys) == len(second_keys) == 2
    assert first_keys[0] == second_keys[0]
    assert first_keys[1] != second_keys[1]
    assert recorded_digest == [
        ("192.0.2.55", rate_limits._CREDENTIAL_NETWORK_CONTEXT),
        (first, rate_limits._TOKEN_CONTEXT),
        ("192.0.2.55", rate_limits._CREDENTIAL_NETWORK_CONTEXT),
        (second, rate_limits._TOKEN_CONTEXT),
    ]


@pytest.mark.parametrize("network_signal", [None, "", "x" * 257, "bad\ud800"])
def test_invalid_network_signals_collapse_to_a_bounded_sentinel(
    redis: _Redis,
    recorded_digest: list[tuple[str, str]],
    network_signal: str | None,
) -> None:
    rate_limits.enforce_network_rate_limit(network_signal=network_signal)

    assert recorded_digest[-1] == (
        rate_limits._UNAVAILABLE_NETWORK_SIGNAL,
        rate_limits._NETWORK_CONTEXT,
    )
    assert len(redis.counts) == 1


@pytest.mark.parametrize("access_token", [None, "", "vp1_invalid", _token() + "x"])
def test_malformed_token_never_creates_a_token_bucket(
    redis: _Redis,
    recorded_digest: list[tuple[str, str]],
    access_token: str | None,
) -> None:
    rate_limits.enforce_credential_rate_limits(
        network_signal="192.0.2.9",
        access_token=access_token,
    )

    assert len(redis.eval_calls) == 1
    assert len(_keys(redis.eval_calls[0])) == 1
    assert ":credential-network:" in _keys(redis.eval_calls[0])[0]
    assert recorded_digest == [("192.0.2.9", rate_limits._CREDENTIAL_NETWORK_CONTEXT)]


@pytest.mark.parametrize(
    "reply",
    [None, [], [0], [True], [1.5], ["not-an-integer"], [1, 2]],
)
def test_unexpected_redis_replies_fail_closed(
    redis: _Redis,
    recorded_digest: list[tuple[str, str]],
    reply: object,
) -> None:
    redis.reply = reply

    with pytest.raises(rate_limits.RateLimitUnavailable):
        rate_limits.enforce_network_rate_limit(network_signal="192.0.2.10")


def test_redis_and_digest_failures_are_safe_and_fail_closed(
    redis: _Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis.error = RuntimeError("redis leaked detail")
    with pytest.raises(rate_limits.RateLimitUnavailable) as redis_error:
        rate_limits.enforce_network_rate_limit(network_signal="sensitive-network")
    assert str(redis_error.value) == "rate_limit_unavailable"
    assert redis_error.value.__cause__ is None

    redis.eval_calls.clear()

    def fail_digest(value: str, *, context: str) -> str:
        raise RuntimeError(f"digest failed for {value} in {context}")

    monkeypatch.setattr(rate_limits, "pseudonymous_digest", fail_digest)
    with pytest.raises(rate_limits.RateLimitUnavailable) as digest_error:
        rate_limits.enforce_network_rate_limit(network_signal="sensitive-network")
    assert str(digest_error.value) == "rate_limit_unavailable"
    assert digest_error.value.__cause__ is None
    assert redis.eval_calls == []

    monkeypatch.setattr(
        rate_limits,
        "pseudonymous_digest",
        lambda value, *, context: value,
    )
    with pytest.raises(rate_limits.RateLimitUnavailable):
        rate_limits.enforce_network_rate_limit(network_signal="sensitive-network")
    assert redis.eval_calls == []


def test_wrapper_runs_network_before_credential_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def record_network(*, network_signal: str | None) -> None:
        calls.append(("network", {"network_signal": network_signal}))

    def record_credential(**kwargs: object) -> None:
        calls.append(("credential", kwargs))

    monkeypatch.setattr(rate_limits, "enforce_network_rate_limit", record_network)
    monkeypatch.setattr(
        rate_limits,
        "enforce_credential_rate_limits",
        record_credential,
    )

    rate_limits.enforce_rate_limits(
        network_signal="192.0.2.1",
        access_token=_token(),
        confirmation=True,
    )

    assert calls == [
        ("network", {"network_signal": "192.0.2.1"}),
        (
            "credential",
            {
                "network_signal": "192.0.2.1",
                "access_token": _token(),
                "confirmation": True,
            },
        ),
    ]


def test_public_rate_limit_client_has_bounded_fail_fast_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    sentinel = object()

    monkeypatch.setattr(rate_limits, "_RATE_LIMIT_REDIS", None)
    monkeypatch.setattr(rate_limits, "redis_url", lambda: "redis://example.test/0")

    def from_url(url: str, **kwargs: object) -> object:
        calls.append((url, kwargs))
        return sentinel

    monkeypatch.setattr(rate_limits.redis, "from_url", from_url)

    assert rate_limits.get_rate_limit_redis() is sentinel
    assert rate_limits.get_rate_limit_redis() is sentinel
    assert calls == [
        (
            "redis://example.test/0",
            {
                "retry_on_timeout": False,
                "socket_connect_timeout": 1.0,
                "socket_timeout": 1.0,
            },
        )
    ]
