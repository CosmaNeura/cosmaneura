# Scoped Scribe native preparation read

Tier 2: this additive server contract reuses a context credential. It does not
reuse an authorization decision, issue a capture permission, or start a source.

After consent admission, iPhone recorder setup currently performs another policy
exchange and mount before obtaining its native recording namespace and public
wrapping key. `prepared_native_read` accepts only `native_recording_preparation`
through the existing authenticated native route. It uses the current prepared
window caller, including current provider/visit identity, both workload policies,
token age/revocation and ordinary admission/consent checks before and after I/O.
The result uses the existing receipt contract. No context credential reaches a
device. No schema, dependency, consent state or audio format changes.

Release order:

1. Independently review the final Core and Scribe commits. Merge Core first.
2. Verify the chosen immutable Core candidate against deployed staging revision
   57, digest `6641b43828c545f0807ab3e688e464368f96686e0317f2aae52a922e4cc93eb8`.
   The candidate is based on protected master `0e0f76bc1386cd1dfd097ea40bc42b9c8fb54da0`.
   Promotion follows the existing founder-controlled staging workflow.
3. Verify `X-Cosmaneura-Prepared-Native-Read-Version: 1` on a successful Core
   native response. The receiver uses this additive capability only after Core
   advertises it. Existing receivers ignore the header; new receivers use the
   ordinary read when it is absent. They never retry a failed prepared call.
4. Release the receiver change, preserving existing cohort/configuration. Its
   single consumed-read marker permits one successful read followed by the first
   window using the original issue time. Context age limits stay unchanged.
5. Repeat fictional preparation and recording-to-transcript-to-saved-note tests.
   Confirm physical iPhone consent-to-first-input timing separately. Virtual
   results never establish the five-second device target.

Recovery: roll the receiver back before Core when recovering this cross-repo
change. A new receiver also handles an older Core via its absent-capability path.
Uncertain reads discard the pending context and publish no result. No database
cleanup or migration is required. Production promotion is outside this plan.

Local evidence lives in
`output/scribe-provider-ux-20261007/autonomous-startup-rounds-20261013` in the owner
workspace. Core's real synthetic PostgreSQL tests cover equivalent receipts,
wrong context/identity/visit, committed and mid-request revocation, expiry,
malformed replies, current recording status and excluded operations. The measured
read executes 163 statements instead of 342. These local statement counts and
elapsed times are not remote phone measurements. Independent review remains a
required release gate; it has not been claimed as complete.
