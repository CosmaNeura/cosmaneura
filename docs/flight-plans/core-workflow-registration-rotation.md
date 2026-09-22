# Flight plan: rotate the Core deployment workflow registration

## Outcome and boundaries

- Problem and measurable outcome: historical runs under workflow ID
  `132737823` can be rerun with their original, unsafe push-deploy snapshot.
  Create a distinct active workflow ID with the current manual founder gates,
  then disable the legacy ID while retaining its source path and history.
- In scope: workflow files, safety tests, live documentation, registration on
  `master` and availability on `staging`, and disabling ID `132737823`.
- Explicit non-goals: dispatching a workflow, deploying either environment,
  changing credentials, Azure resources, collaborators, environment settings,
  branch rules, or the existing GitHub approval process.
- Affected surfaces: the Core GitHub Actions control plane. No application,
  schema, patient, or PHI-bearing data changes.

## Risk and dependencies

- Tier: Tier 2 because this changes production and staging deployment control.
- High-blast-radius decision: historical workflow snapshots remain capable of
  using their historical logic until their workflow ID is disabled.
- Dependency order: merge the replacement to `master`; verify a new active ID;
  verify no queued or running legacy job; disable ID `132737823`; then merge
  the same reviewed workflow content to `staging` and verify parity.
- Hold safety: no deployment is authorized during this sequence. Between the
  disable and the staging sync, staging promotion is intentionally unavailable.
  Do not restore availability by re-enabling the legacy ID.
- Cross-repo ordering: N/A; no contract changes.

## Current deployed state

- Source baselines at review start: `master`
  `cf8b3e126ef37902c28c2b6b3fa868fc27ea19e1`; `staging`
  `4d4ad711e7d04c22465754199020112fa3e20fb2`.
- Both refs contain the safe current workflow blob, but ID `132737823` also
  retains older rerunnable snapshots. Forty unsafe staging-push snapshots
  remain inside GitHub's rerun window; the newest expires around
  2026-09-24 02:27 UTC.
- Reconciliation decision: replace the workflow registration, retain the old
  path as an inert audited tombstone, and disable the legacy ID.
- Previous known-good artifact and data-recovery boundary: N/A; no artifact is
  built or promoted and no environment or data is mutated.

## Conditional controls

- Feature flag: N/A; this is a deployment-control change.
- Schema/data sequence and PHI-safe rehearsal: N/A; no schema or data change.

## Rollout and promotion

1. Parse the changed workflows, run the focused deployment-policy suite, and
   obtain independent agent review of the final substantive commit.
2. Merge through the current PR process to `master`. Do not dispatch either
   deployment job.
3. Verify the new path is registered under a distinct active workflow ID and
   that the merge-triggered production and staging deploy jobs are skipped.
4. Verify ID `132737823` has no queued or running run, disable it, and confirm
   its state is `disabled_manually`.
5. Merge the same reviewed workflow content to `staging`; verify its blob
   matches `master`, its push-triggered deploy jobs are skipped, and no new
   deployment record exists.
- Abort threshold: any missing founder/manual/current-ref gate, any unexpected
  credential-bearing job, any non-skipped deploy job, an active legacy run, or
  a new deployment record stops the sequence.
- Founder production record: N/A; this change authorizes no deployment.

## Security and recovery

- Both workflow paths remain in production configuration-drift and attestation
  hashes, so this rotation deliberately invalidates earlier Terraform apply
  evidence.
- Recovery: repair the new path through another reviewed PR or rotate to
  another fresh workflow ID. Never re-enable ID `132737823` while unsafe
  historical runs remain rerunnable.
- Observation window: verify both branch trees, workflow registry states, run
  job conclusions, deployment records, and the unchanged master ruleset after
  both merges.

## Completion

- The new workflow has a distinct active ID and identical content on
  `master` and `staging`.
- ID `132737823` is `disabled_manually`; its file and history remain.
- All validation jobs pass, all merge-triggered deploy jobs are skipped, and
  GitHub records no deployment from this rotation.
- Follow-up: revisit the shared production-only dispatch inputs separately;
  retain the archived SSDLC HTML as historical evidence rather than silently
  rewriting it.
