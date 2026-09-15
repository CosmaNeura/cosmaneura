# Read-only snapshot source collection (Tier 2)

## One outcome

Collect actual GitHub readbacks and local Git object bytes for the snapshot's
exact18-file source bundle. Stack on PR410 at
8af123a56a2262f62b3cb7b27ad7f26e70951a8d. This is public preparation, outside the
human consumer; no bootstrap dependency/pin or deployment policy changes.

The expected PR head and every file digest MUST come from an actual independent
review of the WHOLE bundle, not only a patch, a locally computed hash or an
implementer's assertion. A source-review record and external provenance remain
necessary; this module cannot independently establish the review. Current
review40 is FAIL and does not authorize use of its corrected successor.

The selected final PR must be merged to this repository's protected master.
All18 files are attributed to that reviewed final head as REVIEW COVERAGE, not
as their original introduction/author. Earlier component PR review requirements
remain and are not waived. The final head and protected source must have exactly
the reviewed bytes for every loaded file. Unrelated later master changes cannot
silently change a loaded source file; any mismatch refuses.

## Closed read-only transport

Only the recorded GitHub CLI2.97.0 and macOS Git executable are used. Check their
fixed canonical leaf, owner/mode/size/content and retained metadata before/after
each command. The Git leaf is root-owned and has system hard links; this is not
the snapshot runtime's one-link file policy. Source constants pin these exact
observed public binaries; updating either requires a new reviewed source pin.
This is a cooperative trusted-CLI interface, not a malicious same-UID/root/native
code sandbox or a defense against all executable-path races.

Use a closed environment with the effective user's ordinary GitHub configuration,
no inherited token/proxy/config overrides, explicit github.com, disabled prompts,
no system/global Git config, no replacement objects or optional Git locks.
Do not change sign-in, roles, scopes, tokens or credential configuration.

Allowed GitHub calls are GET only: master, master protection, the existing exact
ruleset, Actions permission, one exact PR, exact-head statuses and exact-head
check runs. Routes cannot escape this repository or change pagination. Only
local Git origin/shallow/common-directory metadata, exact commit/blob reads and
one ancestry query are allowed. Fetch, push, checkout, status publication, merge,
arbitrary paths/commands, and arbitrary GitHub GET/POST routes are rejected.

Each child has an original30-second ceiling within the collection's180-second
deadline. Stdout is bounded while reading (128KiB per source/commit,8MiB per API
reply); stderr is discarded, not logged. Children run in owned process groups.
Failure kills/reaps the still-unreaped owned group; PID reuse cannot occur before
that wait. No retry or background child is launched. There is a separate bounded
two-second cleanup wait, never an extension of accepted evidence time.

## Exact checks and preserved evidence

Require the expected origin, complete local history and no legacy graft file.
Missing Git objects refuse; any ordinary fetch must be a separate authorized
preparation step. Current master must equal the external expected source commit
and remain protected. Preserve strict Backend/Frontend/change-budget, issuer-null
policy, admin enforcement, stale-review/conversation settings, zero existing
native approvals, no force/deletion, exact active ruleset and empty bypass list.
Actions must remain disabled. This collector never changes those settings.

Require the exact reviewed PR head, same head/base repository and master target,
closed/merged state and provider merge SHA. Recompute that local Git commit's
object ID and require its normal two-parent shape with reviewed head second;
require it contained in the current source commit. This proves content/ancestry
and compatible merge structure, not an audit of historical branch settings or
the user's separate merge consent. Preserve the actual normal-merge action and
independent-review evidence for the final plan reviewer too.

Require genuine successful current required statuses/checks at the exact head.
Reject missing/pending/failing results and same-name non-success check runs even
if a status says success. Reject incomplete pagination or duplicate status names.
The approved issuer-neutral policy is not cryptographic local-test attestation;
the durable actual test commands/results and unchanged-input reuse evidence must
still support any success report. No review text is treated as test execution.

Read every source file at both reviewed head and protected source. Compare bytes
and the external SHA256; the expected name catalog is closed. Refresh required
statuses/checks, master, full protection, ruleset and Actions state afterward.
Changed source/policy or failed checks refuse. Use the original observation time,
not completion time, so collection work cannot renew the ten-minute source
evidence age. Reject total-time excess and wall/monotonic clock divergence.

Return immutable tuples of original public evidence and source bytes, their hash
map, and the exact existing artifact-reader provenance schema. No file is written,
source loaded, authorization minted, or evidence signed. The caller must preserve
original records with exclusive protected writes and retain producer/transport
provenance for independent review. Collection is not an atomic lock against later
GitHub changes; recheck at the required handoff/merge gates. No source record,
plan or handoff may be fabricated, relabeled, extended or reused after change.

## Verification and remaining work

Sixteen new fixture methods passed, then one new tool-pin test passed:17 distinct
methods. Cases include complete schema/file coverage, external digest validation,
input-map snapshotting, unmerged/foreign/wrong-head PRs, master/file drift, shallow/
grafted/missing history, corrupt or non-normal merge objects, policy and check
drift, collisions/pagination, elapsed/clock bounds, invalid JSON, closed commands,
clean environment and tool content/type/mode changes. All GitHub/Git collection
facts are invented. The schema check reads the existing artifact-reader AST,
never imports or invokes the human consumer.

Dedicated real local Python children verified successful bounded stdout, oversized
stdout refusal, timeout cancellation, descriptor closure and reaping. No network,
credentials, encrypted workspace or live snapshot ran. Ruff/format, explicit-
interpreter Pyright and whitespace checks pass. An AST fixture type-narrowing
correction added assertions without changing the executed acceptance logic.

A separate public read-only diagnostic verified the actual pinned Git/GitHub
tool bytes and read the already-merged407Git object. Its object ID and two exact
parents matched the recorded normal merge. It made NO GitHub network call and
did NOT invoke collect_provenance or produce any source/plan/handoff artifact.
It is tool/Git-read compatibility evidence, not live acceptance of the snapshot.

Independent exact source review is pending. No review quota remains and no new
submission/merge authority is inferred. Source recovery is a normal reviewed
revert or leaving this dormant; no deployed state or user data was changed.
Next finish the reviewed public artifact producer and actual human-return gates,
then snapshot/isolated restore/four migrations and the separately authorized
human staging release. Clerk and ordered Zoom acceptance follow usable staging.
