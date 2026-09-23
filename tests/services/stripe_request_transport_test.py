"""The HTTP boundary to Stripe: retries, backoff, and what a failure says.

Every other Stripe test stubs `_request` itself, so nothing exercised the
transport underneath it. These drive the real function through a mock transport.

Retry safety is the point. Stripe guarantees GET and DELETE are idempotent and
caches a keyed POST's result for 24 hours, so those may be replayed. A POST
without an idempotency key may not, and `create_portal_session` is exactly that
case.
"""

import httpx
import pytest

from app.configuration import stripe as stripe_config
from app.services import stripe_billing


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Satisfy `stripe_runtime_configured()` without touching a real account."""

    monkeypatch.setattr(stripe_config, "STRIPE_INTEGRATION_ENABLED", True)
    monkeypatch.setattr(stripe_config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(stripe_config, "STRIPE_ACCOUNT_ID", "acct_test_x")
    monkeypatch.setattr(stripe_config, "STRIPE_PRICE_ID", "price_test_x")
    monkeypatch.setattr(stripe_config, "STRIPE_LIVE_MODE", False)
    monkeypatch.setattr(
        stripe_config, "STRIPE_API_VERSION", stripe_config.SUPPORTED_STRIPE_API_VERSION
    )


@pytest.fixture
def slept(monkeypatch):
    """Record backoff instead of serving it, so these run in milliseconds."""

    recorded: list[float] = []
    monkeypatch.setattr(stripe_billing.time, "sleep", recorded.append)
    return recorded


def _drive(monkeypatch, responses):
    """Install a transport that replays `responses`, and log what it was asked."""

    calls: list[httpx.Request] = []
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        outcome = remaining.pop(0) if remaining else httpx.Response(200, json={})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(
        stripe_billing,
        "_HTTP_CLIENT",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return calls


def test_a_successful_request_is_not_retried(monkeypatch, slept):
    calls = _drive(monkeypatch, [httpx.Response(200, json={"id": "acct_test_x"})])

    assert stripe_billing._request("GET", "/account") == {"id": "acct_test_x"}
    assert len(calls) == 1
    assert slept == []


def test_a_server_error_on_a_get_is_retried_until_it_succeeds(monkeypatch, slept):
    calls = _drive(
        monkeypatch,
        [
            httpx.Response(500, json={"error": {"type": "api_error"}}),
            httpx.Response(200, json={"id": "sub_1"}),
        ],
    )

    assert stripe_billing._request("GET", "/subscriptions/sub_1") == {"id": "sub_1"}
    assert len(calls) == 2
    assert len(slept) == 1


def test_retries_stop_at_the_attempt_limit_and_raise_the_last_response(
    monkeypatch, slept
):
    calls = _drive(monkeypatch, [httpx.Response(503) for _ in range(5)])

    with pytest.raises(httpx.HTTPStatusError) as raised:
        stripe_billing._request("GET", "/account")

    assert len(calls) == stripe_billing._MAX_REQUEST_ATTEMPTS
    assert raised.value.response.status_code == 503


def test_a_keyed_post_is_replayed_because_stripe_caches_the_first_result(
    monkeypatch, slept
):
    calls = _drive(
        monkeypatch,
        [httpx.Response(500), httpx.Response(200, json={"id": "cs_1"})],
    )

    stripe_billing._request(
        "POST", "/checkout/sessions", {"a": "b"}, idempotency_key="key-1"
    )

    assert len(calls) == 2
    # The same key, or the replay would create a second Checkout Session.
    assert {call.headers["idempotency-key"] for call in calls} == {"key-1"}


def test_an_unkeyed_post_is_never_replayed(monkeypatch, slept):
    """`create_portal_session` posts without a key; replaying it is not safe."""

    calls = _drive(monkeypatch, [httpx.Response(500), httpx.Response(200, json={})])

    with pytest.raises(httpx.HTTPStatusError):
        stripe_billing._request("POST", "/billing_portal/sessions", {"customer": "cus"})

    assert len(calls) == 1
    assert slept == []


def test_stripe_should_retry_false_stops_a_retry_the_status_code_would_allow(
    monkeypatch, slept
):
    calls = _drive(
        monkeypatch,
        [httpx.Response(500, headers={"stripe-should-retry": "false"})],
    )

    with pytest.raises(httpx.HTTPStatusError):
        stripe_billing._request("GET", "/account")

    assert len(calls) == 1


def test_stripe_should_retry_true_forces_a_retry_the_status_code_would_refuse(
    monkeypatch, slept
):
    calls = _drive(
        monkeypatch,
        [
            httpx.Response(400, headers={"stripe-should-retry": "true"}),
            httpx.Response(200, json={}),
        ],
    )

    stripe_billing._request("GET", "/account")

    assert len(calls) == 2


def test_a_client_error_is_not_retried(monkeypatch, slept):
    calls = _drive(monkeypatch, [httpx.Response(400, json={"error": {"code": "x"}})])

    with pytest.raises(httpx.HTTPStatusError):
        stripe_billing._request("GET", "/account")

    assert len(calls) == 1
    assert slept == []


def test_rate_limiting_waits_the_interval_stripe_asked_for(monkeypatch, slept):
    _drive(
        monkeypatch,
        [
            httpx.Response(429, headers={"retry-after": "2"}),
            httpx.Response(200, json={}),
        ],
    )

    stripe_billing._request("GET", "/account")

    assert slept == [2.0]


def test_a_retry_after_beyond_the_ceiling_is_clamped(monkeypatch, slept):
    """A long Retry-After must not park a Celery worker for minutes."""

    _drive(
        monkeypatch,
        [
            httpx.Response(429, headers={"retry-after": "3600"}),
            httpx.Response(200, json={}),
        ],
    )

    stripe_billing._request("GET", "/account")

    assert slept == [stripe_billing._RETRY_MAX_DELAY_SECONDS]


def test_a_transport_error_is_retried_when_the_request_can_be_replayed(
    monkeypatch, slept
):
    """No response means the outcome is unknown; the key makes asking again safe."""

    calls = _drive(
        monkeypatch,
        [
            httpx.ConnectError("connection reset"),
            httpx.Response(200, json={"id": "cs_1"}),
        ],
    )

    stripe_billing._request(
        "POST", "/checkout/sessions", {"a": "b"}, idempotency_key="key-2"
    )

    assert len(calls) == 2


def test_a_transport_error_on_an_unkeyed_post_propagates(monkeypatch, slept):
    calls = _drive(monkeypatch, [httpx.ConnectError("connection reset")])

    with pytest.raises(httpx.ConnectError):
        stripe_billing._request("POST", "/billing_portal/sessions", {"customer": "cus"})

    assert len(calls) == 1


def test_the_failure_names_the_cause_instead_of_only_the_status(monkeypatch, slept):
    """Otherwise a decline is only diagnosable from the Stripe dashboard."""

    _drive(
        monkeypatch,
        [
            httpx.Response(
                402,
                headers={"request-id": "req_123"},
                json={
                    "error": {
                        "type": "card_error",
                        "code": "card_declined",
                        "decline_code": "insufficient_funds",
                        "message": "Your card has insufficient funds.",
                    }
                },
            )
        ],
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        stripe_billing._request("GET", "/account")

    message = str(raised.value)
    for expected in (
        "card_error",
        "card_declined",
        "insufficient_funds",
        "req_123",
        "402",
    ):
        assert expected in message


def test_backoff_grows_and_stays_within_the_ceiling(monkeypatch, slept):
    _drive(monkeypatch, [httpx.Response(500) for _ in range(5)])

    with pytest.raises(httpx.HTTPStatusError):
        stripe_billing._request("GET", "/account")

    assert len(slept) == stripe_billing._MAX_REQUEST_ATTEMPTS - 1
    assert all(0 < delay <= stripe_billing._RETRY_MAX_DELAY_SECONDS for delay in slept)
    # Jittered, so compare the envelopes rather than the values.
    assert slept[1] > slept[0] / 2


def test_an_unconfigured_deployment_never_reaches_the_network(monkeypatch, slept):
    monkeypatch.setattr(stripe_config, "STRIPE_INTEGRATION_ENABLED", False)
    calls = _drive(monkeypatch, [httpx.Response(200, json={})])

    with pytest.raises(stripe_billing.StripeNotConfigured):
        stripe_billing._request("GET", "/account")

    assert calls == []


def test_every_request_pins_the_api_version(monkeypatch, slept):
    calls = _drive(monkeypatch, [httpx.Response(200, json={})])

    stripe_billing._request("GET", "/account")

    assert (
        calls[0].headers["stripe-version"] == stripe_config.SUPPORTED_STRIPE_API_VERSION
    )
