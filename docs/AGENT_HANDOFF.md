# Agent assessment handoff

Cosmaneura's Foundry operations agents are reviewers, not implementation or
deployment authorities. Their useful output is a versioned handoff event tied
to an immutable Git commit. GitHub is the operating interface between those
reviewers and the Codex agents that perform the work.

## What is authoritative

- The JSON artifact produced by the trusted assessment workflow is the
  machine-readable record.
- Its repository, pull request, workflow run, base SHA, head SHA, policy SHA,
  Foundry version, and input digest must all match the work being performed.
- Its canonical artifact digest must recompute exactly, and its broker-owned
  evidence deadline must still be in the future.
- A GitHub Check or comment is only an index and human summary. It is never the
  authoritative artifact and is never a production approval.
- Any new commit makes a prior assessment stale. Re-run the assessment for the
  new head SHA before relying on it.

The vendored contracts are:

- `.github/schemas/cosmaneura-agent-handoff-v1.schema.json`
- `.github/schemas/cosmaneura-agent-resolution-v1.schema.json`

## Worker-agent flow

1. Fetch the current assessment for the exact PR head SHA through the
   provenance-verifying GitHub fetcher.
2. Validate it locally before reading or claiming actions.
3. Work only on the listed action IDs and within their stated scope.
4. Emit a resolution receipt against the resulting commit SHA.
5. Run deterministic tests and scanners.
6. Request an independent reassessment of the resulting SHA.

Acknowledgment is not approval. Completion is not verification. A Foundry
assessment is not founder sign-off and cannot satisfy a production gate.

## Current GitHub path

After this change is on `master`, run **Foundry PR Assessment** from `master`
with a pull-request number and one of `deployment_assessment`,
`migration_assessment`, or `security_assessment`. The workflow:

1. verifies that its own code is current `master`;
2. requires an open PR targeting `master`, reads its immutable base-to-head
   comparison and check-run state through the GitHub API, and never checks out or
   executes the PR head;
3. parses patches locally, rejects sensitive or malformed inputs, then discards
   every source line, literal, comment, and hunk label before transport; Foundry
   receives only paths, change counts, numeric hunk structure, and content
   digests;
4. invokes the exact version in `ops/foundry-agents/deployment.lock.json` using a
   dedicated OIDC identity with only `Foundry Project Runtime User`;
5. stamps and validates the draft with trusted workflow context;
6. rechecks the PR head, uploads the canonical JSON artifact, and creates a
   neutral GitHub Check that links back to the workflow run.

The Check remains neutral because an advisory model is not a merge or deployment
gate. Humans and worker agents make decisions from the validated artifact and
the repository's deterministic policy.

The broker independently rejects a green model decision when trusted facts do
not support it: required check provenance is absent/non-green, a PR is closed or
draft, a migration lacks both semantic source and a passed rehearsal, or a
security assessment lacks semantic source or verified scanner coverage. Check
names alone are never trusted. Model citations must name an item in the broker's
evidence manifest.

A worker fetches by exact SHA and kind. The fetcher independently verifies the
current trusted `master` workflow commit, successful workflow provenance,
artifact identity and archive digest, safe ZIP contents, canonical handoff
digest, run/policy/SHA bindings, the current open PR base and head, and the
evidence deadline before writing a mode-0600 local file:

```console
python3 .github/scripts/fetch_agent_handoff.py \
  --sha FULL_HEAD_SHA --kind security_assessment \
  --output /tmp/security-assessment.json
python3 .github/scripts/handoffctl.py summarize \
  /tmp/security-assessment.json --contract assessment
```

If more than one trusted run produced the exact artifact name, pass the run ID
shown by the neutral Check with `--run-id RUN_ID`. Ambiguity, expiry, an obsolete
producer-policy commit, or any mismatch fails closed and requires a new
assessment.

`Release Medic` is deployed and can be invoked through the same pinned CLI and
broker path. Automated release handoff remains intentionally off until the
current deployment scripts emit a sanitized exact-image digest, Container Apps
revision, migration result, and synthetic-health artifact; workflow success
alone is not enough for a `HEALTHY` conclusion.

## Current-state boundaries

The agents may reason over explicitly approved sanitized source, CI summaries,
sanitized Terraform plan summaries, image/revision identifiers, migration-job
status, and synthetic health evidence. The current automatic PR workflow sends
no source-line text. It supports path/risk/check/scanner-gap review; semantic
code-vulnerability conclusions require a separately approved privacy-safe source
or trusted-scanner evidence path. They must not receive:

- secrets, tokens, `.env` contents, or Key Vault values;
- PHI, patient or provider records, transcripts, notes, request bodies, or raw
  application telemetry;
- unrestricted shell, GitHub, Azure, database, or network access.

When required evidence is missing, stale, contradictory, or outside this
boundary, the result is `INCOMPLETE` or `HOLD`, never `READY`.

## Assessment kinds

- `deployment_assessment` — deploy readiness and exact next actions.
- `migration_assessment` — expand/contract and compatibility review.
- `release_assessment` — deployment status and recovery guidance.
- `security_assessment` — code, dependency, CI/IaC, authorization, secret, and
  PHI-exposure findings.

Security review can supplement deterministic scanners but cannot replace
CodeQL/SAST, dependency, secret, IaC, SBOM, and container-image scanning.
The evidence builder records those six coverage classes explicitly, including
`not_configured` when no corresponding exact-SHA check exists.

## Retiring the current-state agents

These agents are deliberately disposable. Replace or retire them when the
platform has same-digest promotion, a production-shaped rehearsal system,
feature/cohort controls, multi-revision rollout, and PHI-safe telemetry. Before
retirement, disable their endpoints, revoke their identities, preserve the
handoff audit artifacts for the required retention period, and remove their
workflow triggers.
