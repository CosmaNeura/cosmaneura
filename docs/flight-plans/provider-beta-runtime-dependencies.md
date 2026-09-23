# Provider beta runtime dependency remediation

Tier 2: identity SDK, HTTP runtime, and static-file security. Based on protected
Core master `ddfe047c`; independent of the dormant Session Prep feature stack.
GitHub Actions is disabled. No cloud, data, cohort, or environment mutation.

The registry audit initially found 14 affected runtime packages. The final
locked runtime export checks 129 registry packages with zero known advisories.
The pinned activemodel Git dependency is excluded from registry advisory coverage.
Clerk and MSAL had to advance because their old bounds pinned vulnerable
cryptography. Use FastAPI's standard extra without its cloud deployment CLI.

Compatibility changes preserve Clerk request verification, existing generated
operation names, nested router registration, and static asset serving. Missing
bearer credentials now return HTTP 401 with a Bearer challenge. Compressed and
SPA files use Starlette's protected directory lookup; traversal and symlinks
outside the public directory cannot expose files. Generated schema changes are
limited to response titles and framework validation-error documentation; the
domain contract and operation names remain unchanged.

Local checks: 1,448 application tests passed in the broad run; the sole remaining
snapshot-pin failure was corrected and the affected contract/auth tests passed
(19 passed, one existing skip). The authority kernel has 131 passed and one
existing optional historical-migration skip. Changed-code Ruff and focused
Pyright checks pass. The generated frontend client typecheck and local build
pass. Static route tests cover gzip negotiation, caching, methods, traversal,
symlink rejection, and SPA fallback. All data and external clients are synthetic.

Recovery is the previous application image and lockfile. No migration or data
rewrite is included. Independent agent review of the final pushed commit remains
required before this draft can enter a release candidate.

Cursor's independent review identified two nested-router consumers still using
the old flattened layout. Access logs and the routes CLI now traverse effective
route contexts, preserving full route templates and leaf names without logging
raw paths or queries. Static serving rejects NUL paths with 404 and preserves
UTF-8 charset on precompressed text. The focused static/access/auth checks pass
26 tests with one existing optional skip; focused Pyright and Ruff pass.

The review's package-age concern did not establish a runtime defect. This exact
FastAPI/Starlette/Clerk lock was also exercised in the combined beta candidate's
1,513 application, 131 authority, and four concurrency checks; the artifact report
records the exact candidate and test-report hashes. No advisory or compatibility
exception is inferred from package age, and the local tests do not prove safety
against undisclosed vulnerabilities. The final substantive push requires a new
Cursor review before release.
