# Clerk session mock in integration tests

## Summary

Replace the Clerk UI flow in `login_as_dev_user` with programmatic session creation
via the Clerk backend SDK, eliminating the test flakiness documented in issue #58.
`test_onboarding_flow` keeps the real UI flow intact, preserving end-to-end signup
coverage. Five Playwright integration tests gain deterministic auth.

## Background

PR #57 documented and reverted three failed attempts at fixing flakiness in the
five Playwright tests that call `login_as_dev_user` (`tests/integration/utils.py`).
Across four CI runs we observed four distinct failure modes, all rooted in the
same chain:

  Browser → Clerk hosted UI → Clerk API → redirect → React Router hydration →
  first authed backend request → `inject_doctor_record` → Doctor row in DB

Any link in this chain can stall in CI. The traces showed cases where
`attempt_first_factor` returned 200 but the Clerk React SDK never fired the
post-auth redirect; cases where the React Router `HydrateFallback` hung
indefinitely; and cases where the browser stayed on the Clerk factor-one form
without progressing. Each fix attempt addressed one symptom but the next run
exposed the flakiness elsewhere — the architectural pattern that the
`systematic-debugging` skill flags as a wrong-architecture signal.

The Clerk Python SDK (`clerk_backend_api`) exposes `sign_in_tokens.create`,
`sessions.create`, `sessions.create_token`, and `users.create` — enough to mint
real sessions server-side and inject them into the test browser, skipping every
fragile link above the backend auth layer.

## Goals

1. The five tests that call `login_as_dev_user` pass deterministically across
   10+ consecutive CI runs.
2. `test_onboarding_flow` continues to exercise the real Clerk UI signup flow
   without regression.
3. No production code changes. The backend auth path
   (`AuthenticateClerkRequest`, `inject_doctor_record`) remains exactly as
   today; we only change how the test browser obtains a Clerk session.
4. The mocking helper is contained in a new test-only module that is trivial to
   delete if we ever roll back.

## Non-goals

- Reducing Clerk API usage in production. The N+1 collapse from PR #57
  (commit `4c5398f` memoizing `getClient()`) already addressed that and remains
  intact.
- Replacing `setup_clerk_testing_token`. It is still used by
  `test_onboarding_flow` to bypass Clerk's bot protection during the real UI
  flow.
- Mocking the backend's JWT verification. We use real, Clerk-signed JWTs so
  that backend behavior in tests matches production exactly.

## Approach

Programmatic auth via Clerk's sign-in token + ticket-redemption endpoint. The
backend SDK mints a single-use sign-in token tied to the dev user's Clerk ID;
Playwright navigates the browser to Clerk's `/v1/tickets/accept` URL, Clerk
sets all required session cookies atomically and 302s back to our app. From
there, the first authenticated request triggers `inject_doctor_record` exactly
as in production.

We picked sign-in tokens over direct cookie injection because cookie names and
expected state across the Clerk frontend SDK (`__session`, `__client_uat`,
`__dev_browser_jwt`, and others) are SDK-internal and subject to change between
Clerk releases. Going through Clerk's documented ticket endpoint outsources
that coupling to Clerk itself.

## Architecture

### New module: `tests/integration/clerk_session.py`

Single responsibility: mint and apply a Clerk session for the dev user in a
Playwright `Page`. One public function:

```python
def create_dev_user_session(page: Page) -> str:
    """Establish a Clerk session for the dev user on the given page.

    Returns the Clerk user id. After this returns, the page has the
    cookies required for the React app to consider the user logged in;
    subsequent navigations to authenticated routes will succeed.
    """
```

Internal steps:

1. Resolve the dev user's Clerk user id. Look it up by the email returned from
   the existing `get_clerk_dev_user()` helper. If the user does not exist in
   Clerk yet (fresh dev instance), create it via `clerk.users.create` so the
   helper is idempotent across environments.
2. Mint a sign-in token: `clerk.sign_in_tokens.create(user_id=…)`.
3. Resolve the Clerk frontend API URL (already done by
   `parse_publishable_key` in `tests/integration/clerk.py` — reuse).
4. Navigate the browser to
   `https://<frontend_api>/v1/tickets/accept?ticket=<token>`. Clerk handles
   the redirect chain and lands the browser at our app's home URL with
   session cookies set on the appropriate domain.
5. Wait for the URL to leave the Clerk domain (short timeout).

### Modified: `tests/integration/utils.py::login_as_dev_user`

Drop the Clerk UI fill/click sequence and the `wait_for_loading` heuristic.
Replace with a call to `create_dev_user_session`, then a short DB poll for the
`Doctor` row (the first authenticated frontend call triggers
`inject_doctor_record`). The onboarding-bypass tail of the helper
(`doctor.onboarding_completed_at = …`) stays unchanged.

### Untouched

- `app/routes/dependencies/clerk.py`, `app/routes/dependencies/doctor.py`: zero
  changes. The backend continues to verify Clerk-signed JWTs against the JWKS
  endpoint exactly as in production.
- `web/app/configuration/clerk.tsx`: zero changes. The PR #57 memoization
  (commit `4c5398f`) remains intact.
- `tests/integration/clerk.py`: zero changes. `setup_clerk_testing_token`,
  `clerk_login_and_verify`, and `clerk_test_email` remain in place for
  `test_onboarding_flow`.
- `tests/integration/onboarding_test.py`: zero changes. The wizard test keeps
  exercising the real UI flow as before.

## Data flow

```
test setup
  └─ create_dev_user_session(page)
        ├─ Clerk API: users.list(email=dev_user_email)   # idempotent
        │     └─ (if 404) users.create(email, password)
        ├─ Clerk API: sign_in_tokens.create(user_id)
        │     └─ returns token
        └─ page.goto(<frontend_api>/v1/tickets/accept?ticket=…)
              └─ Clerk sets cookies + 302 to home_url

test body
  └─ first authed request
        └─ AuthenticateClerkRequest verifies JWT
            └─ inject_doctor_record upserts Doctor
                └─ Doctor.count() == 1 (poll satisfies)
```

## Error handling

The helper must fail loudly with a clear message on Clerk SDK errors, not
silently. Specifically:

- `users.list` returning empty AND `users.create` raising → raise with the
  Clerk HTTP error body included in the message.
- `sign_in_tokens.create` raising → same, surface the HTTP status and body.
- `page.goto` to the ticket-accept URL ending on a non-app URL (still on Clerk
  after the redirect timeout) → raise with the current URL and the test
  browser's last response status.

This prevents the helper from masquerading as the previous flake symptom
("got 0 doctors"). Errors point to their actual source.

## Testing & verification

1. Local sanity: run `tests/integration/user_creation_test.py::test_login_existing_user`
   against the dev docker stack. This is the smallest test that exercises
   `login_as_dev_user`; if it passes locally we have signal before committing.
2. CI run on a fresh branch (`test/clerk-programmatic-auth`) off master once
   PR #57 is merged.
3. Confirm all five `login_as_dev_user`-using tests pass:
   - `tests/integration/user_creation_test.py::test_signup`
   - `tests/integration/user_creation_test.py::test_login_existing_user`
   - `tests/integration/upload_visit_test.py::test_audio_file_upload`
   - `tests/integration/settings_test.py::test_biography_update`
   - `tests/integration/settings_test.py::test_example_note_upload`
4. Confirm `tests/integration/onboarding_test.py::test_onboarding_flow` still
   passes (real Clerk UI flow).
5. Rerun the workflow 5+ times consecutively to validate the flake is gone.

## Acceptance criteria

- 0 flake across 10+ consecutive CI runs on the five mocked tests.
- No regression on `test_onboarding_flow`.
- No new Clerk-related Sentry issues from the test environment.
- The new module is removable as a single file delete if we ever roll back.

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `sign_in_tokens.create` requires a Clerk plan tier we don't have | Low | Fall back to `sessions.create` + manual cookie injection. Less clean but technically equivalent. |
| Clerk's ticket-accept URL or query-param contract differs from what we expect | Medium | Discover during implementation by inspecting a real ticket-accept response. Adjust URL/params accordingly. |
| Frontend Clerk SDK doesn't honor cookies set by the ticket flow when arriving cold | Low | The whole point of going through Clerk's API is that it sets cookies in the form the SDK expects. If this still fails, we know cookie injection is also off the table and we go to plan B (test-only backend bypass) with a security review. |
| Dev user email mismatch between environments | Low | Resolve via `users.list(email=…)` rather than hardcoding a Clerk user id; create on miss. |

## Rollback

The change is contained to two test files. Reverting is a single revert commit:

```
git revert <commit>     # drops the new helper and restores UI-based login_as_dev_user
```

Zero production impact. Zero data migration. Zero coordination required.

## Open questions

None at design time. Implementation details (exact URL, cookie names if we
fall back) are discovered during plan execution; the architecture above is
robust to those details.
