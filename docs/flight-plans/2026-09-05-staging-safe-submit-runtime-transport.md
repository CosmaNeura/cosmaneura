# Flight plan: dormant safe-submit runtime transport

## Outcome and boundaries

- Tier **2**: harden the future restore mutation boundary so ARM and marker
  Blob calls use only the runtime policy's selected Container Apps job UAMI.
- In scope: `staging_restore_safe_submit.py`, its focused tests, and this plan.
  The runtime builder cross-binds the safe-submit and runtime policies, rejects
  ambient credentials/proxies, and pins the immutable-image CA and exact URLs.
- Non-goals: `_runtime_dependencies()` wiring, workflow or Terraform changes,
  activation, Azure/GitHub mutation, restore, sanitization, cutover, or
  production/PHI use.

## Current state and dependency order

- Base is exact protected `master` `f3c5ac9486316a5304c99aac511f55bd2277d99d`.
  Provision remains a credentialless tombstone and every activation flag stays
  false.
- Order: dormant evidence/runtime contracts → this transport hardening → later
  private runtime wiring and attestation → separately approved non-PHI rehearsal.

## Security, rollout, and recovery

- The builder requires an executable validated runtime policy, selects only
  `identities.jobs[provision|cleanup]`, and requires the safe-submit creator
  identity and private marker endpoint to match that policy before environment
  or network access.
- Tokens come only from the exact local/link-local Container Apps identity
  endpoint with the selected client ID and exact ARM or Storage audience.
  Responses bind optional echoes and bounded expiry; no Azure CLI/default
  credential path exists.
- HTTP uses no proxies or redirects, the fixed runtime CA, identity encoding,
  exact final URLs, canonical framing, bounded bodies, and one whole-exchange
  deadline. Caller headers cannot replace authentication or content headers;
  policy-bound marker listing and cleanup writes outside the tombstone are refused.
- Once a PUT/PATCH/DELETE is dispatched, any ambiguous response becomes an
  unknown outcome. No transport retry is added. Marker PUT reconciliation,
  single restore submission, and dominant cleanup tombstones remain unchanged.
- Abort on any policy/identity/endpoint mismatch, redirect, deadline failure,
  ambiguous framing, second mutation attempt, activation change, or workflow /
  infrastructure diff. Recovery is source revert while the dormant gates stay
  false and durable tombstones remain preserved.

## Completion evidence

- Focused safe-submit tests cover both job identities and audiences, exact
  requests, bad endpoints/tokens/framing/final URLs, pre-dispatch refusal,
  post-dispatch uncertainty, one marker PUT with readback, and tombstone races.
- Ruff, Pyright, diff/budget checks, and independent exact-head review must pass
  before merge. Live rehearsal and all promotions remain separately authorized.
