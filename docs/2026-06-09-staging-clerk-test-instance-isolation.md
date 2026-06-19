# Future work: isolate the test suite's Clerk instance from staging runtime

**Date:** 2026-06-09 · **Status:** Not urgent — interim fix already in place · **Owner:** TBD

## The problem

Signing in to **staging** repeatedly produces a duplicate `doctor` row with a **NULL
email**, and it happens **right after a deploy**.

## Root cause

The **test suite and the staging runtime share the same Clerk *development* instance**
(`resolved-emu-53.clerk.accounts.dev`, key `sk_test_…`, stored in 1Password item
`clerk-staging-api`).

- On startup, the test suite runs `delete_all_clerk_users()`
  (`tests/utils.py`, called from `tests/conftest.py`), which deletes **every Clerk user
  except a small allowlist** (`CLERK_ALL_USERS_TO_PRESERVE`). It exists because Clerk dev
  instances cap at ~100 users.
- The **CI Backend job runs `just py_test` on every deploy**, so every deploy wipes the
  staging Clerk users.
- After the wipe, the app can no longer fetch a user's email
  (`clerk.users.get(sub)` → `resource_not_found` in
  `app/routes/dependencies/doctor.py::_fetch_clerk_email`), so the next sign-in creates a
  doctor with a NULL email.

**Production is never affected**: it uses a separate Clerk `live` instance that the test
suite never touches (the cleanup is guarded by `if not is_testing(): raise`).

It only touches Clerk **users**, NOT any database — the staging Postgres data is never
wiped, and the test suite uses its own `test` database.

## Interim fix already shipped (2026-06-09)

Added the team's real staging accounts (`dev@cosmaneura.com`, `ortegaoscar14@gmail.com`)
to `CLERK_ALL_USERS_TO_PRESERVE` (`tests/constants.py`, commit `5eecdff`) so the cleanup
stops deleting them. Test-only change; no production runtime impact. This keeps the
team's sign-ins stable but does NOT protect any other staging user's account.

## The real fix (this document): give the test suite its own Clerk instance

Stop the test suite from ever operating on the instance staging runtime uses.

### Steps

1. **Create a new Clerk development instance** dedicated to automated tests, in the Clerk
   dashboard (separate from `resolved-emu-53`, which stays as the staging-runtime instance).
2. **Store its keys in 1Password** — a new Staging-vault item, e.g. `clerk-test-ci`, with
   `publishable_key` + `secret_key` (mirror the shape of `clerk-staging-api`).
3. **Point CI/tests at the new instance.** Inject `CLERK_PRIVATE_KEY` (and any publishable
   key the tests use) for the **test environment** from `clerk-test-ci`, not
   `clerk-staging-api`. Wire this in `.env.test` / the CI env injection
   (`.github/actions/common-setup`). Leave `.env.staging.backend` pointed at
   `clerk-staging-api` unchanged — staging runtime keeps its own instance.
4. **Recreate the test seed users** in the new instance (the `*+clerk_test@example.com`
   accounts in `tests/constants.py`) so the live-Clerk tests (`get_valid_token`,
   `tests/integration/*`) and `delete_all_clerk_users` operate entirely within it.
5. **Update the hardcoded test JWT issuer** if needed — the mock token in
   `tests/routes/utils.py` (`MockAuthenticateRequest`) pins
   `iss = https://resolved-emu-53.clerk.accounts.dev`; point it at the new instance's
   issuer.
6. **Verify**: deploy staging → sign in → deploy again → confirm the account persists with
   its email and no duplicate doctor appears.
7. **Clean up the band-aid** (optional): once isolated, the team emails added to
   `CLERK_ALL_USERS_TO_PRESERVE` can be removed, since the cleanup no longer touches the
   staging-runtime instance.

### Effort & risk

- **Effort:** small–medium. Mostly Clerk dashboard + a 1Password secret + CI env wiring;
  little to no application code.
- **Risk:** low and test-scoped. Production is untouched (separate `live` instance).
- **Keep** the `is_testing()` guard in `delete_all_clerk_users` and the ~100-user cleanup
  itself — the goal is to point it at a throwaway instance, not to remove it.

## Alternative (not recommended here)

Configure the staging Clerk instance to include an `email` claim in the session token and
read it in `inject_doctor_record` (prefer the claim over `clerk.users.get`). This makes
the app independent of the Clerk user surviving. It works, but it changes the **production
auth path** for a staging-only problem, so it was deliberately not pursued.
