# Private Scribe transcript intake

The master platform accepts completed Scribe transcripts at
`POST /service/v1/scribe/transcripts`. This route is separate from the Clerk-protected staff API.
Clerk remains the staff login system, and patient identity is unchanged.

## Trust boundary

The route accepts an app-only Microsoft Entra access token and requires the
`Scribe.Transcript.Write` application role. It validates the published signing key, issuer,
audience, tenant, expiration, `idtyp=app`, and an exact two-entry physical-caller allowlist. The
entries are the normal Scribe API identity and the independently governed delivery-reconciler
identity. Missing, malformed, or duplicate entries fail closed for both callers.

The request includes only the canonical encounter ID, Scribe session ID, duration, and transcript
segments. The platform loads the encounter and derives its clinician and patient. It rejects body
fields that try to assert clinician, patient, tenant, or scheduling source.

## Non-secret configuration

Configure these values on the web Container App after the Entra app registration and role
assignment are complete:

```text
SCRIBE_HANDOFF_ENABLED=false
ENTRA_TENANT_ID=<tenant-id>
SCRIBE_API_AUDIENCE=<platform-api-client-id>
SCRIBE_CALLER_CLIENT_ID=<scribe-managed-identity-client-id>
SCRIBE_RECONCILER_CALLER_CLIENT_ID=<scribe-reconciler-managed-identity-client-id>
```

These are identifiers, not credentials. Do not add a client secret. Scribe uses its Azure managed
identity to obtain a short-lived token for `api://<platform-api-client-id>/.default`. Keep
`SCRIBE_HANDOFF_ENABLED=false` while either caller identifier or its role assignment is being
prepared. Do not give the reconciler identity an application-role assignment until the coordinated
Scribe claim/fencing repair and private-path acceptance are ready.

The platform API app registration must issue v2 access tokens and add the `idtyp` optional claim
to access tokens. The v2 token's `aud` is the bare application client ID UUID; it is not the
`api://.../.default` scope string.

`SCRIBE_API_AUDIENCE` is specifically the master platform API's inbound audience. Do not put the
Scribe API application ID there. The opposite platform-to-Scribe direction is independently gated
by `SCRIBE_WORKFLOW_ENABLED=false` and uses
`SCRIBE_SERVICE_SCOPE=api://<scribe-api-application-id>/.default`; see `SCRIBE_WORKFLOW.md`.

The matching Entra setup and rollout steps are documented in the Scribe repository at
`docs/MASTER_PLATFORM_HANDOFF.md`.

## Persistence and replay behavior

The non-PHI `scribe_transcript_delivery` receipt binds both the idempotency key and Scribe session
ID to a canonical encounter, payload hash, and platform transcript under the server-owned logical
producer `cosmaneura-scribe-v1`. Neither a request nor a token claim can choose this producer.
Uniqueness is `(logical_producer, idempotency_key)` and
`(logical_producer, scribe_session_id)`, so an identical retry through either physical identity
returns the same transcript as `replayed`. A changed payload or identifier reuse returns `409`;
content is never overwritten.

`caller_client_id` remains the actual physical identity that created the receipt. Every accepted,
replayed, conflicting, or authorization-rejected request also emits a fixed metadata-only audit
event with the actual authenticated client ID. The caller is audit provenance only and never an
idempotency namespace.

The migration refuses to collapse legacy namespaces if duplicate keys or session IDs exist. Its
server default keeps an older application revision compatible during a rolling deploy. Downgrade
restores the former per-caller constraints and drops only the logical-producer column; receipts and
transcripts remain. Do not downgrade after granting the second caller access because doing so would
reintroduce the unsafe physical-caller namespace.

The note-schema and engagement jobs are dispatched once after acceptance. A durable marker repairs
a transient broker failure on the next identical retry; downstream task idempotency makes this
at-least-once dispatch safe. The browser upload and AssemblyAI path remains present during the
feature-flagged rollout.

## Acceptance check

Use only a synthetic encounter and transcript:

1. Deploy the migration and code with `SCRIBE_HANDOFF_ENABLED=false` and only the existing API
   identity assigned the application role.
2. Configure both physical client identifiers, enable the master endpoint, and verify the existing
   API caller's create/replay behavior before assigning the reconciler role.
3. Confirm no token returns `401`, an unallowlisted or delegated caller returns `403`, and a valid
   API token with the application role creates one transcript.
4. After the coordinated Scribe repair is accepted, assign the same application role to the
   reconciler identity. Deliver through the API identity and replay the byte-identical request
   through the reconciler identity. Verify `200`, `disposition=replayed`, the same transcript ID,
   one receipt, and only one note/engagement pipeline invocation.
5. Verify a changed cross-identity binding returns `409` and that audit events identify each actual
   caller without transcript content, names, DOBs, tokens, storage paths, or scheduling-vendor data.

Rollback first revokes the reconciler application's role assignment. Keep the corrected schema in
place for the normal API path. Only if an application rollback requires the legacy schema, confirm
the reconciler can no longer obtain the role-bearing token, disable the endpoint, and then run the
migration downgrade.
