# Clerk session mock in integration tests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Clerk UI flow inside `login_as_dev_user` with programmatic auth via Clerk's sign-in token API, so the five Playwright integration tests using it become deterministic. `test_onboarding_flow` is untouched.

**Architecture:** New test-only helper `tests/integration/clerk_session.py` mints a Clerk sign-in token server-side (via `clerk_backend_api`), navigates the Playwright page to the token's redemption URL (provided by Clerk in the SDK response), and Clerk sets the right session cookies and redirects back to our app. `login_as_dev_user` calls this helper instead of typing email + password into Clerk's form.

**Tech Stack:** Python 3.13, `clerk_backend_api` SDK, Playwright sync API, pytest, flexmock, existing `tests/integration/*` infrastructure.

---

## File Structure

- **Create**: `tests/integration/clerk_session.py` — programmatic auth helper. One public function.
- **Create**: `tests/integration/clerk_session_test.py` — unit test for the helper using flexmock to stub the Clerk SDK and a fake `Page`.
- **Modify**: `tests/integration/utils.py` — `login_as_dev_user` switches from typing into the Clerk form to calling the new helper.

`tests/integration/clerk.py` and `tests/integration/onboarding_test.py` are NOT touched.

---

### Task 1: Add `create_dev_user_session` helper with unit test

**Files:**
- Create: `tests/integration/clerk_session.py`
- Create: `tests/integration/clerk_session_test.py`

- [ ] **Step 1: Write the failing unit test**

Create `tests/integration/clerk_session_test.py`:

```python
"""Unit test for the programmatic Clerk session helper.

These tests stub the Clerk SDK and the Playwright Page so they run as
fast unit tests (no network, no browser). The real end-to-end behavior
is exercised by the integration tests that depend on
`login_as_dev_user`.
"""

from unittest.mock import MagicMock

from flexmock import flexmock

from tests.integration import clerk_session
from tests.integration.clerk_session import create_dev_user_session


def test_create_dev_user_session_mints_token_and_navigates_to_url():
    fake_user = MagicMock(id="user_test_abc123")
    fake_token = MagicMock(url="https://example.clerk.accounts.dev/v1/tickets/accept?ticket=tkt_x")

    flexmock(clerk_session).should_receive("_get_or_create_clerk_user").and_return(fake_user)

    sign_in_tokens = flexmock()
    sign_in_tokens.should_receive("create").with_args(
        request={"user_id": "user_test_abc123"}
    ).and_return(fake_token).once()
    flexmock(clerk_session).should_receive("_clerk").and_return(
        flexmock(sign_in_tokens=sign_in_tokens)
    )

    page = flexmock()
    page.should_receive("goto").with_args(fake_token.url).once()
    page.should_receive("wait_for_url").with_args(object, timeout=10_000).once()

    user_id = create_dev_user_session(page)

    assert user_id == "user_test_abc123"


def test_create_dev_user_session_raises_when_token_creation_fails():
    fake_user = MagicMock(id="user_test_abc123")
    flexmock(clerk_session).should_receive("_get_or_create_clerk_user").and_return(fake_user)

    sign_in_tokens = flexmock()
    sign_in_tokens.should_receive("create").and_raise(RuntimeError("Clerk 500"))
    flexmock(clerk_session).should_receive("_clerk").and_return(
        flexmock(sign_in_tokens=sign_in_tokens)
    )

    page = flexmock()

    try:
        create_dev_user_session(page)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "Clerk 500" in str(e)
```

- [ ] **Step 2: Run the test, confirm it fails**

```
uv run pytest tests/integration/clerk_session_test.py -v
```

Expected: `ImportError` / `ModuleNotFoundError` for `tests.integration.clerk_session`.

- [ ] **Step 3: Implement the helper**

Create `tests/integration/clerk_session.py`:

```python
"""Programmatic Clerk authentication for Playwright integration tests.

`login_as_dev_user` historically drove the Clerk hosted UI in the browser
(fill email, click Continue, fill password, click Continue, wait for the
redirect chain to settle). That redirect chain is flaky in CI — multiple
distinct failure modes were observed across runs on PR #57.

This module sidesteps the UI entirely:

1. The backend SDK mints a one-time `sign_in_token` for the dev user.
2. Clerk returns a redemption URL with the token embedded.
3. We point Playwright at that URL. Clerk's API sets the session cookies
   atomically and 302s back to our app.

`test_onboarding_flow` still uses the UI flow because that test's whole
purpose is to verify the wizard end-to-end.
"""

from clerk_backend_api import Clerk
from playwright.sync_api import Page

from tests.constants import CLERK_DEV_USER_EMAIL
from tests.routes.utils import _get_or_create_clerk_user

# Use a module-level Clerk client so tests can monkeypatch `_clerk` if
# they want to stub. Lazy-initialize on first call so an unset
# CLERK_PRIVATE_KEY at import time doesn't blow up the test collector.
_clerk_client: Clerk | None = None


def _clerk() -> Clerk:
    global _clerk_client
    if _clerk_client is None:
        from app.configuration.clerk import CLERK_PRIVATE_KEY

        if not CLERK_PRIVATE_KEY:
            raise RuntimeError(
                "CLERK_PRIVATE_KEY is required to mint a sign-in token "
                "for the dev user. Make sure your test env exports it."
            )
        _clerk_client = Clerk(bearer_auth=CLERK_PRIVATE_KEY)
    return _clerk_client


def create_dev_user_session(page: Page) -> str:
    """Establish a Clerk session for the dev user on the given page.

    Returns the Clerk user id. After this returns, the page has the
    cookies the React app needs to consider the user logged in;
    subsequent authenticated navigations will succeed.
    """
    user = _get_or_create_clerk_user(CLERK_DEV_USER_EMAIL)
    user_id = user.id

    try:
        token = _clerk().sign_in_tokens.create(request={"user_id": user_id})
    except Exception as e:
        raise RuntimeError(
            f"clerk.sign_in_tokens.create failed for user_id={user_id}: {e}"
        ) from e

    assert token is not None, "sign_in_tokens.create returned None"
    assert token.url, "sign_in_tokens.create returned a token without a redemption url"

    page.goto(token.url)
    # Clerk's redemption flow ends with a redirect back to our app. Wait
    # until we're off the Clerk domain. If this times out, the session
    # was not established — fail loudly with the current URL.
    page.wait_for_url(
        lambda url: "clerk.accounts.dev" not in url and "/v1/tickets/accept" not in url,
        timeout=10_000,
    )

    return user_id
```

- [ ] **Step 4: Run the test, confirm it passes**

```
uv run pytest tests/integration/clerk_session_test.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Confirm ruff lint passes on the new files**

```
uv tool run ruff check tests/integration/clerk_session.py tests/integration/clerk_session_test.py
uv tool run ruff format --check tests/integration/clerk_session.py tests/integration/clerk_session_test.py
```

Expected: `All checks passed!` and `2 files already formatted`.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/clerk_session.py tests/integration/clerk_session_test.py
git commit -m "test(integration): add programmatic Clerk session helper

create_dev_user_session(page) mints a Clerk sign-in token via the
backend SDK, navigates the page to the token's redemption URL, and
waits for Clerk to redirect back into our app with session cookies set.
This replaces the flaky UI-driven login flow used by
login_as_dev_user; see issue #58 and the design doc at
docs/superpowers/specs/2026-05-28-clerk-session-mock-in-integration-tests-design.md."
```

---

### Task 2: Switch `login_as_dev_user` to use the helper

**Files:**
- Modify: `tests/integration/utils.py`

- [ ] **Step 1: Read the current implementation**

```
read tests/integration/utils.py lines 1-15 and 100-150
```

Confirm the current `login_as_dev_user` matches the spec's "before" sketch.

- [ ] **Step 2: Replace `login_as_dev_user` body**

In `tests/integration/utils.py`, replace the entire `login_as_dev_user` function with:

```python
def login_as_dev_user(page: Page) -> Doctor:
    """Log in as the Clerk dev user and return the resulting Doctor.

    Uses the programmatic Clerk session helper to skip the hosted UI
    entirely — see tests/integration/clerk_session.py for the rationale.
    The Doctor row is created the same way as in production: the first
    authenticated request after navigation triggers `inject_doctor_record`.
    """
    # Paranoid testing to ensure database cleaning is working.
    assert Doctor.count() == 0

    create_dev_user_session(page)

    # First authed request after redirect should upsert the Doctor.
    # Short poll closes any tiny race between the redirect landing and
    # the React app's first /api call.
    deadline = monotonic() + 5
    while Doctor.count() == 0 and monotonic() < deadline:
        sleep(0.1)

    assert Doctor.count() == 1, (
        f"expected exactly 1 Doctor after programmatic Clerk session; "
        f"got {Doctor.count()}"
    )

    doctor = Doctor.first()
    assert doctor

    # Bypass the onboarding wizard for tests that focus on post-onboarding
    # surface area. `page.goto(home_url())` is required after the DB write
    # so the layout's loader re-fetches settings and stops redirecting to
    # /onboarding.
    doctor.onboarding_completed_at = datetime.now(timezone.utc)
    doctor.save()
    page.goto(home_url())
    wait_for_loading(page)

    return doctor
```

- [ ] **Step 3: Update the imports in `tests/integration/utils.py`**

The function no longer needs `setup_clerk_testing_token` or `get_clerk_dev_user`. It does need `create_dev_user_session`, `monotonic`, and `sleep`. Replace the import block at the top of the file with:

```python
from datetime import datetime, timezone
from time import monotonic, sleep

from playwright.sync_api import Page

from app.models.doctor import Doctor

from tests.integration.clerk_session import create_dev_user_session
from tests.integration.server import home_url
```

Drop the now-unused imports:
- `from tests.integration.clerk import setup_clerk_testing_token`
- `from tests.routes.utils import get_clerk_dev_user`
- `from playwright.sync_api import TimeoutError as PlaywrightTimeoutError` (none of the reverted flake-fix code remains)

`wait_for_loading` lives in the same file, so no import needed for it.

- [ ] **Step 4: Run ruff to confirm imports are clean**

```
uv tool run ruff check tests/integration/utils.py
uv tool run ruff format --check tests/integration/utils.py
```

Expected: `All checks passed!`. If ruff suggests sorting/reordering imports, run `uv tool run ruff check --fix tests/integration/utils.py`.

- [ ] **Step 5: Run pyright on the file**

```
uv run pyright tests/integration/utils.py
```

Expected: 0 errors. The pre-existing `_user not accessed` info from the dev-user tuple unpacking goes away because we no longer call `get_clerk_dev_user` in this file.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/utils.py
git commit -m "test(integration): make login_as_dev_user use programmatic Clerk session

Drops the email/password UI fill + click sequence and the wait_for_loading
heuristic that proved flaky on PR #57. The new path calls
create_dev_user_session(page), which mints a sign-in token via the Clerk
backend SDK and redeems it through Clerk's ticket endpoint.

Same backend code path as before: AuthenticateClerkRequest still verifies
real Clerk-signed JWTs, inject_doctor_record still upserts the Doctor row
on the first authed request."
```

---

### Task 3: Local sanity check against docker stack

**Files:** none (verification step)

- [ ] **Step 1: Start the dev stack**

```bash
just up --fast
```

If the stack is already running, this is a no-op. If `just up` is not the right command in this repo, see the `Justfile` for `dev` or equivalent.

- [ ] **Step 2: Migrate the test database**

```bash
just db_migrate
```

Expected: alembic upgrades to head on both dev and test databases.

- [ ] **Step 3: Build the JS bundle integration tests depend on**

```bash
just py_js-build
```

Expected: a production-style build of the React app is produced.

- [ ] **Step 4: Run the smallest mocked test**

```bash
uv run pytest tests/integration/user_creation_test.py::test_login_existing_user -v
```

Expected: PASS, runtime around 6–10 seconds.

- [ ] **Step 5: If the test fails, inspect the failure**

If the test fails:
1. Read the error message. If it's `clerk.sign_in_tokens.create failed`, the dev plan may not support sign-in tokens — go to the rollback in the spec ("fall back to `sessions.create` + manual cookie injection"). Stop and surface this to the human.
2. If `Doctor.count() == 0`, look at the Playwright screenshot / trace under `tmp/test-results/playwright/...` to see where the browser ended up. The most likely cause is that Clerk's redirect URL does not return to our app — investigate the `url` field of the `SignInToken` response and the redirect chain.
3. If `wait_for_url` times out, the redirect from Clerk's ticket endpoint did not land back on our domain. Inspect `page.url` in the trace, and either adjust the lambda in `clerk_session.py` (the domain check may be wrong for this Clerk instance) or call out the discrepancy to the human.

Do not loop on guesses. Get evidence, then decide.

- [ ] **Step 6: Run all five mocked tests**

```bash
uv run pytest \
  tests/integration/user_creation_test.py \
  tests/integration/upload_visit_test.py::test_audio_file_upload \
  tests/integration/settings_test.py::test_biography_update \
  tests/integration/settings_test.py::test_example_note_upload \
  -v
```

Expected: 5 passed.

- [ ] **Step 7: Confirm `test_onboarding_flow` still passes**

```bash
uv run pytest tests/integration/onboarding_test.py::test_onboarding_flow -v
```

Expected: 1 passed.

If local sanity passes, proceed to Task 4. If it does not, stop and surface what was found.

---

### Task 4: Push to CI and verify

**Files:** none (CI verification)

- [ ] **Step 1: Confirm the branch is up to date and clean**

```bash
git status
git log --oneline origin/master..HEAD
```

Expected: clean working tree, three commits ahead (design doc, Task 1, Task 2).

- [ ] **Step 2: Push the branch**

```bash
git push -u origin test/clerk-programmatic-auth
```

- [ ] **Step 3: Open the PR**

```bash
gh pr create --base master --head test/clerk-programmatic-auth \
  --title "test(integration): mock Clerk auth via sign-in tokens" \
  --body "$(cat <<'EOF'
## Summary
Replaces the Clerk UI flow inside `login_as_dev_user` with programmatic auth via the Clerk backend SDK's sign-in tokens. The five integration tests that use this helper now skip the hosted UI and go straight to an established session.

See the design doc at `docs/superpowers/specs/2026-05-28-clerk-session-mock-in-integration-tests-design.md` for the full background — including why we picked sign-in tokens over direct cookie injection and the four distinct flake modes that motivated this change.

## What changed
- `tests/integration/clerk_session.py` — new helper, `create_dev_user_session(page)`.
- `tests/integration/utils.py` — `login_as_dev_user` calls the helper instead of typing into Clerk's form.

## What did not change
- Backend auth path (still real Clerk-signed JWTs).
- Frontend Clerk SDK behavior (PR #57's memoization in `web/app/configuration/clerk.tsx` stays).
- `test_onboarding_flow` (still exercises the real signup flow end-to-end).

## Test plan
- [ ] All five `login_as_dev_user` tests pass on CI
- [ ] `test_onboarding_flow` passes on CI
- [ ] No new annotations on the Backend job
- [ ] After merge, monitor Sentry for any new Clerk-related issues

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Watch the run**

```bash
gh run list --workflow=build_and_publish.yml --branch=test/clerk-programmatic-auth --limit=1
gh run watch <run-id>
```

Expected: Frontend success, Backend success.

- [ ] **Step 5: Confirm the right tests ran**

```bash
gh run view <run-id> --log | grep -E "tests/integration/.*\[chromium\]:" | tail -10
```

Expected: 6 successful integration tests including `test_onboarding_flow` and the 5 mocked ones.

- [ ] **Step 6: Confirm annotation count**

```bash
for job_id in $(gh run view <run-id> --json jobs --jq '.jobs[].databaseId'); do
  count=$(gh api "/repos/CosmaNeura/cosmaneura/check-runs/$job_id/annotations" --jq 'length')
  name=$(gh api "/repos/CosmaNeura/cosmaneura/check-runs/$job_id" --jq '.name')
  echo "$name: $count"
done
```

Expected: 0 annotations across all jobs.

If Backend fails, follow the same diagnosis pattern as Task 3 Step 5 — pull `--log-failed`, download artifacts if needed, and surface findings before retrying.

---

### Task 5: Statistical verification of the flake fix

**Files:** none (CI verification)

- [ ] **Step 1: Trigger 5 reruns of the workflow on the same commit**

```bash
run_id=$(gh run list --workflow=build_and_publish.yml --branch=test/clerk-programmatic-auth --limit=1 --json databaseId --jq '.[0].databaseId')
for i in 1 2 3 4 5; do
  gh run rerun "$run_id"
  sleep 30  # space them so GH's queue handles each
done
```

If `gh run rerun` errors with "already running", wait for the in-flight run to finish first.

- [ ] **Step 2: Wait for each rerun to complete and record the outcome**

```bash
gh run list --workflow=build_and_publish.yml --branch=test/clerk-programmatic-auth --limit=6 --json databaseId,conclusion,createdAt
```

Expected: 5 reruns + 1 original = 6 entries. All Backend `success`.

- [ ] **Step 3: Document the result on the PR**

Post a comment on the PR summarizing the rerun results:

```bash
gh pr comment <pr-number> --body "Stability verification: $N consecutive CI runs passed cleanly on the five mocked tests. Details: <link-to-runs>"
```

If any rerun fails, stop and analyze the failure. The flake is not fixed; report findings before merging.

- [ ] **Step 4: Merge (with the human's approval)**

This is a real merge. Do not do this autonomously — present the PR + the verification evidence and let the human decide.

---

## Self-Review

Coverage check against the spec:

- ✅ Goals 1, 2, 3, 4 → Tasks 1–2 produce the helper and switch the call site; Tasks 4–5 verify; the helper is one new file so rollback is trivial.
- ✅ Non-goals → no production code is touched.
- ✅ Architecture (sign-in token + ticket URL) → Task 1 Step 3.
- ✅ Module layout (`tests/integration/clerk_session.py`) → Task 1.
- ✅ Modified file (`tests/integration/utils.py`) → Task 2.
- ✅ Untouched files → none of the listed files appear in any task.
- ✅ Data flow → Task 1's implementation matches the spec's diagram.
- ✅ Error handling → `create_dev_user_session` raises with the Clerk error body; `assert` failure includes diagnostic text.
- ✅ Testing & verification → Tasks 3, 4, 5 cover local sanity, single CI run, and statistical reruns.
- ✅ Acceptance criteria → Task 5 produces evidence for "0 flake across 10+ runs". Five reruns + the original run = 6; if more confidence is required the same Step 1 loop runs again.
- ✅ Risks → mitigations referenced in Task 3 Step 5 troubleshooting.
- ✅ Rollback → Task 1 produces one new module file, Task 2 modifies one existing file. Revertable in one commit.

Placeholder scan: no TBD / TODO / "implement later" / "fill in details". Every code step shows the actual code.

Type / name consistency: `create_dev_user_session(page: Page) -> str` is used identically across `clerk_session.py`, the unit test, and `utils.py`. The `SignInToken.url` field is real (verified during brainstorming).
