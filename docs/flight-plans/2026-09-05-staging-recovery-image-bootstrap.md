# Staging recovery-image bootstrap

Status: first read-only capture failed closed; no mutation occurred. Risk: Tier 2. Sole dispatcher: `JohnClem`.

## Outcome and hard boundary

This one-time control turns the running historical staging image from a mutable-tag observation into a digest-pinned recovery baseline. It neither makes that image a staging candidate nor deploys new source. Merge executes nothing: capture and pin are manual `workflow_dispatch` modes from exact current protected `master`, behind the `staging` environment and non-cancelling `cosmaneura-staging-container-apps` concurrency group. Original and rerun actor must both be `JohnClem`.

It never builds, pushes, imports, retags, migrates, changes secrets, environment, ingress, scale, command, or other runtime configuration, rolls back, or deletes. `infra-staging/deploy.sh` stays unchanged and continues to require a digest.

## Evidence and explicit limitation

The policy pins source `8ea64a5704868031dec66d1f81bb698137bfb29f`, GitHub run `33667675179` attempt 1/job `100375775869`, raw-log digest `sha256:dc938ad83287e70755b45f5da8c7a944e5b48f92a41d3f25f505535c25a479c7`, single-platform manifest `sha256:475908dbb4c6820f91ca6ccd626312ef4f7591c8555923f02949520fcd4208d7`, and `linux/amd64` config `sha256:67a86442ae6166ccf679ff56b445ee674694b7d13307b1d491d0c40ea7924a24`. The validator rechecks raw bytes, source/run labels, availability, exact tag timestamps, and unique ordered build/push/deploy log anchors.

This is retrospective provider evidence, not a publication attestation: the image is unsigned, ACR retained no repository-event diagnostics, its admin user is enabled, and the tag/manifest began writable and deletable. Pin therefore requires review of a fresh capture receipt and this exact phrase:

```text
ACCEPT RETROSPECTIVE PROVIDER EVIDENCE AND PIN sha256:475908dbb4c6820f91ca6ccd626312ef4f7591c8555923f02949520fcd4208d7 TO 3 STAGING WORKLOADS
```

Read-only capture run `33968924079` on protected source `5cd49afc5cc5e500d310730e890777fd37c91f14` authenticated the exact environment-scoped OIDC identity, then failed during initial observation because the installed Azure CLI already returned the diagnostic-settings array and the additional `--query value` transformed it into a non-array. Terminal receipt `sha256:9f5b23d02e3598dac041915f08d5086434d7b43beaf672770cccbd2e998fa973` records `status=failed`, `phase=initial_observation`, no evidence, and all five mutation-journal entries as `not_attempted`. The corrective change consumes the command's native array and adds a regression assertion; any retry still requires fresh action-time confirmation.

## Prerequisites and procedure

Before dispatch, merge after exact-head review; change the GitHub `Staging` environment to its sole custom branch policy `master`, with protected-branch mode off, custom policies on, admin bypass off, and only the branch-policy protection rule; and leave exactly one staging OIDC credential with issuer `https://token.actions.githubusercontent.com`, audience `api://AzureADTokenExchange`, and subject `repo:CosmaNeura/cosmaneura:environment:staging`. Remove the legacy branch credential in that separately approved window.

Reconfirm client `45b940bf-2f4f-4b10-bcc3-4679d8855a6f`, principal `a94600b7-c854-4121-bbcb-a0fa7baae14a`, tenant `d51754d0-bba5-4810-820e-afac542844d6`, subscription `bab2a709-1fcb-4c87-a9fc-802f7477ec13`, and a quiet staging window. At execution time the OIDC session must expose that as its sole enabled subscription; scoped inherited queries across the exact staging resource group, ACR, managed environment, and three Container App targets must union to exactly three effective assignments: ACR `AcrPush`, managed-environment `Contributor`, and staging-resource-group `Contributor`. This is a fresh six-surface gate, not retrospective evidence. Broader absence of unrelated subscription assignments was separately administrator-audited and is not claimed as OIDC-capture proof.

1. Dispatch capture with all pin inputs blank. Download its sole artifact and review the run ID, exact receipt SHA-256, expiry, policy, and live evidence.
2. Within 30 minutes, dispatch pin from the same still-current `master`, giving only that run ID, receipt digest, and the exact phrase above.
3. Retain and independently review the terminal receipt and workflow checks.

Pin redownloads the receipt, verifies canonical bytes and run provenance, and recollects identical evidence. Any master/identity/run/digest/timestamp drift, ambiguous or reordered log, missing/multiarch manifest, source-label mismatch, sidecar, workload disagreement, unhealthy revision, config drift, tamper, expiry, or out-of-order/config-drift partial state stops it. Exact monotonic prefixes are resumable: tag locked; tag plus manifest locked; then worker, scheduler, and API independently pinned in that order. A retry revalidates and skips each completed phase.

Immediately before each mutation it rechecks current `master`, the complete expected progress state, the still-valid capture receipt, and exact Azure identity/access. It then disables write/delete and reads back the exact SHA tag and manifest, followed by revision-copy of worker, scheduler, and API from each exact historical revision using only the digest and fixed `recovery-475908dbb4c` suffix. Each remains Single-mode with one healthy/provisioned active revision, identical sanitized template/config, and API 100% latest traffic. Any third or out-of-order state stops for a new reviewed recovery plan.

After validated-policy/temp-workspace initialization installs its trap, every execution phase emits a non-secret terminal receipt, including partial failure. Pre-initialization failure is surfaced by the workflow and artifact step rather than overclaimed as receipted.

Even a complete receipt says recovery eligibility is false until an independent exact-receipt review is recorded. A later canonical staging deployment remains a separate founder decision with its normal source, schema, synthetic evidence, identity, and recovery inputs; this bootstrap proves none of those gates.
