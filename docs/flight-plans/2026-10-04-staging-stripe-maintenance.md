# Staging Stripe maintenance preparation

Tier 2: reviewed staging billing configuration, with no schema change.

## Scope

Prepare test-mode subscription maintenance and webhooks using the existing
staging 1Password item. Keep new Checkout disabled and live mode explicitly
false. Production, Scribe, Session Prep, access policies and deployment controls
are unchanged. No billing exemption or fake subscription is created.

This draft is not deploy-ready. The owner requested preparation for comprehensive
synthetic staging testing, not a deployment or an expanded clinical cohort.

## Release prerequisites

1. Include the reviewed portal compatibility fix from PR #672 in the selected
   merged candidate; this configuration alone does not repair that validator.
2. Freshly verify the selected staging item's test key, account, price, portal
   configuration and webhook endpoint. The earlier read-only qualification
   established presence/configuration, not signing-secret correctness or delivery.
3. Observe live web, worker, scheduler, Scribe and provider runtime source,
   images, effective configuration and schema. Retain per-workload recovery.
   Choose keep, assimilate or replace for each drift item. The default deployer
   forces Scribe and synthetic Prep off; do not apply it blindly over a working
   combined release. Resolve this through the approved release path before rollout.
4. Reconcile the canonical workflow instructions with the separate combined
   Scribe/Prep release plan's local-founder path and Actions-off requirements.
   An active workflow listing is not authorization to run it. Do not re-enable
   Actions or substitute prose for source/identity/synthetic evidence.
5. The authorized operator performs the reviewed rollout. Verify real test-mode
   webhook delivery, subscription projection and portal access with synthetic
   accounts; a populated signing-secret field is insufficient.

## Acquisition remains separate

Record the later of acquisition disablement and the last old revision stopping.
Inventory current and historical Checkout prices, wait the required 24 hours
and 5 minutes, and run `scripts/stripe_checkout_activation_preflight.py` in test
mode without `--live-mode`. Resolve every ambiguity; require zero eligible open
sessions and `ready_for_acquisition=true`. Only then prepare and review a separate
change enabling `STRIPE_BILLING_ENABLED`. Do not shorten the drain or assume an
empty current price inventory proves readiness.

## Verification and recovery

Execute the actual staging billing shell section using a secretless recorder;
test ambient true/false/empty flag overrides, fixed item selection, all five
vault references and clearing inherited credentials. Run surrounding deployment
contracts and scoped lint/type checks. Obtain independent review of the final
pushed SHA; it is not GitHub approval.

Before deployment, recovery is simply not selecting this draft. After a rollout,
disable acquisition first and retain maintenance while diagnosing; use verified
per-workload image/configuration recovery if needed. Turning off maintenance is
not a refund or a rollback of issued Checkout URLs, subscriptions or stored data.
Never delete billing records or reset clinical records as recovery.
