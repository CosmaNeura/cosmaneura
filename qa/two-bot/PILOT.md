# Core acceptance backlog

Owner: release owner. All gates are NOT RUN. Assign the browser bot for each
profile after isolation is approved. A row is not an eligible queued batch;
source-review concrete fixture names, actions and expected results on the chosen
build, then populate batch.example.json. Do not guess current deployed behavior.

| Gate | Browser coverage and definition of done | Source starting point |
| --- | --- | --- |
| C01 baseline | Both bots identify own synthetic practice, role, release and protected fixture inventory; differences documented | web/app/routes/notes/index.tsx; app/services/doctor_access.py |
| C02 profile A | Bot 1 creates/edits owned synthetic fixtures and completes its approved note journey; exact saved state checked after reload | web/app/routes/notes/detail.tsx; web/app/components/notes/UploadRecordingButton.tsx |
| C03 profile B | Bot 2 repeats the approved journey using distinct fixtures/configuration; both inventories remain independent | Same routes; inspect current forms and terminology before writing cases |
| C04 handoff | Approved synthetic Scribe-to-Core journey completes on pinned Core AND Scribe commits with record lineage | docs/flight-plans/staging-scribe-prep-reconciliation.md |
| C05 boundaries | Approved second-role/tenant checks expose no other bot's records; denials leave owned state intact | app/services/doctor_access.py; tests/services/practice_access_test.py |
| C06 upgrade | Owner-controlled test upgrade preserves both synthetic profiles and fixtures; browser bots verify afterward | Existing release handoff and migration controls |
| C07 recovery | Owner rehearses restore/recovery on disposable target; bots check restored state against inventory | Existing rehearsal runbooks; separate operation approval required |
| C08 operations | Verify approved provisioning, pause/close/export/support journeys that actually exist; park absent features with owner | Source-review routes before queuing |

First batch should target C02 on bot-1, C03 on bot-2. Select 6-8 concrete
state-changing cases from implemented upload/note UI, two relevant edges (e.g.
invalid file and retry), dependent reload checks, own-prefix cleanup and final
inventory. Confirm file constraints and UI text from current source. Never
fabricate clinical records or bypass access gates to make a case runnable.

Evidence packet per gate: case IDs, exact frontend/backend SHAs (both repos for
C04), sanitized browser observations/screenshots where approved, synthetic record
IDs, remaining/protected fixture inventory, defect links, independent verifier.
No gate advances from the local ledger alone. Scope excludes live billing,
external communications, production and real patient data.

Validation commands: `just py_test && just js_test` for a fixer PR, plus relevant
focused regressions and `just lint` as applicable. The offline ledger has its own
stdlib unittest suite; app-wide tests do not prove browser acceptance.
