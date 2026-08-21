# Provider-only V7 staging activation

- Date: 2026-08-21
- Status: accepted for a supervised, synthetic provider pilot

## Scope

This change enabled the platform-owned V7 authority, canonical-conversation,
model-usage, and observational-audit path for the provider Sovereign mount in
staging. Acceptance used synthetic prompts only.

It did not authorize production or real-PHI use, enable V7 for the public
mount, enable labs, patient search, journey digest, or Magisterium MCP, or
establish V8 external immutable retention. This is engineering evidence for a
bounded pilot, not HIPAA certification.

## Accepted deployment

| Component                   | Accepted value                                                            |
| --------------------------- | ------------------------------------------------------------------------- |
| Platform source             | `7cc8051da6540e2b008df266c564548ce51ac061`                                |
| Platform image digest       | `sha256:8e317dbbf0c133fbe4ecc03840e15e5c10a926a1d4e5f170d6e7b5aa658d2ed4` |
| Platform revision           | `cosmaneura-staging-app--2026-08-20-v1-7cc8051`                           |
| Provider Sovereign source   | `91410aa920ad5cde00b2feec3efe5e4ccceec869`                                |
| Provider Sovereign image    | `sha256:e37370a7746a1618927a88987c77c192f0891f5e4b87d24a349a234eaaeb649d` |
| Provider Sovereign revision | `cosmaneura-sovereign-staging-app--gpt5-e37370a7-p`                       |
| Public Sovereign image      | `sha256:198536a55310b1af9a0b4480f2ebc181adbeac7ca11963335a40ad10e4f5e8f2` |
| Public Sovereign revision   | `cosmaneura-web-staging-app--v7pre-198536a5-u`                            |

The platform workload allowlist contains the provider workload only. The
public mount remains on its separate image with Platform Agent API access off.
Production was not changed.

These values record the initial accepted activation. Later reviewed
staging-only Platform deployments may advance its source, image, and revision;
the operator runbook requires exact deployed-commit provenance and a fresh
full smoke rather than treating this historical table as a permanent pin.

## Acceptance result

The final synthetic acceptance established that:

- a provider session could exchange for an opaque mount token;
- a non-provider session and the public workload were denied;
- one canonical turn completed with one user phase and one final phase;
- audit ingestion completed and two model-usage facts were recorded;
- same-key replay preserved idempotency; and
- temporary acceptance sessions and registry build roles were removed.

The audited staging identity rebind completed with one active replacement and
one revoked predecessor. Only the protected receipt SHA-256 is recorded:
`77bb20f6f9de6c26b2e6e2185f816f06614e84469e1a56b00252294dc0f6b7e7`.

## Preserved failed-turn baseline

An earlier synthetic turn reached the platform but failed because the prior
provider image sent an unsupported `temperature` field to `gpt-5.6-sol`. The
turn was closed as unavailable; audit and usage ingestion are both explicitly
`failed`, with nothing pending. Its protected reference SHA-256 is:

`94527b2939fa327ee1a355c94749ef507e47db88445f3f8cabefc2befb44a340`

The standing baseline is exactly:

```json
{
  "failed_audit_count": 1,
  "failed_usage_count": 1,
  "matching_interaction_count": 1,
  "pending_audit_count": 0,
  "pending_usage_count": 0
}
```

The sole row must have both states `failed` and its protected reference must
hash to the value above. Do not delete it, relabel it, or expose its raw
reference. Any other result is drift.

## Recovery fact

The provider app uses `Single` revision mode, so the previous revision is not a
standby. Its old image digest is the public digest shown above and has the known
reasoning-model incompatibility: health can pass while chat fails. It is not a
rollback target and must never receive provider traffic. Recovery is isolation
followed by deployment of the accepted digest for simple drift, or a newly
reviewed immutable forward fix.

Operator checks and recovery are in
[`../V7_PROVIDER_STAGING_PILOT.md`](../V7_PROVIDER_STAGING_PILOT.md).

No raw identity, token, prompt, response, PHI, secret, environment dump, or
unfiltered log is retained in this record.
