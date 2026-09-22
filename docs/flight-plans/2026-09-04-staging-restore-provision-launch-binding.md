# Flight plan: bind provision launch authority into the dormant restore executor

Status: **implemented at the private seam; deliberately non-executable**.

## Outcome and boundaries

- Problem and measurable outcome: make the trusted executor validate one
  authenticated provision launch, its referenced external runtime record, and
  their cross-binding before it reads restore evidence or derives a request.
- In scope: the launch-record/receipt projections, executor dependency seam,
  same-read evidence digest check, focused tests, policy, and runbooks.
- Explicit non-goals: mailbox, signature-verifier, secret/blob, Azure, broker,
  provider, safe-submit, or workflow adapters; Container Apps start/poll
  behavior; infrastructure; activation; and any live restore.
- No user, service, environment, secret, schema, or data is changed by this PR.

## Risk and dependencies

- **Tier 2:** this is a future restore-authorization boundary. A false binding
  could let unrelated evidence, identity, or lifetime claims reach safe-submit.
- The receiver order is authenticated signed receipt -> receipt-derived
  provision and cleanup runtime references -> exact launch record -> verified
  external runtime record -> received-receipt cross-validation -> restore
  inputs. Every failure is bounded and occurs before the next authority stage.
- The opaque tokens are same-process provenance markers, not a sandbox against
  hostile code in that process. Only later reviewed adapters may mint them.
- The existing dormant evidence/authentication/composition/safe-submit stack is
  retained. This slice can be held indefinitely because all activation and
  adapter flags remain false and the public entry point still refuses first.

## Current state and release control

- Base protected `master` is `56101ddd5a91ccb5b382926f36b738cc0f1fec63`.
- The protected provision workflow remains its credentialless failing
  tombstone; cleanup and sweep behavior are unchanged.
- Feature release controls are N/A: there is no user-visible behavior. Enabling
  any assertion requires a separate Tier-2 review and live evidence.
- Schema/data sequencing is N/A: this change performs no migration or restore.

## Binding, rollout, and recovery

- The validated provision record is the authority for TTL, exact six-file
  evidence digests, decoded HMAC-key digest, attestation-bundle digest,
  independent trusted-root digest, rehearsal ID, workflow SHA, run ID, and run
  attempt. Callers can supply paths only; they cannot override those values.
- Singleton files are captured once and hashed before manifest validation. The
  manifest validator compares the launch digests to the bytes from its one
  bounded directory read, then continues its existing full validation. Only a
  successful full validation mints the opaque proof carrying that exact digest
  tuple; the executor compares the proof to the launch record before continuing.
- One shared whole-second non-regressing clock covers receipt freshness,
  evidence freshness, proof composition, and submit. The received-receipt proof
  binds its exact validation instant, and launch expiry is rechecked immediately
  before the sole submit call.
- Container Apps `replicaRetryLimit=0`, job timeout, POST response handling, and
  receipt polling are unchanged. With no transport adapter, no raw 202 response
  can produce a receipt or enter this executor.
- Rollout hold points: merge dormant -> separately implement and review exact
  read/signature adapters -> prove private runtime and provider fidelity ->
  separately wire the workflow -> separately authorize one non-PHI rehearsal.
- Abort on any unsealed type, action other than provision, hash/identity/time
  mismatch, missing/extra evidence file, regressing clock, or pre-submit side
  effect. Recovery is source reversion while all activation stays false; no
  deployed state or data rollback is needed.

## Completion

- Require focused launch, executor, evidence-manifest, and runtime-binding
  tests; Ruff formatting/lint; Pyright; policy validators; diff/budget checks;
  and independent review of the final commit.
- Remaining owners: infrastructure/runtime work must implement and attest the
  adapters and private network. Workflow and founder authorization remain
  separate changes; production/PHI use remains prohibited.
