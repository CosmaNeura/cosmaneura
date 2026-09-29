# Governed recent conversation in the dashboard

Risk: Tier 2 (current-record authorization and retained clinical conversation).
No schema, dependency, cohort, Scribe, capability or environment changes.

The dashboard supplies the existing panel `loadHistory` bridge with the current
mount credential. It posts only the versioned recent-read selector to the fixed
Sovereign provider origin. Record IDs stay local for equality checks; browser
messages, thread references and chart selectors never enter the request.

The adapter enforces the frozen closed envelope: at most six logical turns,
twelve messages, 20,000 Unicode code points per message, and 192 KiB serialized
messages. A 256 KiB streamed response limit bounds parsing. It retains canonical
text and presentation without inventing cards, citations or downloads. Explicit
pending/stale statuses remain distinct from answers. No model or `/chat` request
is made by history hydration.

Every asynchronous authority, fetch and body-read boundary checks cancellation
and the captured record generation. Access denial closes the generation. Record
change, access loss, unmount and the existing 30-second bootstrap deadline abort
history reads. Only explicit Reconnect starts another authenticated generation;
errors never become a success-shaped empty conversation.

## Receiver-first sequence

1. Core PR531 supplies the current-encounter reader and final source validation.
2. Sovereign PR60 supplies `/embed/history` and the panel's closed-envelope/status
   consumer. Its immutable panel asset pin lands independently in Core.
3. This host adapter lands on that pin. Final substantive push requires an
   independent reviewer who did not implement this change.
4. A separately authorized release deploys the exact reviewed Core and Sovereign
   pair. Source merge and local tests do not establish live acceptance.

## Validation and acceptance

Focused transport tests cover request authority, closed payloads, Unicode and
aggregate bounds, pending/empty history, denial and stream cancellation. Real
pinned-panel tests cover canonical hydration with zero chat requests, fresh
reauthentication, wrong/late records, invalid/oversized payloads and bootstrap
timeout recovery. Existing host, refresh, reconnect, load-recovery and Session
Prep completion suites remain affected checks, alongside TypeScript, ESLint,
Prettier and diff checks. Retained validation evidence records exact commands,
source hashes and the actual asset used.

Live acceptance remains: open/reconnect the approved fictional encounter and
observe its authorized recent conversation; confirm pending/stale notices are
not answers and a record switch never shows the previous record's response.
Do not upload a new fixture or modify another task's Scribe encounters.

## Recovery

Revert this host adapter to omit `loadHistory` and restore the prior fixture
expectations. Keep the additive Core reader, Sovereign consumer and compatible
asset pin; there is no database rollback. Any staging rollback uses the reviewed
release's retained compatible revisions and preservation checks. Do not bypass
authorization or substitute empty history to recover availability.
