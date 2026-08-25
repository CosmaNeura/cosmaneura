# Current-state Foundry prompt agents

This directory contains source-controlled definitions for four advisory Azure AI
Foundry prompt agents. The synthetic-tested versions in `deployment.lock.json`
are deployed to the existing `CosmasCode/proj-default` project. Merely checking
out or rendering these assets never changes Azure; `foundryctl.py` is the
explicit, guarded publishing process for the existing `gpt-5.6-sol` deployment.

## Agents

| Definition | Purpose | Allowed decisions |
| --- | --- | --- |
| `agents/deploy-navigator.agent.yaml` | Decide whether an exact PR/SHA has enough evidence to deploy | `READY`, `HOLD`, `NEEDS_HUMAN`, `INCOMPLETE` |
| `agents/migration-sentinel.agent.yaml` | Review schema and data transitions for deploy safety | `READY`, `HOLD`, `NEEDS_HUMAN`, `INCOMPLETE` |
| `agents/release-medic.agent.yaml` | Classify a completed or failed release and prescribe the safest next step | `HEALTHY`, `DEGRADED`, `FAILED`, `INCOMPLETE` |
| `agents/security-vulnerability-sentinel.agent.yaml` | Identify security and code vulnerabilities and hand bounded fixes to a worker | `HOLD`, `NEEDS_HUMAN`, `PASS_WITH_FINDINGS`, `NO_FINDINGS_OBSERVED`, `INCOMPLETE` |

Each definition composes `prompts/common.md` before its agent-specific prompt.
Paths in a definition are relative to that definition file. The model response
is a strict assessment draft that validates against the agent contract in
`contracts/`.

Before submitting a response schema to Foundry, the publisher must resolve and
bundle its local `$ref` entries; Foundry must receive the resulting closed schema,
not filesystem paths.

The contracts narrow, but do not copy, the canonical GitHub handoff schema:

- `.github/schemas/cosmaneura-agent-handoff-v1.schema.json`
- `.github/schemas/cosmaneura-agent-resolution-v1.schema.json`

The trusted handoff adapter combines that draft with the request's verified
subject, configured producer identity, policy SHA, generated handoff ID, and
computed integrity fields. It rejects model-supplied values for those trusted
fields, removes the draft-only version marker, and validates the assembled
artifact against the canonical handoff schema. The model never creates or
guesses an artifact digest.

The resolution schema is consumed by implementation agents after they act on an
assessment. A resolution receipt is never approval: the reviewing prompt agent
must evaluate the new immutable head SHA and issue a new assessment.

## Foundry control CLI

The CLI uses only Python's standard library and the authenticated Azure CLI. It
is locked to the approved project endpoint, GA `api-version=v1`, the
`https://ai.azure.com/.default` scope, the existing model deployment, and the
four names in this directory. It implements no delete operation and rejects all
HTTP mutations except agent-version creation and exact-version route updates.
Responses invocations always send `store: false`.

The publisher performs a privileged GET and verifies the remote instructions,
complete local schema hash, Foundry schema-projection hash, model, and immutable
version before routing. The GitHub runtime identity deliberately has only the
Responses data action, so runtime invocation cannot repeat that management GET;
it pins the immutable numeric version recorded in `deployment.lock.json`.

Inspect and render locally without contacting Azure:

```bash
python3 ops/foundry-agents/foundryctl.py catalog
python3 ops/foundry-agents/foundryctl.py render \
  --agent deploy-navigator-v1
python3 ops/foundry-agents/foundryctl.py render --all \
  --output-dir /private/tmp/cosmaneura-foundry-agent-bundles
```

Inspect remote status with GET-only operations:

```bash
python3 ops/foundry-agents/foundryctl.py list
python3 ops/foundry-agents/foundryctl.py get deploy-navigator-v1
python3 ops/foundry-agents/foundryctl.py get deploy-navigator-v1 --version 1
```

Create an initial version only after reviewing the rendered payload. Creation
fails before POST if the name already exists. `create-all` preflights all four
names before creating any of them.

```bash
python3 ops/foundry-agents/foundryctl.py create deploy-navigator-v1 \
  --confirm deploy-navigator-v1
python3 ops/foundry-agents/foundryctl.py create-all --confirm CREATE-4
```

Creating a later version is intentionally separate and race-guarded:

```bash
python3 ops/foundry-agents/foundryctl.py version deploy-navigator-v1 \
  --expected-latest 1 --confirm deploy-navigator-v1:1
```

Run a pinned, synthetic smoke case and atomically write the validated model
draft for the trusted broker. The input file must be a sanitized JSON object
with no PHI, secrets, credentials, or raw logs.

```bash
python3 ops/foundry-agents/foundryctl.py invoke deploy-navigator-v1 \
  --version 1 --input /path/to/synthetic-evidence.json \
  --confirm-sanitized --draft-output /path/to/validated-draft.json
```

Only after that smoke passes, explicitly route 100 percent to that exact version
with Entra authorization and the Responses protocol:

```bash
python3 ops/foundry-agents/foundryctl.py route deploy-navigator-v1 \
  --version 1 --confirm deploy-navigator-v1:1
```

Then repeat the invocation without `--version` to prove the published selector
resolves to the intended version. The CLI checks the returned resolved version,
parses `output_text`, and validates it against the complete local draft contract.

The schema sent to Foundry is a shape-preserving compatibility projection. It
uses required nullable properties and removes constraint keywords that Foundry's
Structured Outputs implementation rejects. The complete local schema remains
authoritative for post-response validation. Both schema digests and the exact
instruction digest are stamped into agent-version metadata and verified before
routing.

## Current-state boundary

The agents can reason over explicitly approved sanitized repository evidence, CI
results, deployment metadata, migration evidence, and synthetic health results
supplied to them. The automatic PR path emits only content-free diff structure,
not source lines.
They cannot establish real-user health, production-shaped migration behavior,
cohort safety, or absence of vulnerabilities when that evidence is unavailable.
Missing, stale, or contradictory evidence produces `INCOMPLETE`; it never
silently becomes a pass.

Known current-state constraints are deliberately reflected in the prompts:

- the core platform deploys production from `master` and staging from `staging`
  through GitHub Actions to Azure Container Apps;
- migrations run from GitHub Actions runners;
- branch protection and a required Production reviewer are not yet enforced;
- Key Vault and Application Insights are target-state capabilities rather than
  reliable evidence sources for core production;
- an agent sees only the sanitized evidence envelope provided for its run.

## Evaluation fixtures

`evals/manifest.yaml` declares synthetic, non-PHI cases. The fixtures contain no
credentials, production logs, patient records, or live endpoints. Safety-critical
assertions are gates: schema validity, immutable-SHA binding, prompt-injection
resistance, evidence citation, correct non-green handling, and absence of
prohibited actions must all pass before an agent version is published.

These assets intentionally contain no publishing credentials, Azure resource
keys, or model secrets. The GitHub runtime identity is credentialless and has
only `Foundry Project Runtime User` on this project. It cannot publish agent
versions or mutate the application deployment; publishing remains an explicit
operator action.

The trusted `Foundry PR Assessment` workflow records the semantic policy version
and actual Foundry version separately, stamps a two-hour deadline, and publishes
one canonical GitHub artifact. Automatic PR evidence contains no source-line
text. Workers retrieve it only through the provenance verifier:

```bash
python3 .github/scripts/fetch_agent_handoff.py \
  --sha FULL_HEAD_SHA --kind deployment_assessment \
  --output /tmp/deployment-assessment.json
```
