# Cache-Control no-store Middleware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a FastAPI middleware that sets `Cache-Control: no-store, ...`, `Pragma: no-cache`, and `Expires: 0` on every HTTP response from the API app, except responses for paths under `/assets/`. This fixes the Zoom Marketplace review FAIL "Cacheable HTTPS Response".

**Architecture:** A single new middleware function in `app/routes/middleware/__init__.py`, registered as the **last** call inside `add_middleware()` so it becomes the outermost wrapper and its headers can't be overwritten. The function calls `await call_next(request)` and then unconditionally adds the cache-defeating headers, with one early-return path for requests whose URL path starts with `/assets/` (Vite-hashed frontend assets).

**Tech Stack:** Python 3, FastAPI, Starlette `BaseHTTPMiddleware`, pytest, `httpx.AsyncClient` + `fastapi.testclient.TestClient`.

**Spec:** `docs/superpowers/specs/2026-05-14-zoom-cacheable-response-fix-design.md`

---

## File Structure

- **Modify:** `app/routes/middleware/__init__.py` — add new dispatch function `_no_store_dispatch` at module level and register it via `app.middleware("http")(_no_store_dispatch)` as the LAST line inside `add_middleware()`.
- **Create:** `tests/routes/cache_control_middleware_test.py` — all tests for the new behavior. New file (no existing middleware test file in the codebase).

Both files have one clear responsibility. The middleware function is small enough to live alongside `add_middleware()`; no new module needed.

---

## Pre-flight: confirm starting state

- [ ] **Step P.1: Verify clean baseline**

Run from repo root:
```bash
git status
```

Expected: the working tree may have unrelated in-progress changes from the current branch (`feat/zoom-merge-window`). That's fine. Just confirm `app/routes/middleware/__init__.py` and `tests/routes/cache_control_middleware_test.py` are NOT in the modified/untracked list. If they are, stop and ask the user.

- [ ] **Step P.2: Verify the test suite runs before we touch anything**

Run:
```bash
uv run pytest tests/routes/unauthenticated_test.py -v
```

Expected: both `test_healthcheck` and `test_user_status` collected; `test_healthcheck` passes. (`test_user_status` may fail or pass depending on DB seed state — that's fine; we just need to confirm pytest works.)

If pytest itself errors out (import errors, missing fixtures), stop and ask the user before continuing.

---

## Task 1: TDD the core middleware behavior on `/healthcheck`

**Files:**
- Create: `tests/routes/cache_control_middleware_test.py`
- Modify: `app/routes/middleware/__init__.py`

- [ ] **Step 1.1: Write the failing test**

Create `tests/routes/cache_control_middleware_test.py` with:

```python
"""Tests for the no-store Cache-Control middleware.

The Zoom Marketplace review flagged that responses with sensitive data
(Meeting Link, Meeting ID) were cacheable. This middleware ensures every
API response carries headers that forbid browser/proxy caching.
"""

from fastapi import status

from app.server import api_app


async def test_healthcheck_has_no_store_cache_headers(aclient):
    """Public healthcheck endpoint must include cache-defeating headers."""
    response = await aclient.get(api_app.url_path_for("healthcheck"))

    assert response.status_code == status.HTTP_200_OK
    assert "no-store" in response.headers["Cache-Control"]
    assert "no-cache" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"
```

- [ ] **Step 1.2: Run the test, confirm it fails for the right reason**

Run:
```bash
uv run pytest tests/routes/cache_control_middleware_test.py::test_healthcheck_has_no_store_cache_headers -v
```

Expected: FAIL with `KeyError: 'cache-control'` (or `KeyError: 'Cache-Control'`), because the middleware hasn't been added yet and no current endpoint sets these headers.

If the failure is anything other than a missing-header `KeyError` (e.g., import error, 500 response), stop and fix before continuing.

- [ ] **Step 1.3: Implement the middleware (without the `/assets/` exemption yet — TDD-style minimal first cut)**

Open `app/routes/middleware/__init__.py`. At the top, the imports currently are:

```python
from decouple import config
from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from structlog_config import fastapi_access_logger
```

Add `Request` to the FastAPI import line:

```python
from fastapi import FastAPI, Request
```

Then, immediately below the `COOKIE_DOMAIN = ...` line and above the `def allowed_hosts(...)` function, add this module-level function:

```python
async def _no_store_dispatch(request: Request, call_next):
    """Defeat HTTP caching on every API response.

    Required by Zoom Marketplace review: sensitive fields like join_url
    and zoom_meeting_id must never be cached by browsers or proxies.
    """
    response = await call_next(request)
    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0, private"
    )
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
```

Then, at the very end of `add_middleware(app)` — after the `PdbMiddleware` block but **before** `return app` — register the middleware:

```python
    # MUST be the LAST registration: Starlette inserts at position 0 of the
    # middleware list, so the last-registered becomes the outermost wrapper.
    # That means our headers are the final word on the response, and no other
    # middleware can overwrite them.
    app.middleware("http")(_no_store_dispatch)

    return app
```

The final shape of the bottom of `add_middleware()` should look like:

```python
    if is_development() and config("FASTAPI_DEBUG", cast=bool, default=False):
        from app.utils.debug import PdbMiddleware

        # TODO should conditionally include this dependending on set_trace, and fix the stdin/stdout
        # app.add_middleware(PdbrMiddleware, debug=True)

        app.add_middleware(PdbMiddleware, debug=True)

    app.middleware("http")(_no_store_dispatch)

    return app
```

- [ ] **Step 1.4: Run the test, confirm it passes**

Run:
```bash
uv run pytest tests/routes/cache_control_middleware_test.py::test_healthcheck_has_no_store_cache_headers -v
```

Expected: PASS.

- [ ] **Step 1.5: Run the broader unauthenticated test file to confirm we didn't break anything**

Run:
```bash
uv run pytest tests/routes/unauthenticated_test.py tests/routes/cache_control_middleware_test.py -v
```

Expected: `test_healthcheck` and `test_healthcheck_has_no_store_cache_headers` both PASS. (`test_user_status` may pass or fail depending on DB seed; same status as Step P.2.)

- [ ] **Step 1.6: Commit**

```bash
git add app/routes/middleware/__init__.py tests/routes/cache_control_middleware_test.py
git commit -m "$(cat <<'EOF'
feat(security): add no-store Cache-Control middleware to API responses

Fixes Zoom Marketplace review FAIL "Cacheable HTTPS Response". Meeting Link
and Meeting ID responses can no longer be cached by browsers or proxies.

Spec: docs/superpowers/specs/2026-05-14-zoom-cacheable-response-fix-design.md
EOF
)"
```

---

## Task 2: TDD the `/assets/` exemption

The middleware currently applies to ALL responses, including frontend static assets under `/assets/`. We don't want that — Vite content-hashes those files and they should be cacheable for page-load performance. Drive the exemption with a test.

**Files:**
- Modify: `tests/routes/cache_control_middleware_test.py`
- Modify: `app/routes/middleware/__init__.py`

- [ ] **Step 2.1: Write the failing test**

The `/assets/` mount may not exist in the test environment (per `app/routes/static.py:83-87`, when the dev build path is missing, the mount is skipped with just a warning). To make this test deterministic, we test the middleware function directly with a stub `call_next`. Append this to `tests/routes/cache_control_middleware_test.py`:

```python
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "path,should_have_headers",
    [
        ("/assets/index-abc123.js", False),
        ("/assets/styles-xyz.css", False),
        ("/internal/v1/schedule", True),
        ("/healthcheck", True),
        ("/", True),
    ],
)
async def test_no_store_dispatch_exempts_assets(path, should_have_headers):
    """The middleware skips header-setting only for /assets/* paths."""
    from app.routes.middleware import _no_store_dispatch

    # Stub Request — we only read .url.path
    request = SimpleNamespace(url=SimpleNamespace(path=path))

    # Stub call_next that returns a fake response with a mutable headers dict
    async def fake_call_next(_):
        return SimpleNamespace(headers={})

    response = await _no_store_dispatch(request, fake_call_next)

    if should_have_headers:
        assert "no-store" in response.headers["Cache-Control"]
        assert response.headers["Pragma"] == "no-cache"
        assert response.headers["Expires"] == "0"
    else:
        assert "Cache-Control" not in response.headers
        assert "Pragma" not in response.headers
        assert "Expires" not in response.headers
```

- [ ] **Step 2.2: Run the new test, confirm the `/assets/*` cases fail**

Run:
```bash
uv run pytest tests/routes/cache_control_middleware_test.py::test_no_store_dispatch_exempts_assets -v
```

Expected: the two `/assets/...` parametrize cases FAIL with `AssertionError: assert 'Cache-Control' not in {...}` (the middleware is currently setting headers on everything). The other three cases PASS.

- [ ] **Step 2.3: Add the exemption to the middleware**

Edit `app/routes/middleware/__init__.py`. Replace the body of `_no_store_dispatch` with:

```python
async def _no_store_dispatch(request: Request, call_next):
    """Defeat HTTP caching on every API response.

    Required by Zoom Marketplace review: sensitive fields like join_url
    and zoom_meeting_id must never be cached by browsers or proxies.

    Static assets under /assets/* are exempt — they're Vite-hashed and
    safe to cache.
    """
    response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        return response
    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0, private"
    )
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
```

- [ ] **Step 2.4: Run the parametrized test, confirm all cases pass**

Run:
```bash
uv run pytest tests/routes/cache_control_middleware_test.py::test_no_store_dispatch_exempts_assets -v
```

Expected: all 5 parametrize cases PASS.

- [ ] **Step 2.5: Run the full new test file**

Run:
```bash
uv run pytest tests/routes/cache_control_middleware_test.py -v
```

Expected: all tests PASS (currently `test_healthcheck_has_no_store_cache_headers` + 5 parametrize cases = 6 passing).

- [ ] **Step 2.6: Commit**

```bash
git add app/routes/middleware/__init__.py tests/routes/cache_control_middleware_test.py
git commit -m "$(cat <<'EOF'
feat(security): exempt /assets/* from no-store middleware

Vite content-hashes frontend assets; they're safe to cache and forcing
no-store would regress page-load performance.
EOF
)"
```

---

## Task 3: Coverage tests for authenticated endpoint and error responses

These don't drive new implementation (the middleware already covers them) — they're regression tests that lock in the spec's "ALL responses" guarantee against future regressions.

**Files:**
- Modify: `tests/routes/cache_control_middleware_test.py`

- [ ] **Step 3.1: Write the authenticated-endpoint test**

Append to `tests/routes/cache_control_middleware_test.py`:

```python
from unittest.mock import patch

from fastapi.testclient import TestClient


def test_authenticated_schedule_list_has_no_store_headers(
    authenticated_client: TestClient,
):
    """The sensitive endpoint that triggered the Zoom review must carry the headers."""
    # No need to seed a doctor — even a 200 response with empty meetings is enough
    # to verify headers. authenticated_client already wires the auth override.
    with patch("app.routes.internal.sync_zoom_meetings"):
        response = authenticated_client.get(api_app.url_path_for("schedule_list"))

    assert response.status_code == status.HTTP_200_OK
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"
```

Note: this test reuses the `authenticated_client` fixture from `tests/routes/conftest.py`. That fixture auto-creates the dev doctor — no manual setup needed.

- [ ] **Step 3.2: Write the error-response test**

Append to `tests/routes/cache_control_middleware_test.py`:

```python
async def test_404_response_has_no_store_headers(aclient):
    """Even error responses must carry the cache-defeating headers."""
    response = await aclient.get("/this-path-does-not-exist-anywhere")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"
```

Note about this test: in production, the SPA catch-all route (`GET /{path:path}` in `app/routes/static.py:104`) intercepts unknown paths and serves `index.html`. In the test environment, the static mount is skipped because the public dir doesn't exist (verified in Step P.2 — `is_local_testing()` returns True and the mount returns early). So `/this-path-does-not-exist-anywhere` should yield a real FastAPI 404. If it instead returns 200 with HTML, the static mount IS active in the test env; in that case change the path to something that the catch-all will also fall through (e.g., a path under a known router prefix like `/internal/v1/this-route-does-not-exist`).

- [ ] **Step 3.3: Run both new tests**

Run:
```bash
uv run pytest tests/routes/cache_control_middleware_test.py::test_authenticated_schedule_list_has_no_store_headers tests/routes/cache_control_middleware_test.py::test_404_response_has_no_store_headers -v
```

Expected: both PASS.

If `test_404_response_has_no_store_headers` returns a 200 instead of 404 (static mount is active in test env), change the path inside the test to `/internal/v1/this-route-does-not-exist` and re-run.

- [ ] **Step 3.4: Run the full new file**

Run:
```bash
uv run pytest tests/routes/cache_control_middleware_test.py -v
```

Expected: 8 tests PASS (1 healthcheck + 5 parametrize + 1 authed + 1 404).

- [ ] **Step 3.5: Commit**

```bash
git add tests/routes/cache_control_middleware_test.py
git commit -m "$(cat <<'EOF'
test(security): cover authed endpoint and 404 paths for no-store middleware
EOF
)"
```

---

## Task 4: Full regression sweep on the routes test suite

We modified shared middleware. Run the entire `tests/routes/` directory to catch any regression — e.g., another test that asserts on response headers and might be tripped up by the new ones.

- [ ] **Step 4.1: Run all route tests**

Run:
```bash
uv run pytest tests/routes/ -v
```

Expected: same pass/fail counts as before our changes, plus our 8 new passes. None of the existing tests should newly fail. If any existing test fails:
1. Open the failing test and inspect what it asserts on `response.headers`.
2. If it does a `==` equality check on the full headers dict, it needs updating to be tolerant of additional headers (use substring assertions or pop the new ones). Make the minimal change to fix.
3. If it asserts on something unrelated and is now flaky, investigate before assuming our change is unrelated.

- [ ] **Step 4.2: If any fixups were needed, commit them**

Only run this step if Step 4.1 surfaced and fixed existing tests:

```bash
git add tests/routes/
git commit -m "$(cat <<'EOF'
test: relax header equality assertions after no-store middleware addition
EOF
)"
```

If Step 4.1 was clean, skip this step.

---

## Task 5: Manual verification against a running local server

Before declaring done, verify the same way a Zoom reviewer would: with `curl -I`.

- [ ] **Step 5.1: Start the dev server**

In one terminal:
```bash
just py_dev
```

Wait until you see the FastAPI startup logs. If `just py_dev` doesn't exist or fails, the user has a different dev command — ask before improvising.

- [ ] **Step 5.2: Verify cache headers on a public endpoint**

In another terminal:
```bash
curl -sI http://localhost:$PYTHON_SERVER_PORT/healthcheck | grep -iE 'cache-control|pragma|expires'
```

Expected output (capitalization may vary):
```
cache-control: no-store, no-cache, must-revalidate, max-age=0, private
pragma: no-cache
expires: 0
```

If `$PYTHON_SERVER_PORT` is not set in your shell, look at `.env.dev` for the value (or hardcode it in the curl command).

- [ ] **Step 5.3: Verify cache headers on a 404**

```bash
curl -sI http://localhost:$PYTHON_SERVER_PORT/this-route-does-not-exist | grep -iE 'cache-control|pragma|expires'
```

Expected: same three headers present.

- [ ] **Step 5.4: Verify `/assets/*` is exempted (if a build exists)**

If `web/build/development/client/assets/` exists locally (run `just js_build` if not), pick any file in it and curl it:

```bash
ls web/build/development/client/assets/ | head -1
# pick a filename from the output, e.g. index-abc123.js
curl -sI http://localhost:$PYTHON_SERVER_PORT/assets/<that-filename> | grep -iE 'cache-control|pragma|expires'
```

Expected: NO output (none of these three headers present on asset responses).

If no build exists and you don't want to run a JS build just for this verification, skip this step — the unit test in Task 2 already covers the exemption logic deterministically.

- [ ] **Step 5.5: Stop the dev server**

`Ctrl-C` in the dev server terminal. No commit needed for this task — it's verification only.

---

## Task 6: Final sanity check before handoff

- [ ] **Step 6.1: Re-read the diff**

```bash
git log --oneline -3
git diff HEAD~3..HEAD -- app/routes/middleware/__init__.py tests/routes/cache_control_middleware_test.py
```

Confirm:
1. `app/routes/middleware/__init__.py` changes are: added `Request` to the FastAPI import, added `_no_store_dispatch` function at module level, added one line at the bottom of `add_middleware()`. Nothing else.
2. `tests/routes/cache_control_middleware_test.py` is a new file containing the 4 test functions / 8 test cases.

- [ ] **Step 6.2: Report**

Report back to the user with:
- Commit SHAs of the new commits
- Confirmation that all tests in `tests/routes/cache_control_middleware_test.py` pass
- Confirmation that the full `tests/routes/` regression sweep passes
- Note any unexpected behavior or deviations from this plan

Do not declare the task complete until the user acknowledges and (likely) pushes/deploys to staging for a real Zoom-reviewer-eye verification.
