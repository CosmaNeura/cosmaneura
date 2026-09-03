# Provider-only V7 staging pilot

This runbook is for supervised synthetic pilot operation only. The activation
record is
[`change-records/2026-08-21-v7-provider-staging-activation.md`](change-records/2026-08-21-v7-provider-staging-activation.md).

## Guardrails

- Use synthetic prompts only; never enter or replay PHI.
- Never print or retain JWTs, mount tokens, claims, request/response bodies,
  complete environment output, or raw protected record references.
- Keep public V7, labs, patient search, journey digest, Magisterium MCP,
  production, real PHI, and V8 out of this pilot.
- Do not reconcile broadly or mutate a record until its durable outcome is
  known. Never alter the accepted failed/failed row.

## Start, end, and post-deploy checks

Run this checklist at the start and end of every operator window and after any
staging deployment. Stop if any required result differs.

1. Read the platform, provider, and public revisions with narrow queries:

   ```bash
   az containerapp show -g cosmaneura-staging -n cosmaneura-staging-app \
     --query '{latest:properties.latestRevisionName,ready:properties.latestReadyRevisionName,image:properties.template.containers[0].image}' -o json
   az containerapp show -g cosmaneura-sovereign-staging -n cosmaneura-sovereign-staging-app \
     --query '{latest:properties.latestRevisionName,ready:properties.latestReadyRevisionName,image:properties.template.containers[0].image}' -o json
   az containerapp show -g cosmaneura-sovereign-staging -n cosmaneura-web-staging-app \
     --query '{latest:properties.latestRevisionName,ready:properties.latestReadyRevisionName,image:properties.template.containers[0].image}' -o json
   ```

   Require equal latest/ready revisions. The activation record contains the
   initial accepted baseline, not a permanent pin: after a reviewed staging
   deployment, require the live Platform tag to equal the exact audited
   `staging` commit used by the operator checkout. Resolve every tag to its
   registry digest; a tag alone is not immutable evidence. Provider and public
   must remain at their separately accepted immutable digests unless their own
   reviewed deployment intentionally changes them.

2. Require HTTP 200 from the platform `/healthcheck` and both Sovereign
   `/healthz` endpoints. Retain only the expected health fields. Provider must
   report `status=ok`, `mock_mode=false`,
   `mount_profile=provider_assistant`, and
   `platform_agent_api_enabled=true`; public must report `status=ok`,
   `mock_mode=false`, `mount_profile=public`, and
   `platform_agent_api_enabled=false`.

3. Query only the named environment values. Require:

   ```text
   platform: AUTHORITY_KERNEL_ENABLED=true, AGENT_API_ENABLED=true
   provider: MOUNT_PROFILE=provider_assistant, PLATFORM_AGENT_API_ENABLED=true
   public:   MOUNT_PROFILE=public; PLATFORM_AGENT_API_ENABLED is unset or false
   both:     MOCK_MODE=false, ALLOW_PROVIDER_PERSONA=false,
             FEATURE_ATHENA_LABS=false, LABS_ALLOW_PATIENT_SEARCH=false,
             MAGISTERIUM_MCP_ENABLED=false
   ```

4. From the operator checkout, run the committed value-filtered status wrapper:

   ```bash
   bash infra-staging/run-provider-v7-status.sh
   ```

   [`run-provider-v7-status.sh`](../infra-staging/run-provider-v7-status.sh)
   injects only a value-free check into the deployed Platform image, so it
   does not require the smoke harness to be present there. Require
   `V7_STATUS PASS`. It checks the exact one-row failed/failed baseline and
   protected-reference hash in-process and emits no reference or counts.

5. After the next Platform deploy containing the harness commit, run the
   committed
   [`run-provider-v7-smoke.sh`](../infra-staging/run-provider-v7-smoke.sh).
   This future post-deploy gate is not a current closeout blocker. Require
   `V7_SMOKE PASS`, covering provider success, missing/invalid-context denial,
   public-workload denial, a complete user/final turn, complete audit/usage
   ingestion, idempotent replay, and cleanup of every temporary session.
   Health alone is insufficient.

## Stop conditions

Stop new pilot traffic and preserve value-minimized evidence on any:

- unexpected image, revision, feature flag, workload authorization, or public
  access;
- new/missing ingestion row, pending count, baseline hash/state change, or
  query error;
- failed health or positive model-path check;
- PHI, token, identity, prompt, or response content in logs or evidence; or
- residual temporary session, role, or ambiguous identity binding.

Record only UTC time, immutable image/revision, fixed error class, HTTP status
family, aggregate counts, and hashes of protected references. Do not blindly
retry a mutating request or delete a failure.

## Isolate and roll forward

1. Stop synthetic traffic. Change only the provider app to internal ingress,
   keep insecure transport disabled, then verify `external=false`:

   ```bash
   az containerapp ingress enable -g cosmaneura-sovereign-staging \
     -n cosmaneura-sovereign-staging-app --type internal \
     --target-port 8000 --transport auto --allow-insecure false
   az containerapp ingress show -g cosmaneura-sovereign-staging \
     -n cosmaneura-sovereign-staging-app \
     --query '{external:external,targetPort:targetPort,transport:transport}' -o json
   ```

2. Confirm the provider origin is externally unreachable. Leave platform and
   public apps unchanged. Re-run the ingestion check and resolve only an exact
   pending record through separately approved, dry-run-first reconciliation.

3. Diagnose from value-minimized evidence. For simple unreviewed image drift,
   redeploy the accepted provider digest from the activation record under a
   fresh revision suffix. Otherwise build and review a new immutable fixed
   image and deploy that provider-only. Never deploy the old
   `198536a5...e8f2` image; it is known to fail positive chat.

4. While ingress is internal, verify the new digest, feature fences, health,
   and direct `gpt-5.6-sol` behavior. Restore external TLS-only ingress only
   after those checks pass:

   ```bash
   az containerapp ingress enable -g cosmaneura-sovereign-staging \
     -n cosmaneura-sovereign-staging-app --type external \
     --target-port 8000 --transport auto --allow-insecure false
   ```

5. Immediately repeat every post-deploy check and the full synthetic smoke
   test. If any check fails, return provider ingress to internal and stop.

If platform authority itself is suspect, use the reviewed staging deployment
path with `ENABLE_PROVIDER_V7=false` so backend and frontend V7 gates turn off
together. Do not make independent ad hoc gate changes.
