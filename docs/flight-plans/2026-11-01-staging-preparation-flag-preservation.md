# Preserve existing staging preparation controls

Tier 2: a narrow canonical deployment-contract repair. Source publication,
merge and staging re-dispatch require separate approval; no production or
runtime change is authorized by this local source outcome.

## Problem and resulting behavior

Canonical phase-one run `41339202271` refused the full-runtime fingerprint
before migrations or workload updates. Staging already exposes
`SCRIBE_ENCOUNTER_CREATION_ENABLED` and `COMPANION_SESSION_PREP_ENABLED`, but
the resolver's fallback differs from the installed controls and the public
preservation contract cannot carry either key. Matching identity fingerprints
does not establish a matching complete runtime configuration.

Add a separate optional complete pair of canonical boolean strings to the
reviewed runtime-public contract. Both values are explicit inputs; no missing
key is defaulted, no new enabled state is chosen and all four existing contract
shapes remain valid. The previous five-flag preservation group stays independently
optional. Partial pairs, noncanonical values, duplicate JSON keys and unrelated
controls remain refused.

The existing deployer already overlays validated public input before calculating
the complete runtime fingerprint. It already owns this pair only on Core web.
Its workflow, credential resolver, workload contracts, identity gates and
fingerprint/live-comparison functions remain unchanged. Other configuration
differences must continue to block deployment rather than be assimilated.

## Local verification and release sequence

Preserve the initial failing tests against unchanged `f6a8bd75` source. Run the
affected normal infrastructure tests with synthetic public controls and values:
old and combined contract shapes, all pair combinations, exact parser output,
partial/invalid/duplicate refusal, actual deployment overlay, workload scope and
continued full-fingerprint refusal for unrelated configuration drift. Derive
the unchanged fixed 107-path audit from a genuine committed source predecessor
and update its matching verifier digest; do not bypass the canonical source gate.

After final independent review and source approval, publish and merge the repair
against current protected master. Use a fresh reviewed dispatch bound to the
new controller and an explicit existing-state pair; preserve the selected reader
candidate, immutable image, schema sequence and recovery bindings. Recheck the
full runtime before migration or workload changes. Retrying the old controller
or weakening a fingerprint guard is not recovery.

## Recovery and limits

This source repair changes admission of an optional explicit pair, not runtime
defaults or deployed settings. Existing plans without it remain valid. Reverting
the parser and matching audit metadata together restores prior admission while
the full-runtime check stays in place. Existing Scribe work, application source,
database schema and production remain untouched. Sanitized hash-only evidence
cannot exclude other public or secret differences; local success alone does not
prove the next deployment or live provider workflow will succeed.
