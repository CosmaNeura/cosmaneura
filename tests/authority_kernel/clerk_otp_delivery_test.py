"""Synthetic signed payloads and fake SMTP; never a real verification message."""

import asyncio
import base64
import copy
import hashlib
import hmac
import json
import os
import subprocess
from pathlib import Path

import pytest
from aiosmtplib.errors import (
    SMTPConnectError,
    SMTPDataError,
    SMTPReadTimeoutError,
    SMTPServerDisconnected,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mailers import Mailer
from mailers.transports.smtp import SMTPTransport

from app.configuration import clerk_otp_delivery as config
from app.configuration import emailer
from app.configuration import sentry as sentry_config
from app.routes import clerk_otp_delivery as route
from app.services.clerk_otp_delivery import RedisOtpLedger, RejectedOtp, prepare_otp

NOW = 1_800_000_000.0
KEY = b"synthetic-only-signing-key-1234567"
SETTINGS = config.OtpDeliverySettings("ins_synthetic", KEY)
EVENT = {
    "object": "event",
    "type": "email.created",
    "instance_id": SETTINGS.instance_id,
    "timestamp": int(NOW * 1000),
    "data": {
        "object": "email",
        "id": "email_synthetic",
        "slug": "verification_code",
        "to_email_address": "tester@synthetic.invalid",
        "delivered_by_clerk": False,
        "data": {"otp_code": "031729", "requested_from": "never-forward-this"},
        "subject": "never-forward-this",
        "body": "<a href='https://untrusted.invalid'>ignored</a>",
    },
}


def signed(
    body: bytes, now: float = NOW, delivery_id: str = "msg_synthetic"
) -> dict[str, str]:
    stamp = str(int(now))
    digest = hmac.new(
        KEY, delivery_id.encode() + b"." + stamp.encode() + b"." + body, hashlib.sha256
    ).digest()
    return {
        "svix-id": delivery_id,
        "svix-timestamp": stamp,
        "svix-signature": "v1," + base64.b64encode(digest).decode(),
    }


def body(event=None) -> bytes:
    return json.dumps(EVENT if event is None else event).encode()


def prepare(event=None, now=NOW):
    raw = body(event)
    headers = signed(raw, now)
    return prepare_otp(
        raw,
        delivery_id=headers["svix-id"],
        signature_timestamp=headers["svix-timestamp"],
        signatures=headers["svix-signature"],
        settings=SETTINGS,
        now=now,
    )


class OwnedRedis:
    """Exercise actual Redis Lua only inside the explicitly owned fixture."""

    def __init__(self, marker: Path):
        self.owner = json.loads(marker.read_text())
        self.prefix = ["docker", "--context", self.owner["context"]]
        metadata = json.loads(
            self.docker("inspect", "--format", "{{json .}}", self.owner["container_id"])
        )
        network = json.loads(
            self.docker(
                "network", "inspect", "--format", "{{json .}}", self.owner["network_id"]
            )
        )
        assert metadata["Config"]["Labels"]["scribe.otp.owner"] == self.owner["owner"]
        assert metadata["Image"] == self.owner["image"]
        assert (
            network["Internal"]
            and network["Labels"]["scribe.otp.owner"] == self.owner["owner"]
        )
        assert not any(m["Type"] == "volume" for m in metadata["Mounts"])
        assert all(
            b["HostIp"] == "127.0.0.1"
            for bindings in (metadata["NetworkSettings"]["Ports"] or {}).values()
            for b in (bindings or [])
        )

    def docker(self, *args):
        return subprocess.check_output(
            self.prefix + list(args), text=True, stderr=subprocess.PIPE, timeout=10
        ).strip()

    def command(self, *args):
        return self.docker(
            "exec", self.owner["container_id"], "redis-cli", "--raw", *map(str, args)
        )

    def eval(self, script, keys, *args):
        value = self.command("EVAL", script, keys, *args)
        if value.startswith("ERR "):
            raise RuntimeError("synthetic_redis_error")
        return int(value) if value in {"0", "1"} else value


@pytest.fixture
def redis_store():
    marker = os.environ.get("CLERK_OTP_TEST_OWNER_MARKER")
    if not marker:
        pytest.skip("requires an explicitly owned internal-only virtual Redis fixture")
    client = OwnedRedis(Path(marker))
    assert client.command("PING") == "PONG"
    assert client.command("FLUSHDB") == "OK"  # Only this new owned synthetic fixture.
    return RedisOtpLedger(client)


@pytest.fixture
def endpoint(monkeypatch, redis_store):
    sent = []
    now = [NOW]

    async def send(recipient, code):
        sent.append((recipient, code))

    monkeypatch.setenv("PYTHON_ENV", "staging")
    monkeypatch.setattr(config, "SETTINGS", SETTINGS)
    monkeypatch.setattr(route, "ledger", lambda: redis_store)
    monkeypatch.setattr(route, "clock", lambda: now[0])
    monkeypatch.setattr(route, "send_clerk_verification_code", send)
    app = FastAPI()
    app.include_router(route.router)
    return TestClient(app), sent, now, redis_store


def test_native_payload_projects_only_code_and_recipient():
    prepared = prepare()
    assert (
        prepared.recipient == "tester@synthetic.invalid" and prepared.code == "031729"
    )
    assert "031729" not in repr(prepared) and "tester@" not in repr(prepared)
    assert "never-forward" not in repr(prepared)


@pytest.mark.parametrize(
    "field,value",
    [
        ("instance_id", "ins_other"),
        ("type", "user.created"),
        ("object", "email"),
        ("timestamp", int(NOW)),
        ("timestamp", True),
        ("timestamp", str(int(NOW * 1000))),
        ("timestamp", int((NOW + 11) * 1000)),
    ],
)
def test_wrong_envelope_rejected(field, value):
    event = copy.deepcopy(EVENT)
    event[field] = value
    with pytest.raises(RejectedOtp):
        prepare(event)


@pytest.mark.parametrize(
    "field,value",
    [
        ("slug", "invitation"),
        ("slug", None),
        ("delivered_by_clerk", 0),
        ("suppression_reason", "blocked"),
        ("id", []),
        ("to_email_address", "a@synthetic.invalid\r\nBcc: outsider@synthetic.invalid"),
        ("to_email_address", ["tester@synthetic.invalid"]),
        ("to_email_address", "a@b..invalid"),
    ],
)
def test_wrong_email_resource_rejected(field, value):
    event = copy.deepcopy(EVENT)
    event["data"][field] = value
    with pytest.raises(RejectedOtp):
        prepare(event)


@pytest.mark.parametrize(
    "value", [None, 31729, "31729", "0317290", "０３１７２９", "03172\n"]
)
def test_typed_six_digit_code_required(value):
    event = copy.deepcopy(EVENT)
    event["data"]["data"]["otp_code"] = value
    with pytest.raises(RejectedOtp):
        prepare(event)


def test_retry_signature_does_not_refresh_original_queue_age():
    with pytest.raises(RejectedOtp, match="stale_event"):
        prepare(now=NOW + 121)
    with pytest.raises(RejectedOtp, match="stale_event"):
        prepare(now=NOW + 109)
    assert prepare(now=NOW + 30).deadline == NOW + 120


def test_tampering_duplicate_keys_and_oversize_are_rejected():
    raw = body()
    headers = signed(raw)
    args = dict(
        delivery_id=headers["svix-id"],
        signature_timestamp=headers["svix-timestamp"],
        signatures=headers["svix-signature"],
        settings=SETTINGS,
        now=NOW,
    )
    with pytest.raises(RejectedOtp, match="invalid_signature"):
        prepare_otp(raw + b" ", **args)
    with pytest.raises(RejectedOtp, match="body_too_large"):
        prepare_otp(b"x" * 65537, **args)
    raw = raw.replace(
        b'"otp_code": "031729"', b'"otp_code": "031729", "otp_code": "555555"'
    )
    args["signatures"] = signed(raw)["svix-signature"]
    with pytest.raises(RejectedOtp, match="unsupported_payload"):
        prepare_otp(raw, **args)


def test_same_email_replay_sends_once_and_stores_only_metadata(endpoint):
    client, sent, now, store = endpoint
    raw = body()
    assert client.post(route.PATH, content=raw, headers=signed(raw)).json() == {
        "status": "smtp_accepted"
    }
    now[0] += 30
    assert client.post(
        route.PATH, content=raw, headers=signed(raw, now[0], "msg_retried")
    ).json() == {"status": "duplicate"}
    assert sent == [("tester@synthetic.invalid", "031729")]
    record = json.loads(store.client.command("GET", prepare().ledger_key))
    assert set(record) == {
        "fingerprint",
        "owner",
        "attempts",
        "state",
        "lease_until",
        "retry_after",
    }
    assert record["state"] == "sent" and "tester@" not in json.dumps(record)
    assert all(value != "031729" for value in record.values())


def test_claim_concurrency_wrong_owner_and_payload_conflict(redis_store):
    prepared = prepare()
    assert redis_store.claim(prepared, "owner_a", NOW) == "claimed"
    assert redis_store.claim(prepared, "owner_b", NOW) == "busy"
    assert not redis_store.transition(
        prepared.ledger_key, "owner_b", "dispatching", NOW
    )
    assert redis_store.transition(prepared.ledger_key, "owner_a", "dispatching", NOW)
    altered = copy.deepcopy(EVENT)
    altered["data"]["data"]["otp_code"] = "999999"
    assert redis_store.claim(prepare(altered), "owner_c", NOW) == "conflict"


def test_expired_dispatch_owner_is_uncertain_not_automatically_repeated(redis_store):
    prepared = prepare()
    assert redis_store.claim(prepared, "owner_a", NOW) == "claimed"
    assert redis_store.transition(prepared.ledger_key, "owner_a", "dispatching", NOW)
    assert redis_store.claim(prepared, "owner_b", NOW + 31) == "uncertain"
    assert redis_store.claim(prepared, "owner_c", NOW + 32) == "uncertain"


def test_definite_failure_retries_bounded_but_never_acks_success(redis_store):
    prepared = prepare()
    for attempt in range(3):
        current = NOW + 3 * attempt
        owner = f"owner_{attempt}"
        assert redis_store.claim(prepared, owner, current) == "claimed"
        assert redis_store.transition(
            prepared.ledger_key, owner, "dispatching", current
        )
        assert redis_store.transition(prepared.ledger_key, owner, "retry", current)
    assert redis_store.claim(prepared, "owner_four", NOW + 12) == "exhausted"


def test_unsupported_owned_payload_never_creates_success_receipt(endpoint):
    client, sent, _, store = endpoint
    event = copy.deepcopy(EVENT)
    del event["data"]["data"]["otp_code"]
    raw = body(event)
    result = client.post(route.PATH, content=raw, headers=signed(raw))
    assert result.status_code == 422 and not sent
    assert store.client.command("DBSIZE") == "0"


def test_http_definite_failure_can_retry_success(endpoint, monkeypatch):
    client, sent, now, _ = endpoint

    async def failed(*_):
        raise emailer.SmtpNotAccepted()

    monkeypatch.setattr(route, "send_clerk_verification_code", failed)
    raw = body()
    result = client.post(route.PATH, content=raw, headers=signed(raw))
    assert result.status_code == 503 and result.json()["status"] == "delivery_retry"

    async def accepted(recipient, code):
        sent.append((recipient, code))

    monkeypatch.setattr(route, "send_clerk_verification_code", accepted)
    now[0] += 3
    assert (
        client.post(route.PATH, content=raw, headers=signed(raw, now[0])).status_code
        == 200
    )
    assert len(sent) == 1


def test_unknown_smtp_outcome_stays_visible_without_automatic_repeat(
    endpoint, monkeypatch
):
    client, sent, now, _ = endpoint

    async def unknown(recipient, code):
        sent.append((recipient, code))
        raise emailer.SmtpOutcomeUnknown()

    monkeypatch.setattr(route, "send_clerk_verification_code", unknown)
    raw = body()
    assert client.post(route.PATH, content=raw, headers=signed(raw)).status_code == 503
    now[0] += 3
    assert (
        client.post(route.PATH, content=raw, headers=signed(raw, now[0])).status_code
        == 503
    )
    assert len(sent) == 1


def test_disabled_and_production_routes_cannot_send(endpoint, monkeypatch):
    client, sent, _, _ = endpoint
    raw = body()
    monkeypatch.setattr(config, "SETTINGS", None)
    assert client.post(route.PATH, content=raw, headers=signed(raw)).status_code == 503
    monkeypatch.setattr(config, "SETTINGS", SETTINGS)
    monkeypatch.setenv("PYTHON_ENV", "production")
    assert (
        client.post(route.PATH, content=raw, headers=signed(raw)).status_code == 503
        and not sent
    )


def test_settings_require_explicit_staging_identity_and_contract(monkeypatch):
    monkeypatch.setenv("CLERK_OTP_DELIVERY_ENABLED", "false")
    assert config.load_settings() is None
    monkeypatch.setenv("CLERK_OTP_DELIVERY_ENABLED", "true")
    monkeypatch.setenv("PYTHON_ENV", "staging")
    monkeypatch.setenv("AUTHORITY_CLERK_ISSUER", config.STAGING_ISSUER)
    monkeypatch.setenv("CLERK_OTP_CONTRACT_VERSION", config.CONTRACT_VERSION)
    monkeypatch.setenv("CLERK_OTP_INSTANCE_ID", config.STAGING_INSTANCE_ID)
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "synthetic@cosmaneura.com")
    monkeypatch.setenv(
        "CLERK_OTP_WEBHOOK_SECRET", "whsec_" + base64.b64encode(KEY).decode()
    )
    assert config.load_settings() == config.OtpDeliverySettings(
        config.STAGING_INSTANCE_ID, KEY
    )
    for name, value in [
        ("PYTHON_ENV", "production"),
        ("AUTHORITY_CLERK_ISSUER", "https://other.invalid"),
        ("CLERK_OTP_INSTANCE_ID", "ins_other"),
        ("CLERK_OTP_CONTRACT_VERSION", ""),
        ("CLERK_OTP_CONTRACT_VERSION", "verification_code:six-digits:v2"),
        ("EMAIL_FROM_ADDRESS", "synthetic@other.invalid"),
        ("EMAIL_FROM_ADDRESS", "a@cosmaneura.com,b@cosmaneura.com"),
        ("EMAIL_FROM_ADDRESS", "a@cosmaneura.com\r\nBcc: outsider@synthetic.invalid"),
    ]:
        with monkeypatch.context() as patch:
            patch.setenv(name, value)
            with pytest.raises(ValueError, match="configuration_invalid"):
                config.load_settings()
    with monkeypatch.context() as patch:
        patch.delenv("CLERK_OTP_CONTRACT_VERSION")
        patch.setenv("CLERK_OTP_VERIFIED_CONTRACT", config.CONTRACT_VERSION)
        with pytest.raises(ValueError, match="configuration_invalid"):
            config.load_settings()


@pytest.mark.parametrize("size", [24, 32])
def test_settings_accept_standard_svix_and_custom_signing_key_sizes(monkeypatch, size):
    synthetic_key = b"s" * size
    for name, value in {
        "CLERK_OTP_DELIVERY_ENABLED": "true",
        "PYTHON_ENV": "staging",
        "AUTHORITY_CLERK_ISSUER": config.STAGING_ISSUER,
        "CLERK_OTP_CONTRACT_VERSION": config.CONTRACT_VERSION,
        "CLERK_OTP_INSTANCE_ID": config.STAGING_INSTANCE_ID,
        "EMAIL_FROM_ADDRESS": "synthetic@cosmaneura.com",
        "CLERK_OTP_WEBHOOK_SECRET": "whsec_" + base64.b64encode(synthetic_key).decode(),
    }.items():
        monkeypatch.setenv(name, value)
    assert config.load_settings() == config.OtpDeliverySettings(
        config.STAGING_INSTANCE_ID, synthetic_key
    )


@pytest.mark.parametrize(
    "secret",
    [
        "whsec_",
        "whsec_" + base64.b64encode(b"s" * 23).decode(),
        "whsec_%%%",
        "whsec_" + base64.b64encode(b"s" * 24).decode() + "\n",
        "wrong_" + base64.b64encode(b"s" * 24).decode(),
        base64.b64encode(b"s" * 24).decode(),
    ],
)
def test_settings_reject_short_or_malformed_vendor_signing_key(monkeypatch, secret):
    for name, value in {
        "CLERK_OTP_DELIVERY_ENABLED": "true",
        "PYTHON_ENV": "staging",
        "AUTHORITY_CLERK_ISSUER": config.STAGING_ISSUER,
        "CLERK_OTP_CONTRACT_VERSION": config.CONTRACT_VERSION,
        "CLERK_OTP_INSTANCE_ID": config.STAGING_INSTANCE_ID,
        "EMAIL_FROM_ADDRESS": "synthetic@cosmaneura.com",
        "CLERK_OTP_WEBHOOK_SECRET": secret,
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="^clerk_otp_delivery_configuration_invalid$"):
        config.load_settings()


def test_standard_svix_key_preserves_sha256_raw_body_verification(monkeypatch):
    key = b"s" * 24
    monkeypatch.setitem(signed.__globals__, "KEY", key)
    settings = config.OtpDeliverySettings(SETTINGS.instance_id, key)
    raw = body()
    headers = signed(raw)
    arguments = dict(
        delivery_id=headers["svix-id"],
        signature_timestamp=headers["svix-timestamp"],
        signatures=headers["svix-signature"],
        settings=settings,
        now=NOW,
    )
    assert prepare_otp(raw, **arguments).recipient == EVENT["data"]["to_email_address"]
    with pytest.raises(RejectedOtp):
        prepare_otp(raw + b" ", **arguments)
    with pytest.raises(RejectedOtp):
        prepare_otp(
            raw,
            **(
                arguments
                | {
                    "settings": config.OtpDeliverySettings(
                        SETTINGS.instance_id, b"t" * 24
                    )
                }
            ),
        )


@pytest.mark.parametrize(
    "failure,expected",
    [
        (SMTPConnectError("synthetic detail"), emailer.SmtpNotAccepted),
        (SMTPDataError(451, "synthetic detail"), emailer.SmtpNotAccepted),
        (SMTPReadTimeoutError("synthetic detail"), emailer.SmtpOutcomeUnknown),
        (SMTPServerDisconnected("synthetic detail"), emailer.SmtpOutcomeUnknown),
        (TimeoutError("synthetic detail"), emailer.SmtpOutcomeUnknown),
        (asyncio.CancelledError("synthetic detail"), emailer.SmtpOutcomeUnknown),
    ],
)
def test_smtp_classifies_only_fixed_outcomes_without_exception_content(
    monkeypatch, failure, expected
):
    async def send(_, **kwargs):
        raise failure

    monkeypatch.setattr(
        emailer,
        "_mailer",
        Mailer(
            SMTPTransport(host="synthetic.invalid"),
            from_address="synthetic@cosmaneura.com",
        ),
    )
    monkeypatch.setattr(emailer.aiosmtplib, "send", send)
    with pytest.raises(expected) as error:
        asyncio.run(
            emailer.send_clerk_verification_code("tester@synthetic.invalid", "031729")
        )
    assert str(error.value) == ""


@pytest.mark.parametrize("implicit_tls", [False, True])
def test_fixed_email_and_certificate_required_tls(monkeypatch, implicit_tls):
    messages = []

    async def send(message, **kwargs):
        messages.append((message, kwargs))
        return {}, "synthetic accepted"

    monkeypatch.setattr(
        emailer,
        "_mailer",
        Mailer(
            SMTPTransport(
                host="synthetic.invalid", use_tls=implicit_tls, validate_certs=False
            ),
            from_address="Scribe <synthetic@cosmaneura.com>",
        ),
    )
    monkeypatch.setattr(emailer.aiosmtplib, "send", send)
    asyncio.run(
        emailer.send_clerk_verification_code("tester@synthetic.invalid", "031729")
    )
    assert len(messages) == 1
    message, options = messages[0]
    assert message["To"] == "tester@synthetic.invalid"
    assert message["From"] == "synthetic@cosmaneura.com"
    assert message["Subject"] == "Your Scribe Beta verification code"
    assert not any(
        message.get(header)
        for header in ["Cc", "Bcc", "Reply-To", "Sender", "Return-Path"]
    )
    content = "\n".join(
        part.get_content()
        for part in message.walk()
        if part.get_content_type() in {"text/plain", "text/html"}
    )
    assert (
        "031729" in content
        and "never-forward" not in content
        and "https://" not in content
    )
    assert options["validate_certs"] is True
    assert options["use_tls"] is implicit_tls and options["start_tls"] is (
        not implicit_tls
    )
    assert options["sender"] == "synthetic@cosmaneura.com" and options[
        "recipients"
    ] == ["tester@synthetic.invalid"]
    assert options["timeout"] == 8


@pytest.mark.parametrize(
    "transport,sender,recipient",
    [
        (
            "console://?stream=stdout",
            "synthetic@cosmaneura.com",
            "tester@synthetic.invalid",
        ),
        ("memory://", "synthetic@cosmaneura.com", "tester@synthetic.invalid"),
        (
            SMTPTransport(host="synthetic.invalid"),
            "synthetic@other.invalid",
            "tester@synthetic.invalid",
        ),
        (
            SMTPTransport(host="synthetic.invalid"),
            "a@cosmaneura.com,b@cosmaneura.com",
            "tester@synthetic.invalid",
        ),
        (
            SMTPTransport(host="synthetic.invalid"),
            "synthetic@cosmaneura.com\r\nBcc: outsider@synthetic.invalid",
            "tester@synthetic.invalid",
        ),
        (
            SMTPTransport(host="synthetic.invalid"),
            "synthetic@cosmaneura.com",
            "one@synthetic.invalid,two@synthetic.invalid",
        ),
    ],
)
def test_non_smtp_or_unsafe_envelope_cannot_send(
    monkeypatch, transport, sender, recipient
):
    async def forbidden(*args, **kwargs):
        pytest.fail("must not reach transport")

    monkeypatch.setattr(emailer, "_mailer", Mailer(transport, from_address=sender))
    monkeypatch.setattr(emailer.aiosmtplib, "send", forbidden)
    with pytest.raises(emailer.SmtpNotAccepted):
        asyncio.run(emailer.send_clerk_verification_code(recipient, "031729"))


@pytest.mark.parametrize(
    "result,expected",
    [
        (
            ({"tester@synthetic.invalid": (550, "synthetic refused")}, "unused"),
            emailer.SmtpNotAccepted,
        ),
        (
            ({"other@synthetic.invalid": (550, "synthetic refused")}, "unused"),
            emailer.SmtpOutcomeUnknown,
        ),
        (None, emailer.SmtpOutcomeUnknown),
        (({}, None), emailer.SmtpOutcomeUnknown),
    ],
)
def test_normal_transport_return_never_ignores_refusals(monkeypatch, result, expected):
    async def send(*args, **kwargs):
        return result

    monkeypatch.setattr(
        emailer,
        "_mailer",
        Mailer(
            SMTPTransport(host="synthetic.invalid"),
            from_address="synthetic@cosmaneura.com",
        ),
    )
    monkeypatch.setattr(emailer.aiosmtplib, "send", send)
    with pytest.raises(expected):
        asyncio.run(
            emailer.send_clerk_verification_code("tester@synthetic.invalid", "031729")
        )


def test_telemetry_error_and_transaction_hooks_drop_otp_url_and_locals(monkeypatch):
    options = {}
    monkeypatch.setattr(sentry_config, "sentry_enabled", lambda: True)
    monkeypatch.setattr(sentry_config, "is_job_monitor", lambda: False)
    monkeypatch.setattr(
        sentry_config.sentry_sdk, "init", lambda **kwargs: options.update(kwargs)
    )
    monkeypatch.setenv("SENTRY_DSN", "https://synthetic@synthetic.invalid/1")
    monkeypatch.setenv("BUILD_COMMIT", "synthetic")
    sentry_config.configure_sentry()
    assert (
        options["include_local_variables"] is False
        and options["send_default_pii"] is False
    )
    for hook in ["before_send", "before_send_transaction"]:
        for url in [
            "https://staging.invalid" + route.PATH,
            route.PATH + "?unused=ignored",
            "https://staging.invalid" + route.PATH + "?unused=ignored#fragment",
        ]:
            event = {
                "request": {"url": url, "data": EVENT},
                "extra": EVENT,
                "exception": {
                    "values": [
                        {"value": "031729", "stacktrace": {"frames": [{"vars": EVENT}]}}
                    ]
                },
            }
            assert options[hook](event, None) is None
    ordinary = {
        "exception": {
            "values": [{"value": "031729", "stacktrace": {"frames": [{"vars": EVENT}]}}]
        }
    }
    scrubbed = sentry_config.scrub_event(ordinary)
    assert scrubbed == {"exception": {"values": [{"stacktrace": {"frames": [{}]}}]}}


def test_old_signature_and_non_ascii_signature_fail_closed():
    raw = body()
    headers = signed(raw, NOW - 301)
    for signature, stamp in [
        (headers["svix-signature"], headers["svix-timestamp"]),
        ("v1,é", str(int(NOW))),
    ]:
        with pytest.raises(RejectedOtp, match="invalid_signature"):
            prepare_otp(
                raw,
                delivery_id=headers["svix-id"],
                signature_timestamp=stamp,
                signatures=signature,
                settings=SETTINGS,
                now=NOW,
            )


@pytest.mark.parametrize(
    "length,status", [("65537", 413), ("invalid", 400), ("-1", 400)]
)
def test_http_length_rejection_precedes_any_delivery(endpoint, length, status):
    client, sent, _, store = endpoint
    raw = body()
    headers = signed(raw) | {"content-length": length}
    assert client.post(route.PATH, content=raw, headers=headers).status_code == status
    assert not sent and store.client.command("DBSIZE") == "0"


def test_stale_after_claim_never_dispatches(endpoint, monkeypatch):
    client, sent, now, store = endpoint
    claim = store.claim

    def slow_claim(*args):
        result = claim(*args)
        now[0] += 109
        return result

    monkeypatch.setattr(store, "claim", slow_claim)
    raw = body()
    result = client.post(route.PATH, content=raw, headers=signed(raw))
    assert (
        result.status_code == 422
        and result.json()["status"] == "stale_event"
        and not sent
    )
    assert (
        json.loads(store.client.command("GET", prepare().ledger_key))["state"]
        == "claimed"
    )


@pytest.mark.parametrize("failure_phase", ["claim", "dispatch", "completion"])
def test_redis_failure_never_falsely_acknowledges_or_repeats(
    endpoint, monkeypatch, failure_phase
):
    client, sent, now, store = endpoint
    transition = store.transition
    if failure_phase == "claim":
        monkeypatch.setattr(
            store,
            "claim",
            lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic")),
        )
    else:

        def failed_transition(key, owner, state, at):
            if (failure_phase == "dispatch" and state == "dispatching") or (
                failure_phase == "completion" and state == "sent"
            ):
                raise RuntimeError("synthetic")
            return transition(key, owner, state, at)

        monkeypatch.setattr(store, "transition", failed_transition)
    raw = body()
    assert client.post(route.PATH, content=raw, headers=signed(raw)).status_code == 503
    assert len(sent) == (1 if failure_phase == "completion" else 0)
    if failure_phase == "completion":
        monkeypatch.setattr(store, "transition", transition)
        now[0] += 31
        assert (
            client.post(
                route.PATH, content=raw, headers=signed(raw, now[0])
            ).status_code
            == 503
        )
        assert len(sent) == 1


def test_outer_smtp_deadline_cancels_transport_and_is_uncertain(monkeypatch):
    cancelled = []
    original_wait_for = asyncio.wait_for

    async def blocked_send(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def short_wait_for(awaitable, timeout):
        assert timeout == 8
        return await original_wait_for(awaitable, timeout=0.001)

    monkeypatch.setattr(
        emailer,
        "_mailer",
        Mailer(
            SMTPTransport(host="synthetic.invalid"),
            from_address="synthetic@cosmaneura.com",
        ),
    )
    monkeypatch.setattr(emailer.aiosmtplib, "send", blocked_send)
    monkeypatch.setattr(emailer.asyncio, "wait_for", short_wait_for)
    with pytest.raises(emailer.SmtpOutcomeUnknown):
        asyncio.run(
            emailer.send_clerk_verification_code("tester@synthetic.invalid", "031729")
        )
    assert cancelled == [True]


def test_malformed_telemetry_url_cannot_leak_or_break_scrubber():
    assert (
        sentry_config.scrub_event(
            {"request": {"url": "https://[invalid" + route.PATH, "data": EVENT}}
        )
        is None
    )


@pytest.mark.parametrize("slug", ["verification_code", "invitation", "password_reset"])
def test_clerk_owned_email_receipt_replays_without_send_or_ledger(
    endpoint, monkeypatch, slug
):
    client, sent, now, store = endpoint
    event = copy.deepcopy(EVENT)
    event["data"]["slug"] = slug
    event["data"]["delivered_by_clerk"] = True
    event["data"]["data"] = {"never_read_as_otp": {"arbitrary": "ignored"}}
    raw = body(event)

    def forbidden_ledger():
        pytest.fail("Clerk-owned receipt must not create/open a delivery ledger")

    monkeypatch.setattr(route, "ledger", forbidden_ledger)
    for elapsed, delivery_id in [
        (0, "msg_owned"),
        (30, "msg_owned_replay"),
        (500, "msg_owned_late"),
    ]:
        now[0] = NOW + elapsed
        result = client.post(
            route.PATH, content=raw, headers=signed(raw, now[0], delivery_id)
        )
        assert result.status_code == 200 and result.json() == {
            "status": "ignored_clerk_owned"
        }
        assert "no-store" in result.headers["cache-control"]
    assert not sent and store.client.command("DBSIZE") == "0"
    assert prepare(event) is None


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("foreign_instance", 403),
        ("missing_id", 422),
        ("missing_slug", 422),
        ("missing_recipient", 422),
        ("unsafe_recipient", 422),
        ("wrong_object", 422),
        ("wrong_event_type", 422),
    ],
)
def test_clerk_owned_receipt_requires_signed_scoped_wellformed_event(
    endpoint, mutation, expected
):
    client, sent, _, store = endpoint
    event = copy.deepcopy(EVENT)
    event["data"]["delivered_by_clerk"] = True
    if mutation == "foreign_instance":
        event["instance_id"] = "ins_other"
    elif mutation.startswith("missing_"):
        del event["data"][
            {
                "missing_id": "id",
                "missing_slug": "slug",
                "missing_recipient": "to_email_address",
            }[mutation]
        ]
    elif mutation == "unsafe_recipient":
        event["data"]["to_email_address"] = (
            "one@synthetic.invalid,two@synthetic.invalid"
        )
    elif mutation == "wrong_object":
        event["data"]["object"] = "user"
    else:
        event["type"] = "user.created"
    raw = body(event)
    result = client.post(route.PATH, content=raw, headers=signed(raw))
    assert result.status_code == expected and not sent
    assert store.client.command("DBSIZE") == "0"


def test_clerk_owned_receipt_cannot_ack_bad_signature(endpoint):
    client, sent, _, store = endpoint
    event = copy.deepcopy(EVENT)
    event["data"]["delivered_by_clerk"] = True
    raw = body(event)
    result = client.post(route.PATH, content=raw + b" ", headers=signed(raw))
    assert result.status_code == 401 and not sent
    assert store.client.command("DBSIZE") == "0"


@pytest.mark.parametrize("ownership", [None, "true", "false", 1, 0])
def test_unknown_ownership_never_acknowledges_receipt(endpoint, ownership):
    client, sent, _, store = endpoint
    event = copy.deepcopy(EVENT)
    event["data"]["delivered_by_clerk"] = ownership
    raw = body(event)
    assert client.post(route.PATH, content=raw, headers=signed(raw)).status_code == 422
    assert not sent and store.client.command("DBSIZE") == "0"


def test_custom_owned_unknown_template_is_not_receipt_ack(endpoint):
    client, sent, _, store = endpoint
    event = copy.deepcopy(EVENT)
    event["data"]["slug"] = "invitation"
    raw = body(event)
    assert client.post(route.PATH, content=raw, headers=signed(raw)).status_code == 403
    assert not sent and store.client.command("DBSIZE") == "0"
