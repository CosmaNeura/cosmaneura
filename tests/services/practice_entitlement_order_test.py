"""Exhaustive entitlement properties for the 31 August existing-user lock."""

from datetime import datetime, timedelta, timezone
from itertools import product

import pytest

from app.models.practice import (
    ENTITLED_SUBSCRIPTION_STATUSES,
    KNOWN_SUBSCRIPTION_STATUSES,
    Practice,
    PracticeAcquisitionSource,
)

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def _practice(status, **kwargs):
    return Practice(
        legal_name="Synthetic",
        join_code="SYNTHF1",
        subscription_status=status,
        **kwargs,
    )


def test_active_exemption_outranks_pending_verification():
    assert _practice("pending_verification", billing_exempt=True).entitled(at=NOW)


def test_active_exemption_outranks_invalid():
    assert _practice("invalid", billing_exempt=True).entitled(at=NOW)


def test_active_exemption_outranks_unknown_status():
    assert _practice("future-status", billing_exempt=True).entitled(at=NOW)


@pytest.mark.parametrize("status", ["invalid", "pending_verification", "future-status"])
def test_expired_exemption_does_not_outrank_bad_status(status):
    assert not _practice(
        status, billing_exempt=True, billing_exempt_until=NOW
    ).entitled(at=NOW)


def _before(practice):
    status = practice.subscription_status
    if status is not None and status not in KNOWN_SUBSCRIPTION_STATUSES:
        return False
    if status in {"pending_verification", "invalid"}:
        return False
    if practice.billing_exemption_active(at=NOW):
        return True
    if (
        practice.acquisition_source == "public_self_service"
        and status in ENTITLED_SUBSCRIPTION_STATUSES
        and not practice.has_authoritative_stripe_subscription_binding()
    ):
        return False
    trial_active = practice.trial_ends_at is not None and practice.trial_ends_at > NOW
    return status == "active" or (
        status in {None, "trialing"}
        and trial_active
        and (status is not None or practice.acquisition_source != "public_self_service")
    )


def test_reorder_never_reduces_entitlement():
    clocks = [None, NOW - timedelta(seconds=1), NOW, NOW + timedelta(seconds=1)]
    sources = [source.value for source in PracticeAcquisitionSource] + ["unknown"]
    for status, source, trial, exempt, expiry, bound, refused in product(
        [None, *KNOWN_SUBSCRIPTION_STATUSES, "unknown"],
        sources,
        clocks,
        [False, True],
        clocks,
        [False, True],
        [False, True],
    ):
        practice = _practice(
            status,
            acquisition_source=source,
            trial_ends_at=trial,
            billing_exempt=exempt,
            billing_exempt_until=expiry,
            stripe_customer_id="cus_synthetic" if bound else None,
            stripe_subscription_id="sub_synthetic" if bound else None,
            stripe_state_version=1 if bound else 0,
            late_checkout_refused_at=NOW if refused else None,
        )
        before = _before(practice)
        after = practice.entitled(at=NOW)
        assert not before or after, (
            status,
            source,
            trial,
            exempt,
            expiry,
            bound,
            refused,
        )
        if before != after:
            # Narrow allowed difference: durable late-Checkout refuse marker
            # preserves an unbound future local trial (master _before still
            # returns False for all invalid).
            if (
                status == "invalid"
                and refused
                and not bound
                and trial is not None
                and trial > NOW
                and source != "public_self_service"
                and not practice.billing_exemption_active(at=NOW)
            ):
                assert after is True
                continue
            assert practice.billing_exemption_active(at=NOW)
            assert status in {"invalid", "pending_verification", "unknown"}
