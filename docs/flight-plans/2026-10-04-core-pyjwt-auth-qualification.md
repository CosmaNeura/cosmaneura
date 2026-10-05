# Core PyJWT authentication qualification

Tier 2. The user explicitly authorized local Core authentication-dependency
qualification on October 4, 2026 (October 5 UTC), without identity configuration
changes or deployment. This PR is stacked on Core tooling PR 678 at
`8751574547eeb244766710a2a3d9ddf73facb379`; merge that prerequisite first and
retest the actual integrated candidate.

## Scope

Raise the existing PyJWT security floor to 2.15 and lock 2.15.1. Change only its
dependency declaration/lock, focused tests and this flight plan. Production
authentication code, RS256 allowlist, issuer/audience/caller/role policies,
required-claim differences between Scribe and Agent APIs, identity configuration,
routes and clinical/billing behavior remain unchanged. No migration, new feature
flag, patient data, provider network call or release cohort applies.

The [upstream changelog](https://raw.githubusercontent.com/jpadilla/pyjwt/2.15.1/CHANGELOG.rst)
documents normalized malformed-claim/recursion/network errors and parsed JWKS
caching in 2.15, plus signature-padding compatibility in 2.15.1. Tests use real
RSA signatures, the real PyJWKClient and Core's authenticator through a synthetic
HTTP endpoint. Only JWKS HTTP I/O is replaced. Clerk SDK verification uses a
disposable public key and Core's existing verified-session normalizer; no live
Clerk identity or authentication setting is changed.

## Verification and availability

- Valid signed service requests succeed; missing/invalid lifetime, signature,
  algorithm, issuer/audience/tenant, caller, role and app-only claims fail closed.
- Deep payloads, malformed JWKS members and truncated responses cannot leak raw
  exceptions through the tested boundary. Base64URL signature padding remains
  accepted while non-alphabet junk is rejected.
- Existing Core behavior maps JWKS lookup errors to sanitized 401, not Scribe's
  503. Keep this behavior explicit; the dependency update does not redesign it.
- Unknown signing keys are refresh-throttled; a new key can receive 401 until
  the existing 30-second cooldown elapses. Tests advance the client clock marker
  without sleeping, recover the new key, and confirm the known key still works.
  Existing per-key LRU caching has no time-based revocation guarantee.
- Clerk verification accepts a signed synthetic session and rejects expired,
  wrong-party, wrong-signature and malformed-expiration tokens. Core normalization
  checks the expected issuer and keeps the raw session identifier out of its hash.
  This is not verification of deployed Clerk settings or a real browser login.
- Focused tests, full isolated authority suite, scoped lint/types/format, lock
  consistency, current installed-environment vulnerability scan and an old-version
  negative control are required. Preserve external-fixture skips and baseline-red
  types explicitly; do not represent this as full app qualification.

Evidence: `/private/tmp/cosmaneura-core-pyjwt-focused.xml`,
`/private/tmp/cosmaneura-core-pyjwt-authority-final.xml`,
`/private/tmp/cosmaneura-core-pyjwt-negative-control.xml`, and
`/private/tmp/cosmaneura-core-pyjwt-audit.json`. The old 2.14 control still accepts
valid requests and sanitizes deep payloads, but fails the malformed-JWKS,
truncated-response and Clerk malformed-expiration regressions. No live keys,
tokens, credentials or patient information are retained.

## Release and recovery gates

Obtain independent non-implementer review of the final pushed SHA. Keep Actions
disabled and preserve approval/protection controls. This PR does not authorize
merge, rollout or changes to the release path. John must select merged, reviewed
source via the approved founder process, reconcile deployed drift, and retain
the predecessor source/image digest, configuration and schema evidence.

Before promotion, verify genuine synthetic staging requests for both Clerk and
Entra identities: valid login/service calls succeed and wrong issuer, tenant,
audience, caller, role, expiry and signatures are refused. Verify Scribe handoff
and session prep with the intended real identities. Record metadata-only evidence,
not bearer tokens. Native/browser/payment acceptance remains a separate gate.

Recovery: restore the predecessor image/lockfile with unchanged configuration;
there is no database rollback. That reintroduces the known PyJWT finding, so a
forward fix is preferred; restoring 2.14 requires explicit security-risk
acceptance. Never relax signature, claim, caller or role checks for recovery.
