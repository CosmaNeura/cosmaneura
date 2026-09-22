# Clerk test cleanup boundary and future instance isolation

**Date:** 2026-06-09 · **Updated:** 2026-08-18 · **Status:** Destructive cleanup fixed; physical isolation remains recommended · **Owner:** TBD

## The historical problem

Signing in to **staging** repeatedly produces a duplicate `doctor` row with a **NULL
email**, and it happens **right after a deploy**.

## Historical root cause

The **test suite and the staging runtime share the same Clerk *development* instance**
(`resolved-emu-53.clerk.accounts.dev`, key `sk_test_…`, stored in 1Password item
`clerk-staging-api`).

- On startup, the test suite ran `delete_all_clerk_users()` (`tests/utils.py`, called
  from `tests/conftest.py`), which deleted **every Clerk user except a small allowlist**.
  It existed because Clerk dev instances cap at ~100 users.
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

## Superseded interim fix (2026-06-09)

Added the team's real staging accounts (`dev@cosmaneura.com`, `ortegaoscar14@gmail.com`)
to `CLERK_ALL_USERS_TO_PRESERVE` (`tests/constants.py`, commit `5eecdff`) so the cleanup
stops deleting them. Test-only change; no production runtime impact. This keeps the
team's sign-ins stable but does NOT protect any other staging user's account. The
allowlist and its regression test were removed by the positive-ownership fix below.

## Code safety boundary implemented (2026-08-18)

The suite now calls `delete_stale_clerk_test_users()`. Cleanup eligibility is positive,
not inverse: a Clerk user is deletable only when all of these conditions hold:

- the process is in the test environment;
- the Clerk key starts with `sk_test_` (a live key is rejected before listing users);
- the user has a Clerk-shaped `user_…` id;
- the user has exactly one email address; and
- that address exactly matches the ephemeral integration helper's reserved namespace,
  `test-<19-digit time_ns>+clerk_test@example.com`, and is at least one hour old.

The entire candidate set is selected before the first delete. Missing, malformed,
multi-email, seed, pilot, and future unrecognized accounts fail closed. John, Greg, and
future pilot users therefore require no source-code allowlist entry. The query requests
the complete development-instance cohort (limit 500, above Clerk's ~100-user development
cap), so stale UI-test signups are still reclaimed. The Backend job has a 20-minute hard
timeout; the one-hour age gate prevents overlapping PR workflows from deleting each
other's active test users. Pilot accounts must never use the reserved `+clerk_test`
namespace.

## Remaining defense in depth: give the test suite its own Clerk instance

Stop the test suite from ever operating on the instance staging runtime uses.

### Steps

1. **Create a new Clerk development instance** dedicated to automated tests, in the Clerk
   dashboard (separate from `resolved-emu-53`, which stays as the staging-runtime instance).
2. **Store its keys in 1Password** — a new Staging-vault item, e.g. `clerk-test-ci`, with
   `publishable_key` + `secret_key` (mirror the shape of `clerk-staging-api`).
3. **Point CI/tests at the new instance.** Add repository-level GitHub Actions secrets
   `CLERK_TEST_PRIVATE_KEY` and `CLERK_TEST_PUBLISHABLE_KEY`, then use them for the
   workflow-level `CLERK_PRIVATE_KEY`, `VITE_CLERK_PUBLIC_KEY`, and
   `CLERK_PUBLISHABLE_KEY` values consumed by the Backend/Frontend test jobs in
   `.github/workflows/build_and_publish_manual.yml`. Keep the deploy jobs' job-level,
   environment-scoped `CLERK_PRIVATE_KEY` and publishable-key overrides unchanged, so
   staging and production continue to receive their own runtime instances. The composite
   `.github/actions/common-setup` action does not export Clerk credentials from direnv.
   Locally, `.env.shared` currently supplies Clerk values; add a later `.env.test`
   override backed by the dedicated `clerk-test-ci` 1Password item.
4. **Recreate the test seed users** in the new instance (the `*+clerk_test@example.com`
   accounts in `tests/constants.py`) so the live-Clerk tests (`get_valid_token` and
   `tests/integration/*`) operate entirely within it.
5. **Update the hardcoded test JWT issuer** if needed — the mock token in
   `tests/routes/utils.py` (`MockAuthenticateRequest`) pins
   `iss = https://resolved-emu-53.clerk.accounts.dev`; point it at the new instance's
   issuer.
6. **Verify**: deploy staging → sign in → deploy again → confirm the account persists with
   its email and no duplicate doctor appears.
7. **Keep the positive-ownership cleanup boundary.** Physical isolation is an additional
   control, not a reason to restore delete-everyone-except logic.

### Effort & risk

- **Effort:** small–medium. Mostly Clerk dashboard + a 1Password secret + CI env wiring;
  little to no application code.
- **Risk:** low and test-scoped. Production is untouched (separate `live` instance).
- **Keep** the test-environment guard, `sk_test_` guard, and positive ownership predicate
  even after the tests move to a throwaway instance.

## Alternative (not recommended here)

Configure the staging Clerk instance to include an `email` claim in the session token and
read it in `inject_doctor_record` (prefer the claim over `clerk.users.get`). This makes
the app independent of the Clerk user surviving. It works, but it changes the **production
auth path** for a staging-only problem, so it was deliberately not pursued.
