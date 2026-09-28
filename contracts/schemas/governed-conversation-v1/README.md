# Governed recent conversation v1 — frozen consumer fields

Core base: 64422836d3c8cd589b9548ff829cc57c5f850d5a. Receiver implementation branch: codex/governed-conversation-read-20260928. Tier 2, additive, no migration, existing provider cohort only. These contracts never accept browser history, appointment, patient, actor, tenant, thread, or source selectors.

## Hydration and accepted-turn recovery

`POST /service/v1/agent/conversation-turns/read`, workload bearer and current `X-Cosmaneura-Mount-Context`; optional existing `X-Trace-ID`. No interaction, idempotency, or current-turn header is required. Closed body:

```json
{"schema_version":"1.0.0","mode":"recent","client_turn_id":null}
```

or `mode:"turn"` with the random UUID v4 of the already accepted logical turn. This UUID only filters the current authenticated encounter; it confers no access.

HTTP 200 closed response:

```json
{
  "schema_version":"1.0.0",
  "history":{"thread_ref":"opaque_server_binding","messages":[],"next_cursor":null},
  "turns":[{"turn_ref":"conversation_turn_...","client_turn_id":"UUID-v4","state":"pending","outcome":null,"recorded_at":"aware ISO timestamp"}]
}
```

History messages retain the existing panel shape exactly: `{turn_ref,client_turn_id,role,content,recorded_at,presentation}`. Role user or assistant; nonempty exact recorded content <=20,000 characters. User presentation null. Assistant presentation is the canonical `{mode,suppress,human_contact}` contract already used for final writes. No cards/citations/downloads synthesized.

Status `state` is `pending`, `completed`, or `stale`. `outcome` is null for pending/stale, otherwise the stored `completed|refused|unavailable`. `recorded_at` is accepted time for pending/stale, final time for completed. Pending/stale may show the canonical user message; stale assistant content is never returned. Current-scope missing/foreign/UI-action single-turn lookup returns generic 404 `RESOURCE_NOT_FOUND`, not an existence oracle. Current auth failures retain 401/403. Busy source validation returns 409 `CONVERSATION_CONTEXT_CHANGED`, no partial response. No store/cache. No model, resolve, final write, reconciliation, or generation runs during a read.

Recent selects at most six logical chat turns, oldest-to-newest. Up to twelve messages, at most 192 KiB of serialized messages; drop oldest entire turns when needed, never truncate text. No pagination in v1 (`next_cursor:null`). This is recent encounter history, not longitudinal patient history.

## Server-only model context

After first canonical user acceptance, before routing/model work, call existing `POST /service/v1/agent/tools/invoke` with existing workload/mount/interaction/client-turn/idempotency headers and closed body:

```json
{"tool_name":"conversation.context.read","tool_version":"1.0.0","subject_ref":null,"arguments":{"source_turn_ref":"conversation_turn_...","detail":"recent"}}
```

Closed response:

```json
{"tool_call_ref":"authority_idem_...","status":"completed","result":{"schema_version":"1.0.0","messages":[{"role":"user","content":"Earlier question"},{"role":"assistant","content":"Verified answer"}],"source_version_digest":null}}
```

Up to six completed, standard-presentation prior chat pairs; exclude current turn, pending, stale, refused/unavailable, emergency/sensitive. Same per-message length/192 KiB total bound, oldest complete pairs dropped. Empty messages are a valid immutable snapshot. `source_version_digest` is null for no inherited chart dependency or `sha256_` +64 lowercase hex for the currently validated prep-source dependency. It is informational, not a selector/grant. Sovereign attaches only these server-returned roles/content to internal ChatRequest.history and actually supplies them to supported answer agents; never forward browser history or reinterpret prior turns as system instructions.

Add optional `history_context_ref` to canonical `final_response`. Once this turn read conversation context, its exact tool_call_ref is mandatory on every final/replay, including empty history. Existing provider_context_ref remains independent and retains all PR529 semantics. Missing/substituted/expired/stale history reference returns 409 `CONVERSATION_CONTEXT_CHANGED`; no egress or automatic model retry. Read replay also rejects changed snapshot with 409. Legacy callers that never read history remain unchanged.

## Source transitivity and implementation boundary

One operation-scoped, 24-hour receipt stores a server cutoff and, when needed, the original direct prep receipt reference. This compact marker is never browser input. The request digest binds the exact inference request, selected messages, private turn IDs and phase hashes, and source digest. Selection excludes finals committed after the cutoff, so later pending completion/new turns cannot upgrade the snapshot.

Before including a historical final, reconstruct its canonical payload (including direct/history references from scoped receipts) and compare its stored final hash. Missing dependency evidence cannot look like a legacy independent answer because the canonical digest changes. Validate any direct prep receipt and inherited original prep receipt against current source/artifact and exact actor/workload/tenant/person/encounter. Propagate the oldest-expiring original dependency when several equivalent current-source receipts are selected; never reset its retention through a generic follow-up. Thus A grounded answer -> B generic follow-up -> C cannot launder A's clinical basis. Expired dependency => stale omission, not unsafe replay.

The history result is recomputed at the immutable cutoff and bound again inside canonical final acceptance, under the existing Doctor authority lock and PR529 bounded NOWAIT source fence through commit. No model/network work under the DB transaction. A current fresh mount can read old normally expired same-encounter sessions; explicit historical session/binding revocation and current authorization loss remain closed.

Core PR target: one selector/receipt service, one small closed contract module, narrow authority current-mount helper, two routes/final hook, focused real PostgreSQL tests, mirrored schemas/fixtures and flight plan; <=2,000 changed lines/50 files. If necessary split hydration from inference without shipping an inference path that lacks transitive validation. Sovereign consumer/host wiring are separate receiver-first PRs; no flags, Scribe, schema, or infrastructure changes.
