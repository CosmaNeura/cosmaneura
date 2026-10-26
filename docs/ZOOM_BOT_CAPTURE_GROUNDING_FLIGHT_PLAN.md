# Zoom bot capture-grounding pilot

Tier 2: clinical draft generation. No database/schema, privilege, consent,
ingestion or capture authorization changes. This candidate is unqualified until
independent review and actual bot-path evaluation pass.

## Problem and behavior

The previous source-disposition candidate rejected an actual corrected-value
response and omitted a partially answered injury question in a second response.
Its source cards were also too dense for a useful clinical note. Those evaluated
heads, outputs and draft PRs 830/831 remain preserved and unactivated.

This candidate uses the existing capture source-grounding system message,
retention context, response schema and Plan/history resolvers inside the real
bot `summarize.perform` path. It keeps the bot's actual latest example, custom
schema, saved preferences and configured Azure client. There is one final model
invocation and no intermediate action extractor or model repair. Default callers
and the capture implementation retain their prior request and runtime behavior.

Plan text comes from selected complete source entries. With patient-only
Subjective and exclusively unidentified speech, recognized history fields use
the existing source renderer, including SOAP medication-list uncertainty. Other
fields contain model prose: source fidelity, inclusion and usefulness still need
semantic review. Protected fields may remain verbose; no source panel or note
storage contract is added here.

## Source and instruction boundaries

- Recall's stored Doctor/Patient/Other roles are model classifications, not
  independently verified identities. The request retains source preferences and
  explicitly qualifies these roles. Quoted Plan attribution says the same.
  Unknown never becomes Patient or Doctor based on names or apparent dialogue.
- The bot-only history prompt retains clinically relevant unanswered and partial
  questions as source context. It removes capture's contrary omission instruction
  only from the bot request. A question does not establish a denial or plan.
- Source facts override examples and inferred reasons. The saved Plan reason
  preference is retained; protected Plan adds one limitation that rationale
  beyond the selected source is not established. It does not claim a quoted
  source lacks an explicitly stated reason.
- Actual merge boundaries are passed from the loaded transcript lengths. They
  preserve ordinal/recording order but cannot be selected as speech. Identical
  words in actual speech are never dropped through marker-text matching.
- Response shape, duplicate keys, indexes and output sizes fail closed. These
  checks cannot determine whether selected words are a clinical decision,
  whether an omitted fact was important, or whether narrative prose is true.

## Narrow selection and unchanged publication

The selection is the same as the earlier unactivated pilot: staging only, exact
A/B doctor/Clerk/email tuples, exactly one linked consumed Zoom authorization,
and a succeeded Recall ingestion tied to the trigger transcript. The queue hint
is not authority. The job rechecks the policy and current doctor access; any
ScribeCaptureBinding still excludes the ordinary bot path. Existing Zoom consent,
authorization expiry/consumption and ingestion behavior are unchanged. A later
transcript may complete after the original authorization was consumed; this does
not create a new authorization or extend its expiry.

All other calls retain their original dispatch arguments and generation path.
Appointment serialization, retention, latest-transcript idempotency and schema
customization remain unchanged. Invalid bot output raises a value-free typed
exception excluded from Celery autoretry; existing ordinary/network retry policy
is unchanged. The normal UI may remain without a completed note on such a
failure. No success or release-readiness claim follows from an HTTP 200 model call.

## Validation and release sequence

1. Local socket-blocked request/resolver regressions, including the actual A/B
   schemas/examples/preferences and exact default prompt hashes. Source-only
   preservation checks for capture helpers and unrelated summarization functions.
2. Independently reviewed final pushed head. Run isolated owned PostgreSQL tests
   for selection, real save/reopen, no-extra-call replay, typed failure and merge
   boundaries. A handcrafted response proves persistence mechanics only.
3. Separately reviewed maximum two configured calls, once each for preserved R2
   and R3, SDK retries zero. Preserve all failures and independently assess full
   clinical content, unanswered questions, role qualification and verbosity.
4. Only after acceptance: merge and prepare a fresh current-trunk staging image
   reconciliation. No activation/deployment is included in this source PR. A real
   normally authorized bot recording and saved-note review remain required.

## Recovery

Before deployment, keep this draft unactivated or revert its selection/hook commit
through a reviewed forward change. After deployment, remove the explicit policy
selection through a fresh current-trunk build; do not restore an old image over
other features/configuration/schema. Never regenerate, rewrite or delete retained
notes as rollback. Capture/default paths and historical evidence stay intact.
