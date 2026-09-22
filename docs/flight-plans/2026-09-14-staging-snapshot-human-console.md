# Foreground human snapshot confirmations and removal fallback (Tier 2)

## One bounded outcome

Implement the actual Terminal interaction needed by the snapshot capture core.
Stacked on PR401's test/format-correctedbc3ff7aa. No live entrypoint or generic supplied
input/output callbacks. No new infrastructure, network, dependency, original
cache access, credential, firewall mutation, image creation, claim or deployment.
An agent must never drive this class against the user's real Terminal.

The current approval is the human-managed single-current-IP firewall window of
at most30minutes, with the human creating/removing the rule and accepting
concurrent-edit risk. This source does not broaden, renew or request that scope.
Two foreground execution confirmations remain required by the capture contract.
Exact plan and identity/workspace hashes are displayed once; the user types two
short fixed public phrases, not repeated long hashes. This is not receipt review
or proof of source provenance; those gates precede construction in the launcher.

## Actual Terminal binding and deadlines

Require the dedicated sealed/core-disabled process, three standard character
devices owned by the effective user, one terminal device and current foreground
process group. Hold non-inheritable duplicate descriptors and compare standard
and owned descriptor identity before/after every I/O. Refuse redirects, changed
ownership/device/mode, background groups, raw mode or non-echoed input.
Initial descriptor/native checks have a five-second guard. Each later interaction
uses one original absolute guard; no nested/restarted clock. Bounded ASCII input
and output, post-read deadline checks, and input flushing before each prompt
prevent a pasted batch from answering multiple confirmations. No secret prompt.

The console is opened before the capture core and closed in the outer finally
only AFTER the core's human removal request. Its process-local flags prevent
repeat prompts, not cross-process replay. Source/plan/handoff/one-use authority
still belongs to the final protected launcher; a TTY does not confer it.

## Human-only firewall workflow

Print the UTC removal deadline, exact Azure target, removal procedure and warning
that Azure does not auto-expire this rule BEFORE printing any creation details.
If fallback output fails partway, creation instructions are never released.
The human uses the existing Azure Portal's server Networking / Firewall rules
page, the exact rule name and identical start/end IPv4. No helper runs a mutation
CLI or copies a command into another Terminal. Portal use avoids this helper
changing the original CLI cache while its identity binding must remain stable.

After a successful creation operation the human explicitly confirms completion.
Every core finally path requests removal, including expiry, interruption, failed
capture or partially displayed instructions. The human must settle ALL pending
or uncertain creation/removal operations and then verify removal. Only the exact
combined terminal-and-removed answer yields HumanClosure(true,true); an absence
report while creation is pending is not sufficient. The core MUST independently
read back absence and unchanged pre-existing rules. No atomic ownership, native
TTL or crash-proof cleanup is claimed; concurrent edits require human resolution.

## Verification, recovery and remaining gates

New fixture tests cover descriptor/group/owner/mode rejection, duplicate cleanup,
two confirmations, fixed identifiers, prompt order, partial fallback, exact rule
scope, one-shot creation/removal, expired-artifact removal, pending mutation,
input bounds, changed Terminal, timeout and idempotent descriptor closure.
All Terminal metadata/driver/answers and process seal/timer are invented/mocked.
They do not establish real human presence, native behavior or live acceptance.
All19 distinct new methods passed across the implementation sequence: first18,
then one new plus five affected after bounding initial native checks; two affected
creation methods passed after explicit optional-deadline narrowing. Ruff check,
Ruff format check, Pyright and diff checks PASS. No unchanged suite was replayed.

Recovery is to stop using this dormant source; no deployed state changes.
Remaining: full protected source/runtime loader, exact plan/handoff review gates,
current-public-IP binding and complete typed Operations adapter. Actual snapshot,
restore/four-migration rehearsal, staging release, Clerk/Auth and ordered Zoom
acceptance are not performed or accepted here. Current CI and independent exact
source review remain required; this PR is neither merge nor execution authority.
