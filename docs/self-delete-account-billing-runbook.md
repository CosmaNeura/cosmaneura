# Self-delete: sole-owner Stripe follow-up runbook

Authenticated self-delete (`DELETE /v1/me/account`) never cancels a practice
Stripe subscription and never blocks deletion on billing state. When a sole
owner deletes themselves while a nonterminal subscription or open Checkout
exists, Core leaves billing untouched and records operator evidence instead.

This is intentional: auto-cancel remains a founder decision, and canceling
from the delete path would expand the billing surface beyond the self-delete
feature latch (`ACCOUNT_SELF_DELETE_ENABLED`, production default OFF).

## When this runbook applies

Look for append-only `PracticeAccessAudit` rows on the practice:

| Action | Meaning |
| --- | --- |
| `doctor_self_deleted` with `stripe_follow_up=1` in `document_reference` | Seat-sync / live subscription follow-up queued at delete time |
| `stripe_checkout_follow_up_required` | Sole owner left while Checkout could still complete, **or** a Checkout completed after `owner_reassignment_needed_at` was set |
| `stripe_refund_needed` | A late Checkout completed after owner self-delete; Stripe may have charged while Core refused to bind. Operator alert `LateCheckoutRefundNeeded` also fires once per subscription. |

Also check practice fields (PHI-free):

- `owner_reassignment_needed_at` is set
- `late_checkout_refused_at` when a late Checkout was refused (durable trial marker)
- `stripe_quantity_sync_required` / `stripe_quantity_sync_target` when seat sync was queued
- `stripe_subscription_id`, `subscription_status`, `stripe_checkout_session_id`

## Refund owner

**Refund owner: `OPEN-JOHN`**

Late-Checkout refunds are founder-owned. Do not cancel or refund in production
Stripe without John's yes. Staging/test-mode Stripe refunds still need an
explicit operator decision recorded on the change ticket.

## Detection (read-only, before flipping the production latch)

Count practices left ownerless (must be zero before production latch flip):

```bash
uv run python -m app.cli count-owner-reassignment-needed
```

Or the thin wrapper (same query, still synthetic/staging DB only unless John
runs it himself):

```bash
uv run python scripts/count_owner_reassignment_needed.py
```

Agents never run this against production or any real patient database.

## Operator steps (staging / test Stripe keys only unless John says otherwise)

1. Confirm the practice is ownerless (`owner_reassignment_needed_at` set) and
   that the audit rows match the ticket.
2. For a late Checkout that attempted to bind after delete: Core marks the
   subscription contract `invalid`, sets `late_checkout_refused_at`, does
   **not** silently attach an ownerless entitlement, writes
   `stripe_refund_needed` / `stripe_checkout_follow_up_required`, and pages via
   `LateCheckoutRefundNeeded`. Prefer the subscription id from the audit row's
   `document_reference` (`stripe-subscription:<id>`) — refused late Checkout
   does **not** bind `practice.stripe_subscription_id`.
3. **Cancel or refund in Stripe before transferring ownership.** Refund owner
   `OPEN-JOHN` cancels or refunds the Stripe subscription/customer as
   appropriate (test-mode keys in staging/CI; production Stripe mutation needs
   John's yes). Clear checkout session fields only through the normal billing
   recovery paths. Refusal audits stay sticky: a later event for the same
   subscription will not bind even after `owner_reassignment_needed_at` clears.
4. Decide ownership transfer vs practice wind-down only after Stripe cancel /
   refund is settled. Self-delete does not terminate the covered entity.
   Successful `transfer-practice-owner` clears `owner_reassignment_needed_at`
   but does **not** clear refuse audits or `late_checkout_refused_at`.
5. Record the Stripe decision on the same change ticket (`operator_ref`).

## What self-delete does not do

- Does not call Stripe cancel/update APIs
- Does not change entitlement for practices whose owners never self-delete
- Does nothing while `ACCOUNT_SELF_DELETE_ENABLED` is OFF (production default)
- Does not revoke an unbound practice's still-open local trial or an active
  pilot grant for remaining clinicians when late Checkout is refused
