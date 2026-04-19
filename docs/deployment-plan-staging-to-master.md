# Staging → Master Deployment Plan

**Date drafted:** 2026-04-17
**Author:** Oscar Ortega
**Source branch:** `staging`
**Target branch:** `master`
**Scope:** ~50 commits / 182 files changed / ~13K lines added

---

## 1. What's in this release

### Product changes
- **Doctor onboarding wizard** (`/onboarding`): Welcome → Biography → Example Note → Zoom (Coming Soon) → All Set.
- **Zoom integration**: per-doctor OAuth (Authorization Code Grant), webhook handlers for `meeting.started` / `meeting.ended`, token refresh, auto-join via Recall. Currently **gated** behind the `ZOOM_COMING_SOON` flag in the frontend — backend is live but the user-facing step just shows "Coming Soon".
- **Brand refresh**: new logos (`cosmas-icon-*.png`), updated color palette, redesigned sidebar, new `AppFooter`, `BrandLogo`, `legal-components`.
- **Legal / support pages**: `/privacy-policy`, `/terms-of-service`, `/support` (each with a static HTML fallback in `web/public/`).
- **Schedule views**: `/schedule` (list) and `/schedule/:id` (detail).
- **Stepper UX**: shared `StepIndicator` component used in onboarding.

### Infra / CI
- New `infra-staging/` Terraform stack (standalone from `infra/`).
- New GitHub Actions job `deploy-staging` triggered on push to `staging`.
- `nixpacks.toml` now installs `poppler-utils` (required by `pdf2image`).
- Workflow env: `VITE_CLERK_PUBLIC_KEY`, `VITE_POSTHOG_KEY`, `VITE_POSTHOG_HOST`, `CLERK_PRIVATE_KEY` wired from GitHub secrets.

### 1Password vault reorganization
- `OP_VAULT_UID` split into:
  - `OP_PROD_VAULT_UID`
  - `OP_STAGING_VAULT_UID`
  - `OP_SHARED_VAULT_UID`
  - `OP_DEV_VAULT_UID`
- All `.env.*` files now resolve secret paths against the appropriate vault.

---

## 2. Database migrations

Eight new Alembic migrations, applied in this order:

| # | Revision | Description | Risk |
|---|---|---|---|
| 1 | `7d9faac96b6d` | Add unique constraint on schema name per doctor | Low (additive) |
| 2 | `262b426e281a` | Add zoom OAuth fields to doctor | Low (additive) |
| 3 | `1054f3c5c104` | Add `zoom_meeting` table | Low (additive) |
| 4 | `3766645b25a6` | Add `doctor_video_integration` table | Low (additive) |
| 5 | `b2b524604b33` | **Remove** zoom OAuth token columns | ⚠ Deletes all `doctor_video_integration` rows where `provider='zoom'` |
| 6 | `f1e2d3c4b5a6` | **Restore** zoom OAuth token columns (redesigned) | ⚠ Also deletes any remaining zoom rows |
| 7 | `28d416bf0315` | Add `onboarding_completed_at` to `doctor` | ⚠ No backfill — existing doctors default to NULL |
| 8 | `a1b2c3d4e5f6` | Add column comments + timezone to `doctor_video_integration` | Low (metadata only) |

### Migration risk notes

- **Migrations 5 & 6** delete Zoom integration rows by design (we rewrote the token model). Safe in prod **only if no Zoom rows exist today**. To be verified in pre-flight.
- **Migration 7** leaves every existing doctor with `onboarding_completed_at = NULL`. Because the authenticated layout redirects any NULL doctor to `/onboarding`, **every existing doctor will hit the wizard on their next login**. The wizard auto-detects existing bio + notes and drops them on the "All Set" step — so it's one extra click, not data loss. We will **add a backfill** (see §4) to eliminate this friction entirely.
- No destructive changes to patient, note, intake, or transcript data.

---

## 3. New secrets required in production 1Password

Must be present in the prod vault (`OP_PROD_VAULT_UID`) **before deploy**:

| Secret | Purpose |
|---|---|
| `ENCRYPTION_KEY` (Fernet) | Encrypts OAuth tokens at rest. **Blocker**: backend cannot read/write integration tokens without this. |
| `ZOOM_CLIENT_ID` | Zoom Marketplace app — client ID |
| `ZOOM_CLIENT_SECRET` | Zoom Marketplace app — client secret |
| `ZOOM_SECRET_TOKEN` | Zoom webhook signature secret |
| `RECALL_API_KEY` | Recall.ai credential for meeting bot |
| `ZOOM_REDIRECT_URI` | Prod domain callback URL (registered with Zoom Marketplace) |
| `ZOOM_SUCCESS_REDIRECT` | Prod post-auth redirect target |
| `COOKIE_DOMAIN` | Prod domain for session cookie scoping |

Already wired but worth double-checking the new paths resolve in prod: `CLERK_PRIVATE_KEY`, `VITE_CLERK_PUBLIC_KEY`, `SENTRY_DSN`, `VITE_SENTRY_DSN`, `VITE_POSTHOG_KEY`, `VITE_POSTHOG_HOST`, `ASSEMBLY_API_KEY`, `AZURE_OPENAI_*`, `AZURE_UPLOADS_*`, `AZURE_DEID_*`, `DATABASE_URL`, `REDIS_URL`.

---

## 4. Pre-flight checklist (before merging `staging` → `master`)

- [ ] Confirm all secrets in §3 exist in the prod 1Password vault at the paths referenced by `.env.production.backend` and `.env.production.frontend`.
- [ ] Run `SELECT COUNT(*) FROM doctor_video_integration WHERE provider = 'zoom';` in prod. Expected: `0`. If non-zero, pause and review.
- [ ] Add backfill for migration `28d416bf0315`:
  ```sql
  UPDATE doctor
  SET onboarding_completed_at = NOW()
  WHERE onboarding_completed_at IS NULL;
  ```
  Bundle this either inside the migration's `upgrade()` or as a follow-up one-shot. Existing doctors already have bio and note schemas, so marking them complete is safe.
- [ ] Register the production Zoom redirect URI in Zoom Marketplace (needed only when flipping the `ZOOM_COMING_SOON` flag — not blocking for this release since Zoom stays gated).
- [ ] Smoke-test on staging end-to-end:
  - [ ] New provider signup → wizard completes → reaches `/notes`.
  - [ ] Existing provider login → lands directly on `/notes` → sees new branding, existing patients, existing note types.
  - [ ] Intake flow → generate questions → submit → summarize.
  - [ ] Upload recording → transcript → note generation.
  - [ ] Schedule list and detail render.
  - [ ] Sentry and PostHog events visible in staging projects.

---

## 5. Deploy steps

### Phase A — Database
1. CI runs `alembic upgrade head` against prod DB as part of the deploy job (already wired).
2. Watch the migration output for errors; all eight should apply cleanly.

### Phase B — Application
1. Merge `staging` → `master` via PR.
2. CI builds web + api images, pushes to ACR, `terraform apply` from `infra/`, container apps roll out.
3. Monitor the deploy job until both containers are healthy.

### Phase C — Post-deploy verification
- [ ] Hit `/settings`, `/schedule`, `/notes`, `/intake` — no 500s, correct branding.
- [ ] Sentry receiving events from prod backend + frontend.
- [ ] PostHog receiving events from prod frontend.
- [ ] Clerk session works on prod domain.
- [ ] Sign in as one existing provider — verify no forced wizard, data intact.
- [ ] Sign in as a fresh test account — verify wizard flows through to `/notes`, Zoom step shows "Coming Soon".

### Phase D — Gated Zoom rollout (later, separate release)
1. Provision Zoom Marketplace app for prod (redirect URI, scopes).
2. Confirm `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`, `ZOOM_SECRET_TOKEN`, `RECALL_API_KEY` in prod vault.
3. Flip `ZOOM_COMING_SOON = false` in `web/app/routes/onboarding.tsx` (and any other gated call sites).
4. Deploy.
5. Validate OAuth handshake end-to-end with one real provider before announcing.

---

## 6. Rollback plan

### Application rollback
- Redeploy the previous master image from ACR. No schema rollback required for app-only issues (all new migrations are additive at the column level, with the Zoom delete being the only destructive step — see below).

### Database rollback
- `alembic downgrade <target>` runs the reverse migrations.
- **Not fully reversible**: migrations `b2b524604b33` and `f1e2d3c4b5a6` delete Zoom token rows. Downgrade restores the columns but **not the data**. Since prod has no Zoom rows today, this is a non-issue.

### Feature kill switches
- Zoom: `ZOOM_COMING_SOON = true` already hides the UX; if issues arise we do not need to deploy to disable it — it ships disabled.
- Onboarding: if the wizard regresses, the backfill from §4 ensures no existing user is forced through it; new signups could temporarily be paused at Clerk if needed.

---

## 7. Risk summary

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Missing `ENCRYPTION_KEY` in prod vault | Medium | Backend startup fails or token ops crash | Pre-flight check (§4) |
| Prod has stale Zoom rows wiped by migrations 5/6 | Low | Lose any existing Zoom link data | Pre-flight SELECT COUNT(*) (§4) |
| Existing doctors see wizard on next login | High (without backfill) | One extra click, mild confusion | Add backfill (§4) |
| Zoom OAuth misconfig in prod Marketplace app | N/A this release | None — gated behind flag | Addressed in Phase D |
| Terraform drift between `infra/` and new `infra-staging/` | Low | Staging-only | Staging stack is isolated |
| Clerk / PostHog / Sentry keys pointing to wrong project | Medium | Missing telemetry | Post-deploy verification (§5 Phase C) |

**Overall risk: Low.** No destructive changes to patient-facing data. The two migration deletes and the onboarding backfill are the only moving parts that need manual attention, and both are addressed in the pre-flight checklist.

---

## 8. Follow-ups after this release

- **SOAP note template seed for new providers**: pre-load a default `DoctorNoteSchema` on doctor creation so step 2 of the wizard (upload example note) becomes optional. Half-day of work; ships behind the same pattern as the Zoom flag.
- **Zoom GA**: Phase D above, scheduled once the Zoom Marketplace app is approved and you've reviewed functionality.
- **Consolidate `infra/` and `infra-staging/`**: consider a single stack with workspace-per-environment to avoid drift.
