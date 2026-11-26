# Access-counts SQL rationale and exact PASS criteria (R2 precheck r2, Thu 26 Nov 2026)

**Status of the SQL:** this is a NEW candidate. The CTO chose it at 9:22am ET. Fable has **not reviewed it yet**, and it goes into the delta review. The text is byte-identical to `/workspace/pc-test/access.sql`, a copy the Build Lead wrote at 8:54am ET. It is NOT the `access-counts.sql` that Codex wrote on John's Mac at 8:03am ET. That file is not on the box and nobody here has seen it.
- sha256 of the SQL text: `5568dd8f5aefaef199b32165412a665f5373851ce29d059f94a92270895e700a`. That is the hash of `candidate-access-counts.sql`, of `/workspace/pc-test/access.sql`, and of `runner.py`'s `ACCESS_SQL` literal (`ACCESS_SQL_SHA256`). A test checks that all three are equal.
- It is a single read-only statement: nine `SELECT '<fixed label>', count(*)` branches joined with `UNION ALL`. It takes no input, and every label is a string literal in the SQL. The only values that come back are 9 fixed labels and 9 integers.

## Why these counts (the live-user lock)
The R2 prompt's LIVE-USER LOCK says existing live users (MCD pilots, existing practices, comp and pilot-grant accounts) keep access exactly as they have it today. The release may add no agreement, BAA re-accept, practice, onboarding, re-join, billing, Checkout, card or sign-in step. "Access counts after the deploy must be >= the counts before, per category. Any drop … means STOP and print the rollback." These nine counts are that "before" baseline. The same SQL runs again after the deploy, and any drop in any category means STOP and rollback.

Meanings come from the model source at cf67a631 (`app/models/doctor.py`, `practice.py`, `baa.py`, `pilot_access_grandfather_grant.py`). Each table and column is created by a migration dated August 2026, which is older than the live head 49126bf99c72 (31 Oct). None of them is among the 7 pending migrations. **Inferred:** I have not checked the live schema. If a table or column is missing live, the query errors, the runner prints `ACCESS_ERROR=query_refused`, and the run STOPs.

| Label | What it counts | Why a drop would mean live users lost access |
|---|---|---|
| `doctors_active` | `doctor` rows with `deleted_at IS NULL`: clinician accounts that are not soft-deleted. Each one is bound to a Clerk user by `clerk_id`. | These are the accounts that can sign in today. A drop means a migration soft-deleted or lost accounts (c764298c7590 adds the self-delete fields). |
| `doctors_total` | All `doctor` rows, including soft-deleted ones. | A drop means a hard delete. |
| `practices_total` | All `practice` rows. | A practice disappearing is a practice/onboarding wall for its members. |
| `practices_baa_clickwrap_accepted` | Practices with `baa_accepted_at IS NOT NULL`, meaning the click-through BAA was accepted. | A drop would push those practices into a BAA re-accept wall, which the lock forbids (the BAA stays at 2.3). |
| `practices_negotiated_baa_on_file` | Practices with `negotiated_baa_on_file = true`. | Same BAA-wall reason, for practices on a negotiated BAA. |
| `practices_subscription_status_set` | Practices with `subscription_status IS NOT NULL`. | b38d5a410e49 (invoice billing) and 5f3c9a1e7b24 (Stripe unit amount) are pending billing migrations. A drop could move practices onto a billing/Checkout/card path. Stripe stays OFF. |
| `baa_acceptances_total` | All `baa_acceptance` rows: BAA acceptance records. | These are audit evidence and must never decrease. A drop can also bring back a BAA wall. |
| `practice_members_active` | `practice_member` rows with `access_revoked_at IS NULL`. | Membership is what gives a clinician access to a practice. A drop means members lost access or would be sent to re-join. |
| `pilot_grants_active` | `pilot_access_grandfather_grant` rows with `revoked_at IS NULL`. | These are the comp and pilot-grant accounts named in the lock. A drop means pilots lost their grandfathered access. |

All nine are global totals. None of them is per practice, per user or per ID, so the output can't identify anyone.

### What the counts cannot show: the Clerk redirect risk
STEP 1's wall check (8:03am ET, on John's Mac, which I haven't seen) noted a Clerk redirect risk. It's G7: the new web query-principal binding fails closed to /sign-in or /onboarding when identity sources disagree. The counts show that the database-side identity and access rows are unchanged: Clerk-bound doctors, memberships, BAAs and grants. That is **necessary but not sufficient**. A redirect is runtime behaviour, and identical counts don't rule it out. G7 (staging sign-in smoke) and John's own sign-in at STEP 8 still cover it, and they stay required.

## Exact PASS criteria (anything else is a STOP)
These are evaluated on the runner's marker lines. `tests/pass_check.py` is the reference implementation used by the tests.
1. **Markers present exactly once:** `RUNNER_SHA_OK=1`, `RUN_START=1`, `BUNDLE_VERIFIED=97`, `ACCESS_LOCK_TIMEOUT_MS=4000`, `ACCESS_ROWS=9`, `RUN_DONE=1`, `PRECHECK_EXIT=1`, and exactly one `PRECHECK_JSON=` line. Two `RUN_START` lines means the platform retried the replica, and that is a STOP.
2. **No error markers:** zero `RUN_ERROR=`, `PRECHECK_REFUSED=` or `ACCESS_ERROR=` lines.
3. **Access counts:** exactly 9 `ACCESS_COUNT=<label>,<n>` lines. The labels must be exactly the allowlist, in allowlist order: doctors_active, doctors_total, practices_total, practices_baa_clickwrap_accepted, practices_negotiated_baa_on_file, practices_subscription_status_set, baa_acceptances_total, practice_members_active, pilot_grants_active. Each `n` must be a non-negative integer. The runner already prints no counts at all unless this holds.
4. **#992 JSON byte-equal to the expected G2 literal (`G2_SHAPE=match`):**
   ```
   {"abort_counts":{"practice_conflicts":0,"reused_uuids":0,"zero_uuids":0},"e78845cecca6_would_abort":false,"is_ancestor_of_repository_head":true,"migration_head":"49126bf99c72","pending_migrations":["ddd6261fa36f","e78845cecca6","69bad2e072e2","b38d5a410e49","c764298c7590","5f3c9a1e7b24","5708b4399768"],"provider_practice_authority_null_practice_id_count":0,"refusal_reasons":["pending_migrations"],"repository_head":"5708b4399768","scope":"core"}
   ```
   - **Expected alembic head of the candidate:** `repository_head = 5708b4399768`. That is the single head of the cf67a631 migrations, manifest `a3684949bdc4cf9016ee597000dc96abf9ec4a7702bd667743d92bead9fe5895` over 97 files. I recomputed it on the box today, and the synthetic happy path reproduced it with the precheck's own code.
   - **Live head:** `migration_head = 49126bf99c72`, taken from the R2 prompt's G2. **Unverified today.**
   - `e78845cecca6_would_abort = false`.
5. **The carrier's own checks pass** (see README): the execution status is `Succeeded`, the job template is unchanged, and there was one execution with no retry.

`PRECHECK_EXIT=1` is expected and is a PASS, because migrations are pending (R2 G2 reads the JSON, not the exit code).

### Each #992 guard and what counts as PASS
| #992 guard (JSON field) | PASS | STOP if |
|---|---|---|
| Live head is known and is an ancestor of the candidate head (`is_ancestor_of_repository_head`, no `migration_head_unknown`) | `true`, `migration_head = 49126bf99c72` | `false`, `migration_head_unknown`, or any other head |
| Pending migrations (`pending_migrations`, reason `pending_migrations`) | exactly the 7 revisions above, in that order, and `refusal_reasons = ["pending_migrations"]` | any other list or any extra reason |
| Guard tables present (`scribe_capture_provider_link` and `provider_practice_authority`; no `guard_tables_missing`) | both present | `guard_tables_missing` (then `e78845cecca6_would_abort` is null) |
| e78845cecca6 abort guard (`abort_counts`: practice_conflicts, reused_uuids, zero_uuids) | all three are 0, so `e78845cecca6_would_abort = false` and there's no `e78845_abort` | any of them > 0, `would_abort` true or null |
| provider_practice_authority NULL practice_id count | 0 | > 0 (the JSON differs from G2, so STOP and the CTO decides) |

Synthetic tests for each STOP: e78845 abort, guard tables missing, NULL practice authority, unknown head, and an access table missing (`tests/test_runner.py` T07).
