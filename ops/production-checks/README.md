What happens tonight to existing live users: nothing changes. Nothing runs from this folder until a separately briefed, JohnClem-gated GitHub Actions carrier exists and John clicks it. When it does run, it is ONE extra one-off execution of the existing production migration job (cosmaneura-prod-migrate). Against the database it does SELECTs only, inside READ ONLY transactions. There is no revision, traffic, image, secret, role, firewall or job-template change.

# R2 #992 precheck + access-before counts: runner r2 (Thu 26 Nov 2026, Build Lead)

The carrier-independent runner, rebuilt from the Fable-reviewed runner `f61f498b` to close Fable's `cto-plan-precheck-12cb28f1` P2–P7 and (A), plus the earlier HOLES that apply to the runner. The CTO's 9:30am ET call: John said NO to running from his Mac, so there is no mac-command and no wrapper. The carrier is assumed to be a JohnClem-gated GitHub Actions button on the existing Release identity, with no Mac and no local az. **The carrier is not built here**; a separate brief covers it. The section "What the carrier must supply" is its contract.

## Files
| File | What it is |
|---|---|
| `runner.py` | The in-container runner. Every SQL statement it issues is a literal, and so is the access-label allowlist. Its output passes an enforced output boundary. |
| `bootstrap-args.txt` | The exact single `--args` element. It asserts the runner's sha256 before running `exec`. |
| `candidate-access-counts.sql` | The candidate SQL, byte-identical to `/workspace/pc-test/access.sql` and to the runner's literal. NEW, not yet Fable-reviewed. |
| `SQL-RATIONALE.md` | What each of the 9 counts means, why it matters for the live-user lock and the Clerk redirect risk, and the **exact PASS criteria**. |
| `tests/` | Synthetic-only tests: `run_tests.sh`, `synthetic_pg.sh`, `test_runner.py`, and `pass_check.py` (the reference PASS check). |
| `test-output.txt` | Output of the last test run. |
| `SHA256SUMS` | sha256 of every file. |

## Controls
- **P2, output boundary.** Every output line goes through `emit()`, and there is exactly one `print(` in the file. Only fixed keys can leave the database, each with a type-checked value: fixed codes, the 9 fixed labels, non-negative ints (not bool), booleans (URL_SHAPE), and 12-hex revisions. The #992 JSON is re-validated field by field and must be byte-canonical; anything else becomes `PRECHECK_REFUSED=shape_refused`. Row and column content, DATABASE_URL, exception text and env are never printed. All 26 synthetic tests are green, including row-content leak attempts and a scan of every output for the password, the URL and the synthetic canary rows.
- **P3, label allowlist.** `ACCESS_LABELS` (9 global categories) is in the runner. The result must be exactly the columns `(category, n)` and exactly those 9 labels, with no duplicates and integer counts. If not, the runner prints only `ACCESS_ERROR=shape_refused` and **no counts**. Order doesn't matter on input; output is in allowlist order.
- **P4, runner pinned, SQL literal.** `bootstrap-args.txt` computes `sha256(CORE_RUNNER_Z)` and compares it with the pinned `b940747c…` before running `exec`. On a mismatch it prints only `RUN_ERROR=runner_sha_mismatch` and exits 0. The pin sits in `--args`, so the Azure execution record shows what ran. `bundle["sql"]` is gone: a bundle with any key other than `scripts` is refused. There's no keyword filter and no SQL input path. The runner can issue only `ACCESS_TXN_SQL`, `ACCESS_LOCK_SQL`, `ACCESS_ASSERT_SQL` and `ACCESS_SQL`, plus the #992 SQL inside the sha-pinned precheck script.
- **P5, the real control** (it replaces "require existing coordination", which does not exist).
  - Release is gated to John's own dispatch.
  - **No Plan or Release click happens until the receipt shows `status=Succeeded` and every PASS criterion in SQL-RATIONALE.md holds.**
  - If a real migration overlaps this run, the migration fails fast on its 4s `lock_timeout` (d04cc2ee). It is not corrupted.
  - This runner's access session sets and asserts `lock_timeout = 4s` (`ACCESS_LOCK_TIMEOUT_MS=4000`; a test shows a blocked query is refused after 4.0s). It also inherits `statement_timeout` 30s and `default_transaction_read_only=on` from the #992 `connection_parameters()`.
  - The #992 precheck subprocess keeps its own pinned 5s `lock_timeout`, because its code is hash-pinned and can't change.
  - Overlap is fail-fast, not corrupting.
- **P7, candidate manifest.** Before DATABASE_URL is read, the runner requires the shipped migrations manifest to equal `EXPECT_MIGRATIONS_MANIFEST = a3684949…` (cf67a631, 97 files, recomputed on the box today). The carrier must also check that the candidate SHA's manifest equals it before starting (see the carrier contract).
- **(A), as far as the runner can enforce it.** Both scripts' sha256 and the manifest are verified before the first and only DATABASE_URL read (a static test checks the order). One execution with no retry: the runner always exits 0, so the job's replica retry is never triggered by the runner. Detecting a platform retry (two `RUN_START`), the digest, the template re-check and the status handling are carrier duties (below).
- **Azure side effects, honestly stated.** One job execution and its log lines. The execution's env (both scripts, 97 migrations, the runner) is stored in the execution record and readable by anyone who can read executions. It contains no secret: DATABASE_URL is a `secretref`. Starting an execution is not read-only on Azure's side.

## What the carrier must supply (contract for the separate workflow brief)
**Inputs (exactly these, nothing else):**
- Subscription `bab2a709-1fcb-4c87-a9fc-802f7477ec13`, RG `cosmaneura-prod`, job `cosmaneura-prod-migrate`, app `cosmaneura-prod-app`.
- `az containerapp job start` with a per-execution override:
  - `--container-name cosmaneura-prod-migrate`
  - `--image cosmaneuraprodregistry.azurecr.io/cosmaneura/cosmaneura@sha256:877fe96e9b43297be2ac479ef939c5e47476229fc867062ad0fda2bd1a50fac6`
  - `--command /opt/venv/bin/python`
  - `--args=<the exact contents of bootstrap-args.txt>`, as one element (sha256 `b722cedc815e259920c8cd6f2cb5f891369699183e853612bc95567d83d79a67`)
  - `--env-vars` with exactly these four:
    - `DATABASE_URL=secretref:<selector>`. The selector is `database-url` or `database-url-v2`, and it must equal the live app's selector.
    - `CORE_RUNNER_Z` = base64(zlib(bytes of `runner.py`)). The runner's sha256 must be `b940747c7ed2fdd0d414dca664c8d642adae0f92539fe2ae28d28c72f59a9b59`. About 6.4 KB.
    - `CORE_BUNDLE_Z` = base64(zlib(JSON `{"scripts": {"scripts/production_migration_precheck.py": <text at cf67a631>, "scripts/session_prep_production_eligibility_preflight.py": <text at cf67a631>}}`)). About 13.8 KB.
    - `CORE_MIGRATIONS_Z` = base64(zlib(JSON `{<path>: <text>}` for every `migrations/versions/*.py` at cf67a631)). About 92.4 KB.

**Before the start (each STOPs on failure, and every az call STOPs on error):**
- The candidate SHA is cf67a631 and its manifest equals `a3684949…` (P7).
- The pinned digest is the image of the revision(s) serving 100% of traffic (`revision list`, `trafficWeight > 0`), not just `properties.template`.
- No execution is in a non-terminal state. Anything other than Succeeded, Failed, Stopped or Degraded counts as busy, including null or Unknown.
- Read the template hash using names-only fields (the deploy.sh:172 query plus replicaRetryLimit and replicaTimeout) and record `replicaRetryLimit`.

**After the start:**
- Exactly one execution, no retry, no second start.
- Poll to a terminal state for at most 5 minutes.
- Only `Succeeded` continues. Failed, Stopped or Degraded is a STOP. On timeout or unknown, send `job stop` for that execution first, then STOP.
- Read back the logs filtered to the marker grammar, and write the receipt header (status, execution, selector name, image, runner sha) before judging.
- Re-read the template hash, which must be unchanged.
- Apply the PASS criteria in SQL-RATIONALE.md, including exactly one `RUN_START` and exactly one `PRECHECK_JSON`, no error markers, and `G2_SHAPE=match|differs`.
- Stage the outputs and publish them only after every check passes.
- A STOP after the execution ran needs a fresh John approval, not a rerun. If the log stream is gone, use Log Analytics instead of rerunning.

## John's rule (8:48am ET): alternatives, click count, no-secret/no-shell
### Alternatives considered (carried from baef88fc, updated)
- **Staging-registry cf67a631 image.** Rejected: the prod job authenticates only to the prod registry, so this would need a new secret and a cross-environment widening.
- **Wait for the Release-built prod image.** Rejected: that image is built inside the Release, after the Plan/Apply gate, which would invert the "precheck before Plan" gate.
- **The job's current template tag 7953980159bd.** Rejected: it also lacks the precheck, and it's a tag, not a digest.
- **A new PR or a new image for the runner itself.** Rejected for the runner. Code ships via env with the sha pinned in `--args`. The carrier workflow is a separate brief.
- **The documented path in `docs/production-migration-precheck.md`** (John runs it in an Azure-hosted shell, typing a read-only credential). Rejected: it needs a secret and a shell from John. Two deviations from that doc, to be named in the GATE sentence: this path uses the app's own credential inside a read-only session, and it runs through a job execution. A doc correction is queued.
- **Codex, az or psql from John's Mac, or Cloud Shell.** Rejected: John refused Cloud Shell at 8:28am ET and the Mac-run block at 9:30am ET.
- **CoS running a one-command block in John's session.** Dropped by John's 9:30 NO.
- **Chosen:** a JohnClem-gated GitHub Actions button on the existing Release identity, which widens no access (Fable's 12cb28f1 (B)). Fable could not verify the environment-reviewer settings, so whether dispatch plus environment approval adds up to one click is **Unverified**.

### John's click count: at most ONE
That one click is the carrier's button, by design. This folder adds no click.

### No secrets, no shell for John
John reads nothing and types nothing. DATABASE_URL stays a `secretref` inside Azure. The runner never prints it, and no secret, connection string, token or env dump appears in any output (tested).

## Unverified
- **SQL candidate:** not reviewed by Fable. Table and column meanings are inferred from the cf67a631 source, not checked against the live schema.
- **Live head:** `49126bf99c72` (R2 G2) has not been re-read today.
- **Image contents:** that 877fe96e has `/opt/venv/bin/python`, psycopg 3.2.6 and libpq 17.4 (or newer) is inferred. The tests ran on psycopg 3.3.9 with libpq 18.
- **Azure behaviour:** whether Azure accepts a ~92 KB env value, whether a per-execution override leaves the template unchanged (inferred from deploy.sh:311-315), whether a revision's template image is shown as a digest, the job's `replicaRetryLimit` and `replicaTimeout`, and whether the log stream is still readable after an execution finishes.
- **Synthetic test caveat:** URL_SHAPE `ca_file:0`, because the test CA isn't the system bundle. That field is informational only.
- **Carrier tests:** Failed, timeout, busy, az-failure and digest-mismatch handling belongs to the carrier and is not tested here, because no carrier exists yet.
