# Flight plan: occurrence-aware Zoom capture and assignment

## Outcome and boundaries

- Problem: reusable Zoom rooms and back-to-back visits need durable occurrence, encounter, segment, and assignment state.
- This PR adds dormant schema only; it changes no Clerk, Entra, MCD, Acuity, Zoom/Recall behavior, billing, BAA, environment, Terraform, or production.
- Unmatched encrypted content stays provider-scoped, expiring, and unassigned; soft matching never grants chart authority.

## Risk and dependencies

- Tier 2: this creates patient/PHI-path schema. Later behavior waits for dual-write, shadow UX, and a real smoke.
- #239 snapshot `b0397946d9fe74dd205339fdc9d47fd602df66e3` has no file overlap, but its routing, allowlist, alert/watchdog, CLI, and bot-state contracts conflict.
- Recheck final #239 for overlap, one state owner, linear migrations, and a combined Zoom-to-Recall smoke.

## Current state and release control

- Cosmas already has provider appointments, patient name/DOB, schedule times,
  Zoom identifiers, and intake. No additional MCD or Acuity access is required.
- There is no canonical appointment-to-occurrence key. Rank provider-local
  candidates by time/name/DOB; require exact binding or provider confirmation.
- No feature flag applies because this rung exposes no user-visible behavior.

## Schema/data sequence

| Rung | Invariant | Recovery |
|---|---|---|
| Dormant foundation | Global active meeting/occurrence/bot IDs; immutable provider lineage; encrypted expiring pending content | Leave tables dormant; downgrade refuses after use |
| Later behavior | Capture first, assign second, atomically remove from Needs Assignment | Disable behavior; retain rows |

## Rollout, security, and completion

- Before PHI use, rehearse `docs/MIGRATION_PLAYBOOK.md`; record only counts,
  hashes, timing, invariants, and cleanup proof.
- Abort on cross-provider attachment, stale/duplicate occurrence, lost artifact, missed bot join, or canonical transcript before confirmation.
- Require focused/migration tests, full CI, exact-SHA independent review, final
  #239 deconfliction, and a join/disclosure/stop-start/assignment smoke.
- Merge is not migration, activation, deployment, or Build & Publish; each later production step remains founder-authorized and manual.
