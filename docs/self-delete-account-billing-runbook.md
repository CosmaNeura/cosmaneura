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

Also check practice fields (PHI-free):

- `owner_reassignment_needed_at` is set
- `stripe_quantity_sync_required` / `stripe_quantity_sync_target` when seat sync was queued
- `stripe_subscription_id`, `subscription_status`, `stripe_checkout_session_id`

## Operator steps (staging / test Stripe keys only unless John says otherwise)

1. Confirm the practice is ownerless (`owner_reassignment_needed_at` set) and
   that the audit rows match the ticket.
2. Decide ownership transfer vs practice wind-down. Self-delete does not
   terminate the covered entity.
3. For a live subscription that should stop: cancel in the Stripe Dashboard
   (or approved CLI) with the practice's `stripe_subscription_id`. Prefer
   test-mode keys in staging/CI. Production Stripe mutation needs John's yes.
4. For a late Checkout that bound or attempted to bind after delete: Core
   marks the subscription contract invalid and does **not** silently attach
   an ownerless entitlement. Cancel or refund the Stripe subscription/customer
   as appropriate, then clear checkout session fields only through the normal
   billing recovery paths.
5. Record the Stripe decision on the same change ticket (`operator_ref`).

## What self-delete does not do

- Does not call Stripe cancel/update APIs
- Does not change entitlement for practices whose owners never self-delete
- Does nothing while `ACCOUNT_SELF_DELETE_ENABLED` is OFF (production default)
