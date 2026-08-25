# Flight plan: <feature name>

> Copy to `docs/flight-plans/<feature>.md` BEFORE writing feature code.
> The agent drafts this; the founder approves it. CI rejects feature PRs that
> don't link a flight plan.

## Scope
- **Problem / outcome:**
- **In scope:**
- **Explicitly NOT in scope (non-goals):**
- **User cohorts affected:** (new users / existing users / specific roles)

## Risk
- **Tier:** 0 / 1 / 2 — and why
- **Touches auth, billing, or patient-data schemas?** yes/no → if yes, list
  the migration ladder rungs this feature requires and link each rung's PR
  as it lands.

## Flag
- **Flag name:** `ff_<feature>_<yyyymm>`
- **Owner:** 
- **Expiry date:** (when the flag should be removed after full rollout)

## Schema changes
| Change | Ladder rung | PR |
|---|---|---|
| | | |

## Rollout plan
1. Founder + synthetic practice — date:
2. Pilot practice — date:
3. 25% — date:
4. 100% — date:

## Health signals (checked between cohorts)
- (e.g. login success rate, error rate on <endpoint>, canary journey pass,
  support pings from pilot practice)

## Rollback trigger + move
- **We roll back if:** (specific, measurable — e.g. login success < 99% over 30 min)
- **The move is:** (flag off / revert PR # / ladder rung rollback)

## Stack outline (planned PR layers)
1. Seam/interface —
2. Core logic —
3. UI —
4. Flag wiring + rollout —
