# Visit Prep package read for CosmaBot

**Status:** Ratified design; receiver qualification pending · **Version:** 1.0.1

This boundary lets a physician explicitly open the same Core-owned Visit
Prep package on the Cosmaneura dashboard or in CosmaBot, including a preparation
without a chart or encounter. Core owns patient words, corrections, review,
synthesis, access and retention. CosmaBot renders that package; it does not create
a second clinical record or rewrite the brief through another model.

John approved implementation and architecture on October 2, 2026. Architecture
v3.6 and ADR 0028 ratify this design at
`6b879150205f275612d1a3fd2318530dfa331857`, merged through
[architecture PR 21](https://github.com/CosmaNeura/cosmaneura-architecture/pull/21).
The [governing contract](https://github.com/CosmaNeura/cosmaneura-architecture/blob/6b879150205f275612d1a3fd2318530dfa331857/contracts/visit-prep-companion-v1.md)
is authoritative; this branch copy tracks implementation requirements. Ratification
is not evidence that a receiver is operational. Schemas and fixtures are test material, not authorization
for deployment, patient contact or live processing. Normative words below describe
the approved design, not currently available receiver behavior.

## Ratified architecture decision

The ratified design verifies two credentials on every read: CosmaBot's Entra
app-only workload token and the physician's current Core Clerk session token.
The preparation must belong to that physician. No persistent preparation bearer
or mount grant is added. Session Prep's encounter-bound mount and permission are
not used as Visit Prep authority.

An alternative is a new short-lived opaque preparation grant, with issuance,
revocation, expiry and replay storage. Direct per-read authentication was selected
for this read-only operation because the companion already supports authenticated
Core connection reads. Physician authentication needs a designated adapter;
domain code must not parse Clerk claims.

ADR 0028 and the corresponding governing updates:

- Recognize CosmaBot as a companion presentation surface for Visit Prep without
  replacing Sovereign or changing Session Prep.
- Exempt this pre-visit package from the encounter prerequisite. Capture, notes,
  signing, EHR access and other encounter-bound workflows keep their requirements.
- Govern a dedicated read with explicit physician-action audit, not a generic
  conversational Agent API invocation.

ADR 0028 narrowly amends ADRs 0001 and 0008 for this Core-owned, chart-free
pre-visit display. Sovereign remains the conversational brain; Session Prep
retains its encounter-bound authority and sole approved provider action rail.
The broader approved Visit Prep scope still includes patient AI clarification,
bot actions and booking/email/SMS/reminders. This first read contract neither
authorizes those actions nor removes them from the delivery goal.

## Endpoint and credentials

Endpoint: `POST /service/v1/companion/visit-prep/package`. It accepts one
JSON request and returns one fully buffered JSON response. It MUST NOT stream
clinical content, redirect, use query parameters or invoke a model. Merely opening
a workspace MUST NOT perform a read.

| Header                                | Rule                                                                                                                                     |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `Authorization`                       | Entra app-only bearer for the explicitly allowlisted CosmaBot workload, configured Core audience/issuer/tenant and `AgentApi.Access` role |
| `X-Cosmaneura-Provider-Authorization` | Current physician Core Clerk session bearer, verified by Core's designated auth adapter                                                  |
| `X-Cosmaneura-Visit-Prep-Version`     | Exact value `1`                                                                                                                          |
| `Content-Type` and `Accept`           | `application/json`                                                                                                                       |
| `Accept-Encoding`                     | `identity`                                                                                                                               |

The receiver returns `Cache-Control: no-store`, `Referrer-Policy: no-referrer`,
`Content-Type: application/json` and the exact version header on success and
failure. Both credentials are bounded to 8,192 ASCII characters and MUST NOT
contain whitespace other than the single bearer scheme separator.
Each security/version header MUST occur once. Credentials MUST NOT enter bodies,
URLs, responses, logs, traces, browser persistence, errors or model input.
Duplicate JSON keys, unknown fields, non-finite numbers, compressed bodies,
ambiguous headers and invalid UTF-8 fail closed. Actual received bytes enforce
the 2,048-byte request limit independently of Content-Length.

Core MUST verify both credentials on every read, including a retry, and reject
support/view-as sessions. It re-resolves the existing reviewed provider-account
connection and compares its current actor, tenant and connection digest with the
request assertions. Assertions detect wrong selection; they never grant access.
No read creates an actor, membership, provider link, patient, chart, capture
binding or appointment. Identity correspondence is not licensure verification.

## Request and physician intent

The [request schema](request.schema.json) is closed. Action is `visit-prep.open`
and purpose is `provider_previsit_review`. Workspace and fresh request UUIDs come
from an explicit physician UI action. The preparation reference comes from an
authorized Core selection, not a model-invented patient/encounter mapping. It
remains an untrusted selector until Core verifies exact current ownership.

Core MUST enforce current practice workforce membership, physician ownership,
legal/BAA state, seat/entitlement, package-read capability, cohort controls,
preparation and contribution validity, and applicable retention/hold rules.
Neither credential alone nor a formerly valid connection permits disclosure.

Only the bounded connector receives the physician credential through the
authenticated host. Models receive neither credential and cannot select headers,
origin, scope or action. This grants no generic RPC, EHR access, mutation,
generation, sending, signing or consent transition. Follow-up reasoning or chat
inclusion requires its own ratified context/lineage boundary.

## Atomic Core read

The [result schema](result.schema.json) wraps the existing dashboard synthesis
projection. Core MUST use the same current-source/artifact domain in one
caller-owned SQL transaction, holding ownership/lineage locks through receiver
authorization, content-free action/package audits and the final commit. Staged
content is private until commit acknowledgment succeeds.

After lock/audit waits and immediately before commit, Core MUST recheck
time-dependent physician, preparation and receiver authority against database
time. Token and overall request deadlines remain in force. Denial, serialization
failure, audit failure or uncertain commit requires discarding staged content.
A separately committed dashboard read is not a receiver transaction substitute.

The response echoes exact workspace, request, preparation and current connection
digest. `checked_at` is the final successful authorization observation, not a
claim of permanent access. `package` is `visit_prep.patient_synthesis.v1`:

- Same package ID, question-set version, source version and contribution basis
  as the dashboard at that observation.
- Applicable physician topics with current and initial patient/caregiver words
  and attributable revisions/source references; hidden topics disclose nothing.
- Separately attributed current explanations for coded answers, with
  `explanation_words`, `explanation_source_id` and `explanation_recorded_at`.
  These do not replace selected codes or add physician topics. Changing the
  answer revision invalidates its prior explanation; current reads omit it.
  A withdrawal returns null words and source ID while retaining its recorded
  time and underlying evidence in Core.
- Explicit missing/skipped topics and completion/review flags. Original interview
  completion is distinct from current answer completion.
- Same current artifact ID/version, generation profile and exact brief. Stale
  artifact content is never returned.

The unconditional fifth topic remains the free-form `story`. Conditional topics
may be absent without being presented as applicable skips. An unlinked preparation
has null patient and appointment IDs; no identity or encounter is fabricated.

Both implementations MUST check closed schemas and these semantic relationships:
package IDs match; topic keys/positions are unique and ordered; topic states
determine missing/skipped sets and completion; reviewed answers are complete with
current review reference/time; citations resolve only to current answered sources
and quote their exact words (choice values use the physician's current label;
explanations cite their own separate source and words, never the code source);
and brief/artifact fields obey ready versus stale/missing rules. Core's source
digest and grounding validation remain authoritative. Schema-valid fixtures
alone prove no authorization, database, clinical-quality or operational property.
Fixture identities, source/connection digests and model profile are invented
shape examples, not cryptographic provenance or live authentication evidence.

`ready` requires a current artifact and brief. All-skipped input may have a valid
empty brief. `stale`/`missing` return current authorized patient sources but no
artifact fields or brief. Build status describes current-source work only. A
read MUST NOT enqueue generation or substitute stale output.

The complete UTF-8 response is capped at 262,144 bytes; overflow fails without
truncating patient words. A 15-second total read budget includes credential
acquisition/verification, SQL waits and receipt validation. No automatic polling,
model work or transport retry is introduced.

## Failure and repeat reads

Errors are static value-free JSON, such as `{"detail":"Visit Prep is unavailable."}`.
They contain no selector, identity, clinical data or internal exception.
Authentication failure is 401; missing/wrong current authority is 403 without
confirming preparation existence; invalid input is 422; excess input is 413;
unsupported version is 426; transient uncertainty is 503; deadline is 504.
No failure becomes a successful empty patient report.

A transport retry MAY reuse its request UUID and exact body, but is independently
reauthorized and observes current state. It MUST NOT replay a retained clinical
response. Correction or artifact publication can therefore change a later read.
An explicit reload uses a new request UUID. UUIDs are correlation, not authority
or evidence of a conversational model turn. Late results cannot revive cleared
or superseded client actions. Read audits may record repeated observations;
this is not a messaging submission or exactly-once delivery claim.

## Presentation and invalidation

CosmaBot MUST label patient/caregiver words and AI interpretation separately from
physician-reviewed/signed facts. It renders the exact brief with access to
original/current words, citations, missing topics and patient review state.
Patient text is plain text, never HTML, executable Markdown or tool authority.

The client MUST validate closed receipts, byte/deadline bounds, echoed scope,
version, MIME type, identity encoding and `Cache-Control: no-store` before
disclosure. It rechecks local connection and selected preparation/workspace
before and after the read. Credential expiry, account/workspace switch,
hidden/unfocused view, lost connection, denial and observed source change clear
the package and suppress late results. Reopening requires fresh authorization;
offline/cached clinical fallback is forbidden. Authorization is observation-time,
not continuous knowledge of server-side withdrawal after the response.

This read MUST NOT durably store patient content or credentials in CosmaBot.
Core retains the encrypted canonical package and existing retention/legal holds.
Audits contain opaque scope/version references, purpose, workload, physician
session lineage, state, timing and outcome, never patient words or credentials.
Transient buffers are discarded after use.

## Qualification and activation

Architecture is ratified. A synthetic-qualified receiver precedes the companion
adapter; neither is qualified solely by design approval or these schemas.
Acceptance covers actual database lock waits,
BAA/member withdrawal, credentials expiring during waits, wrong workload/account/
workspace, commit acknowledgment loss, correction, no chart link, partial
caregiver reports, all-skipped reports, hostile wire input, late results and exact
dashboard/bot package and artifact version parity.

All release controls stay default-off. This contract grants no application merge, deployment,
real messaging, PHI/model/provider processing, cohort activation or workspace
retirement. Design ratification is separate from clinical, delivery and release
acceptance.
