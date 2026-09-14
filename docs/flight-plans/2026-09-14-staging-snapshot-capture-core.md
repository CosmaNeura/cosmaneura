# Bounded snapshot sequencing core (Tier 2)

## Outcome and limits

One small sequencing core for the already-approved staging logical-snapshot
route. It sequences HUMAN single-IP rule creation/removal, bounded read-only
capture, closure observation and value-minimized publication. The helper has
NO firewall mutation operation. It is not a new recovery platform, cloud service
or deployment workflow.

The owner explicitly approved a separately reviewed human-managed single-IP
window of30minutes, with the owner creating/removing the rule and accepting
concurrent-edit risk. This changes the earlier automatic-ownership contract;
it does not waive independent source/plan/handoff review or prove atomicity.
Azure's checked firewall contract uses create-or-update and asynchronous delete,
without demonstrated conditional ownership semantics. Never claim otherwise.

There is deliberately NO live adapter, executable human launcher, ARM request,
database client invocation, credential resolution or restore implementation in
this change. Running the module refuses. Tests provide in-memory operations;
passing them does not establish a real firewall change, snapshot, encryption,
identity, restoration, recovery or migration. No live action is authorized by
constructing the dataclasses or by a test result.

## Retained authority boundary

The future exact, independently reviewed human launcher must bind normally
merged control source, immutable runtime/client/library pins, exact fresh plan
and handoff review, configured-user identity linked to the exact ARM token and
MSAL home account, and both foreground human confirmations before entry.
Operation inputs here are evidence REFERENCES, not trusted authorization.
The manual-window approval reference must be authenticated by that launcher,
not accepted merely because it looks like a SHA256.
The adapter must actually revalidate these gates where the core requests it.
No partial or dummy adapter is acceptable for use.

Plans live four hours from their real creation timestamp; handoffs live ten
minutes. Start requires more than120seconds of handoff life and more than the
entire30-minute access bound left on the plan. The separate launcher retains
the longer pre-prompt lifetime check and distinct confirmation/action timing.
No artifact renewal, expired-receipt reuse, one-use claim creation/deletion,
changed-plan acceptance, approval bypass or extra source merge is implied.

The receipt is a start gate, not a reason to abandon emergency cleanup after
the operation starts. Active capture and cleanup remain bounded by the plan and
access deadline. No second operation may start using an expired handoff.

## Execution contract for the future adapter

- Exact existing staging server/database only, verify-full TLS and pinned CA.
- Current IPv4 verified before and after HUMAN opening; equal start/end, globally
  routable canonical IPv4, no private/range/Azure-wide rule. Unique exact rule
  name, absent before creation, with all existing rules retained.
  Compare rule names case-insensitively, matching ARM resource identity; a
  differently capitalized existing rule must never be overwritten as "new".
- Show both exact reviewed human commands and the absolute closure deadline
  before releasing creation instructions. The user runs creation/removal in a
  separate regular Terminal tab; the foreground observer NEVER runs those
  mutations. A partial prompt/interrupt still requests human closure. Human
  progress confirmations are not repeated scope-approval requests.
- Creation must finish normally (including the provider's long-running action)
  before capture. Recheck original handoff validity, identity, exact current
  rule, baseline and encrypted destination; do not reset clocks after waiting.
- One monotonic timeline covers preparation and operation; no reset after
  slow prerequisites. Capture capped at20minutes/1GiB, with five minutes
  reserved for closure inside the overall30-minute bound. Adapter must enforce
  time/output bounds while streaming and manage/reap child process groups.
- Reviewed empty encrypted payload descriptor from PR394. Exclusive no-follow
  output creation, never a detached mount's plaintext fallback directory.
- Confirm actual source89c31f0ae275, complete custom-format archive and final
  digest, not just file presence. Existing broad source role is explicitly NOT
  least-privileged: assess the fixed read-only command in exact plan review.
  No new role/grant, source SQL mutation, application import or migration.
- No raw credentials, records, DSNs, provider responses or private diagnostics
  in metadata, console, repository or external review. No ambient PG/DYLD/proxy
  overrides; no shell interpolation or arbitrary command/target arguments.
- Return a capture result only after all runtime checks pass. The result does
  not attest restoration, migration, provider isolation or deployment readiness.

## Failure and cleanup

No automatic PUT, DELETE, retry, ownership claim or existing-rule repair exists.
Once instructions might have been seen, the finally path cancels/reaps capture
and requests HUMAN removal even on interruption, expiry or a broken clock.
Cancellation or failed readback cannot suppress that request. The human must
settle ALL creation/removal operations, including unknown outcomes, and attest
both that none remains pending and that exact rule removal completed. A missing
attestation refuses even if a read happens to show absence. This is operator
evidence plus observation, not a provider-side serialization guarantee.

The exact reviewed human procedure must explain how to inspect any unknown
long-running action before removal. A failed/timed-out CLI is not proof that
the action stopped; never replay it blindly or accept a temporary absence.
Changed rules or baseline require explicit operator attention, no helper repair.

Never assume native Azure firewall TTL. A crashed machine, network loss,
unreaped child, changed rule, or failed absence readback requires immediate
human action and must not be called successful. The exact live plan must
specify that operator fallback; these tests do not prove crash-proof cleanup.
Do not introduce a scheduled cloud executor as an unapproved workaround.

Success metadata is published only after human terminal-operation/removal
attestations plus observed new-rule absence and unchanged prior rules. It says
human-only management and explicitly false atomic ownership/native expiry/
crash-proof cleanup. These bounded observations do not exclude later concurrent
edits; that residual risk is the specific owner-approved policy, not hidden.
Partial encrypted artifacts are preserved, not accepted or
automatically deleted. Retention and later exact cleanup remain separately
scoped. Existing backups and persistent staging data are untouched.

## Validation and recovery

New pure tests cover ordering, stale/extended artifacts, unsafe IPs, duplicate
inventory, preexisting-name refusal, unknown HUMAN create/delete outcomes, source or
archive errors, changed IP/rules, deadline overruns, clock jumps, interruption,
failed cancellation/removal, pending-operation attestations, partial prompts,
shared human-wait deadlines, no mutation methods, and publication only after
closure. Fixed-code public exception normalization includes pre-window and
publication failures, malformed IPs and malicious exception subclasses. No unchanged
passing suites are manually replayed; required GitHub CI still applies.

Independent review follows the final substantive push. This PR stays dormant
until a complete live adapter and human launcher are independently reviewed
and normally adopted, with a fresh exact plan/handoff. Recovery now is to stop
using the unactivated core; there is no deployed state or source data to revert.

Prior review30 FAIL is retained as history: P1 unsupported automatic ownership
is replaced by this separately approved human-managed contract, requiring
independent re-review; P2 redaction is corrected, also requiring re-review.
