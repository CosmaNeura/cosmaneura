# Retained public review-record adapter (Tier 2)

## Review41 correction — September15

The original monotonic sequence check allowed missing SSE frames. Require the
first sequence to be zero and each subsequent provider frame exactly previous+1.
Retain all lifecycle, response identity, tail, external hash and false-authority
checks. New fixtures cover shifted starting numbers and a removed middle frame
with recomputed transport hashes; neither may normalize into accepted evidence.
This changes public evidence normalization only, not the sealed human runtime.
The failed exact-head review remains historical; new source review is required.

## One outcome

Convert the established sender's retained public evidence into the exact review
record consumed by the snapshot artifact reader. Stack on PR409 at
e301ad893351d9e679948cf8e82e981e72c6ce65. No consumer dependency or source pin
changes: this is an offline preparation helper, outside the human entrypoint.
It neither authenticates a provider nor grants source/merge/execution authority.

The independently reviewed caller must obtain all seven input hashes, response
ID and completion time from the actual authorized authenticated transport and
preserve its provenance. Invented consistent inputs are NOT independent evidence.
This adapter cannot establish that a network request happened or bind an account
to an endpoint by itself. The hard-coded endpoint label is the established
transport contract, not a signature or a permission to use another sender.

No network, file write, source/runtime loader, encrypted workspace, account/cache,
plan/handoff creation, renewal, claim, firewall, snapshot, restore or deployment
is performed. Direct execution refuses. There is no automatic retry or repair.
Do not call a human-only consumer to test this helper.

## Exact evidence and normalization

Require exactly the original protected request file, terminal response, text,
sender summary, raw SSE stream, preserved stream identity and stream diagnostic.
Every input has a fixed size bound and must match its externally supplied digest.
Reject duplicate JSON keys, non-finite constants, malformed/non-object JSON,
wrong subject/bundle/kind, mismatched request/response metadata, future completion,
non-UTC timestamps, wrong response identity and changed transport/authority flags.

Validate the recorded SSE lifecycle and sequence through its complete captured
tail. Require one creation and one matching completed terminal response, reject
error/incomplete/failed events, duplicate/reordered sequence numbers, identity
changes, mismatched event labels, partial frames and trailing events. Compare
the parsed terminal response structurally against the original response file.
This is consistency validation, not independent transport authentication.

Require completed assistant text with no tool outputs or incomplete/error state.
Keep the artifact reader's exact single leading PASS and no-P0/P1/P2 decision
lines. The human/reviewer must still inspect the whole actual verdict and its
limitations; a formatting check is not semantic independent acceptance.

The sender appends one framing newline to its request file and text file. Remove
only those two known framing bytes in the RETURNED records after checking all
original hashes. Extracted provider text must equal the original text file minus
exactly that one newline. Preserve any newline that was actually in provider
text. Response bytes remain completely unchanged. Return normalized summary
hashes plus the original seven-digest map; never overwrite/relabel old evidence.

The immutable result has no plan, handoff or receipt. Source records must bind
the source bundle as their subject; plan/handoff records only normalize existing
independent reviews. Actual artifact expiry/order, protected source provenance,
explicit identity and human-presence checks remain in the existing launcher.
An expired or changed plan/handoff cannot be made usable by normalization.

## Validation and limits

Twelve new fixture methods passed initially. Two new framing/Unicode/boundary
methods and two affected methods then passed: fourteen distinct passing methods.
The methods include subcases for every input bound, missing/extra files, hash
drift, duplicate/non-finite JSON, wrong request metadata, transport flags, time,
incomplete messages, quoted/conflicting decisions, stream sequence/identity/
terminal/tail errors, diagnostics and exact newline preservation. All review
and authority facts are invented. No actual acceptance or human consumer ran.

Ruff/format, explicit-interpreter Pyright and whitespace checks pass. Initial
Pyright narrowing errors were resolved by casts AFTER existing runtime checks;
no acceptance condition was weakened. Unchanged passing suites were not replayed.

A separate read-only diagnostic parsed the actual saved review39 SSE transcript
and matched its complete terminal response. It did not normalize or promote
review39's CI verdict into a staging review, write files, send a request or run
the snapshot consumer. Review39's missing staging metadata remains disqualifying.

Independent exact source review and specifically authorized protected merge are
still required before use. Review40 concerns the earlier snapshot source stack,
not this new helper. The existing review quota is not extended by this PR.
GitHub Actions remains disabled; no hosted tests or deployment route is added.

## Recovery and remaining delivery

Stop using this dormant helper on failure. Preserve every original evidence
file; correct source through a reviewed PR, never rewrite an old verdict.
The actual authenticated producer, protected provenance, fresh reviewed plan and
handoff, human-owned encrypted workspace and Terminal action are still required.
Snapshot/isolated restore/four migrations, staging release, Clerk and ordered
Zoom acceptance are incomplete. This is preparation for those outcomes, not a
replacement for them or a general-purpose recovery platform.
