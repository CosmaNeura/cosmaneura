## What & why

<!-- 1-3 sentences. Link the flight plan if this is feature work: -->
Flight plan: docs/flight-plans/<feature>.md

## Risk tier

- [ ] **Tier 0** — copy/styling/docs/config text/additive UI behind existing flag
- [ ] **Tier 1** — new logic/endpoints/additive schema (flag + flight plan required)
- [ ] **Tier 2** — auth / billing / patient-data / destructive migration (full gates)

### Tier 1+ checklist
- [ ] Behind feature flag: `ff_...`
- [ ] Flight plan linked above and up to date

### Tier 2 checklist
- [ ] Migration ladder rung: `expand` / `dual-write` / `backfill` / `shadow` / `cut-reads` / `contract` (one rung only)
- [ ] Rehearsal run against de-identified prod snapshot: green (link)
- [ ] Rollback move written below
- [ ] Founder sign-off

## Rollback move

<!-- The exact action if this change misbehaves in production: -->

## Change record (HIPAA change management — auto-audit trail)

- **Systems/data affected:**
- **PHI impact:** none / read path / write path / schema
- **Tested by:** CI + <manual/canary/rehearsal as applicable>
- **Reviewed by:**
