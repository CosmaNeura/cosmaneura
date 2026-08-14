# Private Scribe transcript intake

The master platform accepts completed Scribe transcripts at
`POST /service/v1/scribe/transcripts`. This route is separate from the Clerk-protected staff API.
Clerk remains the staff login system, and patient identity is unchanged.

## Trust boundary

The route accepts an app-only Microsoft Entra access token and requires the
`Scribe.Transcript.Write` application role. It validates the published signing key, issuer,
audience, tenant, expiration, `idtyp=app`, and the exact configured Scribe client ID.

The request includes only the canonical encounter ID, Scribe session ID, duration, and transcript
segments. The platform loads the encounter and derives its clinician and patient. It rejects body
fields that try to assert clinician, patient, tenant, or scheduling source.

## Non-secret configuration

Configure these values on the web Container App after the Entra app registration and role
assignment are complete:

```text
SCRIBE_HANDOFF_ENABLED=true
ENTRA_TENANT_ID=<tenant-id>
SCRIBE_API_AUDIENCE=api://<platform-api-client-id>
SCRIBE_CALLER_CLIENT_ID=<scribe-managed-identity-client-id>
```

These are identifiers, not credentials. Do not add a client secret. Scribe uses its Azure managed
identity to obtain a short-lived token for `api://<platform-api-client-id>/.default`.

The matching Entra setup and rollout steps are documented in the Scribe repository at
`docs/MASTER_PLATFORM_HANDOFF.md`.

## Persistence and replay behavior

The database migration adds a non-PHI `scribe_transcript_delivery` receipt. It binds both the
caller's idempotency key and Scribe session ID to a canonical encounter, payload hash, and platform
transcript. Identical retries return the same transcript as `replayed`. A changed payload or
identifier reuse returns `409`; content is never overwritten.

The note-schema and engagement jobs are dispatched once after acceptance. A durable marker repairs
a transient broker failure on the next identical retry; downstream task idempotency makes this
at-least-once dispatch safe. The browser upload and AssemblyAI path remains present during the
feature-flagged rollout.

## Acceptance check

Use only a synthetic encounter and transcript:

1. Deploy the migration and code with `SCRIBE_HANDOFF_ENABLED=false`.
2. Configure Entra and the three identifiers, then enable the master endpoint.
3. Confirm no token returns `401`, an unauthorized app returns `403`, and a valid Scribe token with
   the application role creates one transcript.
4. Replay the same request and verify `200`, `disposition=replayed`, the same transcript ID, and only
   one note/engagement pipeline invocation.
5. Verify logs and errors contain no transcript content, names, DOBs, tokens, storage paths, or
   scheduling-vendor data.
