# Zoom Marketplace Fix: Cacheable HTTPS Response

**Date:** 2026-05-14
**Status:** Design approved, pending implementation plan
**Context:** Zoom Marketplace review returned a FAIL on "Cacheable HTTPS Response": the application allows sensitive information such as Meeting Link and Meeting ID to be cached or stored in the local cache, where it may be retrieved by other users with access to the same computer.

## Goal

Ensure that no HTTP response from our FastAPI application can be cached by a browser, intermediate proxy, or shared cache, except for the explicitly-safe Vite-hashed frontend assets under `/assets/`. The fix must be observable in the response headers of every authenticated and unauthenticated API endpoint, so the Zoom reviewer can verify it by inspecting any request in their browser's network panel.

## Non-goals

- Frontend storage hardening. Audit confirmed no use of `localStorage`, `sessionStorage`, `IndexedDB`, or service workers — nothing to change there.
- Stripping `zoom_meeting_id` from URL paths. The path `/schedule/:zoom_meeting_id` appears in browser history regardless of HTTP cache directives; that is a separate concern not raised by this review.
- Edge / CDN configuration. There is currently no CDN or reverse proxy in front of the app (confirmed: no nginx in `Procfile`, no CDN in `infra/`).
- Changing the response shape of any endpoint.
- Adding cache headers to the Vite-bundled JS/CSS under `/assets/`. Those are content-hashed and meant to be cached aggressively — touching them would regress page-load performance for no security benefit.

## Current state (verified)

- `app/server.py` builds a single `api_app: FastAPI` and mounts: `internal_api_app`, `external_api_app`, `chat_api_app`, `webhook_api_app`, `zoom_auth_app`, `recall_callbacks_app`, plus `/healthcheck`, `/status`, and `mount_public_directory(api_app)` which serves SPA assets.
- `app/routes/middleware/__init__.py:add_middleware()` registers `fastapi_access_logger`, `logging_context`, `CORSMiddleware`, `TrustedHostMiddleware`, `SessionMiddleware`, and (debug-only) `PdbMiddleware`. **No `Cache-Control` header is set anywhere in `app/`** (grep verified).
- `app/routes/static.py:mount_public_directory()` adds the static mount at `/assets/` (via `GZipStaticFiles`) and the SPA catch-all routes `GET /` and `GET /{path:path}` which return `index.html`.
- The sensitive endpoint that triggered the review is `GET /internal/v1/schedule`, which returns `ZoomMeetingResponse` (includes `join_url`, `zoom_meeting_id`, `topic`, `start_time`, `duration`).

## Design

### Single middleware in `add_middleware()`

Add a new HTTP middleware to `app/routes/middleware/__init__.py` that runs on every response from `api_app` and applies cache-defeating headers to all responses **except** those for paths starting with `/assets/`.

Headers applied:

```
Cache-Control: no-store, no-cache, must-revalidate, max-age=0, private
Pragma: no-cache
Expires: 0
```

Rationale per directive:

| Directive | Why |
|---|---|
| `no-store` | The load-bearing one. Forbids any cache (browser, proxy) from writing the response to disk or memory. |
| `no-cache` | Defense-in-depth: even if some intermediary ignores `no-store`, this forces revalidation. |
| `must-revalidate` | Belt-and-suspenders for proxies that might serve a stale cached copy when offline. |
| `max-age=0` | Redundant with `no-store` but required by some security scanners and explicit for clarity. |
| `private` | Forbids shared caches (corporate proxies, CDN edges) from storing the response. |
| `Pragma: no-cache` | HTTP/1.0 compatibility. Legacy intermediaries still honor this. |
| `Expires: 0` | HTTP/1.0 compatibility. A past date signals "already stale". |

### Scope: what the middleware covers

- ✅ All routes in `internal_api_app`, `external_api_app`, `chat_api_app`, `webhook_api_app`, `zoom_auth_app`, `recall_callbacks_app`
- ✅ `/healthcheck`, `/status`
- ✅ SPA shell: `GET /` and `GET /{path:path}` returning `index.html` (good practice for SPAs — guarantees users get the latest shell after a deploy)
- ✅ Error responses (4xx, 5xx) — middleware wraps `call_next` so they receive the headers too
- ⛔ Static assets under `/assets/*` — explicitly skipped to preserve Vite's content-hashed asset caching

### Ordering relative to existing middleware

Starlette's `add_middleware()` inserts at position 0 of the middleware list, so the **last-added middleware becomes the outermost wrapper**. The outermost wrapper is the *last* to touch the response on the way out, which is exactly where we want to sit — no other middleware can overwrite our headers. We will register the new middleware as the **last** call inside `add_middleware()` (after `SessionMiddleware` and after the debug-only `PdbMiddleware` block).

### Implementation sketch

```python
# in app/routes/middleware/__init__.py

async def _no_store_dispatch(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0, private"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def add_middleware(app: FastAPI):
    # ... existing middleware registrations unchanged ...

    # MUST be the LAST registration so it becomes the outermost wrapper
    # and its headers can't be overwritten by other middleware.
    app.middleware("http")(_no_store_dispatch)
    return app
```

`app.middleware("http")(fn)` is the function-call form of the `@app.middleware("http")` decorator; both delegate to `app.add_middleware(BaseHTTPMiddleware, dispatch=fn)`.

### Verification

The reviewer's check is: open browser devtools, navigate to any authenticated page, inspect any API response, confirm `Cache-Control: no-store` is present. Our verification mirrors this:

1. `curl -I https://app.cosmaneura.com/healthcheck` should show the three headers
2. `curl -I` against any `/internal/v1/*` endpoint (with valid auth) should show the three headers
3. `curl -I` against `/assets/<hashed-file>.js` should **not** show the three headers (Vite caching preserved)

## Testing

Add `tests/routes/middleware_test.py` (or extend an existing test file in `tests/routes/`) with:

1. **Public endpoint test** — `GET /healthcheck` returns `Cache-Control: no-store, ...`, `Pragma: no-cache`, `Expires: 0`.
2. **Authenticated endpoint test** — `GET /internal/v1/schedule` (with an authed test client) returns the same three headers.
3. **Error response test** — A request that triggers a 404 still returns the three headers (FastAPI's default 404 response goes through the middleware).
4. **Static asset exclusion test** — Use a request to a path under `/assets/` (e.g., `/assets/nonexistent.js`). Even if the asset doesn't exist (returns 404 from `StaticFiles` or the SPA catch-all), the response should **not** have `Cache-Control: no-store`. If the `/assets/` mount is not present in the test environment, fall back to a direct unit test that imports the middleware function and calls it with a mock `Request` (path `/assets/foo.js`) and asserts no headers were added.

Each test asserts the directive presence using substring match (e.g., `"no-store" in response.headers["Cache-Control"]`), not equality, so future additions to the header don't break tests.

## Risks and considerations

- **Performance**: zero measurable impact. Three string assignments per response; no I/O.
- **Backwards compatibility**: none broken. We only add headers, never strip or modify existing behavior.
- **Header override**: a future endpoint might want to set its own `Cache-Control` (e.g., a public, cacheable endpoint). Because this middleware is the outermost wrapper, it would overwrite any per-route header. If/when that need arises, refactor to skip when the response already has `Cache-Control` set. Not needed today.
- **Webhooks (`/webhooks/zoom`, `/recall_callbacks/*`)**: setting `no-store` on webhook responses is harmless — Zoom doesn't cache them anyway; it only inspects the status code.
- **Frontend `<a href={meeting.join_url}>`**: the link in the DOM is fine. It exists only while the page is rendered. The fix addresses the cached *response body*, which is what the reviewer flagged.

## Out-of-scope follow-ups (do not do in this change)

- Adding a `meta http-equiv="Cache-Control"` tag inside `index.html` (mostly cosmetic — modern browsers prefer HTTP headers; we already cover the HTML response with `no-store` via the middleware path coverage)
- Auditing whether `zoom_meeting_id` should be a UUID instead of a numeric Zoom ID (separate threat model)
- Adding a CSP `frame-ancestors` policy or other security headers (separate Zoom requirements, separate change)
