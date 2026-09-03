# Session Prep source-revocation replay hardening

## Outcome

Prevent a process-local Session Prep idempotency receipt from replaying a
previously prepared brief after its synthetic source context becomes revoked.
Revocation replaces that key's cached content with a content-free unavailable
tombstone; later exact retries replay only the tombstone.

## Risk and scope

Tier 2 by effect because this is a provider/PHI egress boundary. The patch is
limited to the default-off synthetic Session Prep service and its regression
test. It changes no Auth/IAM code, database schema, deployment workflow,
environment configuration, provider grant, or live service.

The request digest conflict check remains first. A caller cannot use revocation
to change a request under an existing idempotency key. Source revocation then
overrides replay of prior content, including after the canonical turn has
closed. New logical poll keys also receive a content-free unavailable result.

## Acceptance

- A ready result can replay while the source remains valid.
- After the same context becomes `source_revoked`, an exact retry never returns
  the ready brief, provenance, or source text.
- The first revoked response is a new content-free tombstone; another exact
  retry replays that tombstone.
- A new logical poll after revocation is also content-free.
- Existing static revocation, immutable artifact, stale-content, scope, and
  idempotency-conflict tests remain green.

## Recovery and activation

Revert the patch to recover the prior synthetic behavior. No data migration or
cleanup is required. Real-provider and PHI use remain prohibited until durable
artifact/idempotency and invalidation semantics, Auth/IAM, audit/retention,
source adapters, monitoring, incident response, and clinical/legal governance
are implemented, verified, and explicitly approved.

## Local verification (2026-09-03)

- Ruff format and lint: passed for the changed service and test.
- Pyright: 0 errors for the changed service and test.
- Session Prep service, receiver, and conversation kernel: 51 passed.
- PostgreSQL and Redis were local test instances only; all fixtures were
  synthetic. The local PostgreSQL server reported 16.15 versus the repository's
  16.9 pin, so exact-version CI remains authoritative.
