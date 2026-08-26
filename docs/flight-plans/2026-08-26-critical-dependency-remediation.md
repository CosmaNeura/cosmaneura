# Flight plan: remediate critical dependency advisories

## Outcome and scope

- Remove all currently reported Critical-severity dependencies from the Core
  Python and JavaScript lockfiles without changing application authorization or
  session-storage design.
- Runtime updates: `h11` 0.16+, `@clerk/shared` 3.47.4+, and the React Router
  packages at 7.9.4+.
- Development-tool updates: Vitest 3.2.6+, Happy DOM 20+, Handlebars 4.7.9+,
  and node-tar 7.5.19+.
- The application does not use Clerk `createRouteMatcher`/`createPathMatcher`
  or React Router `createFileSessionStorage`; those two vulnerabilities were
  not shown reachable in the reviewed source. The dependencies are updated so
  that future use cannot silently introduce the vulnerable implementations.
- The h11 issue affects HTTP parsing and is treated as reachable until the
  patched runtime artifact is promoted.

## Risk class and hold points

- **Tier 2.** The dependency set includes authentication and request-parsing
  components, even though the patch is intended to preserve behavior.
- Hold after dependency resolution, after CI, after independent review, and
  before production promotion.
- Abort if route generation, type checking, unit tests, the production web
  build, authentication-negative tests, or HTTP parser checks fail.

## Validation

1. Verify the Python lock resolves `h11==0.16.0` or newer and that the runtime
   imports the same version.
2. Run the complete frontend unit suite and type check.
3. Build the production frontend with synthetic configuration only.
4. Run package-manager audits for both production and full JavaScript graphs;
   require zero Critical findings.
5. After pushing the final commit, require GitHub checks and an independent
   review tied to that exact commit. Confirm the corresponding Dependabot alerts
   close; do not dismiss unresolved alerts merely because current call sites
   appear unreachable.

## Rollout and recovery

1. Merge through the protected default branch only after all hold points pass.
2. Build once and identify the immutable image digest. Do not rebuild between
   test and promotion.
3. Promote manually under the existing founder-only production controls.
4. Verify sign-in, signed-out denial, authenticated API calls, large/chunked
   request rejection, note navigation, and error handling using synthetic data.
5. Observe authentication failures, HTTP 4xx/5xx rates, and application errors.
6. If behavior regresses, roll back to the previously known-good image digest;
   do not re-open exposure by disabling request or authorization checks.

## Compatibility note

React Router 7.9 requires a function component as the route-module default.
The existing provider higher-order component can return a class-or-function
union, so the root route now exports a small function wrapper around the same
provider composition. This is a type-compatibility change, not a new auth path.

React Router 7.9 also loads the production Vite configuration during standalone
type generation. The type-generation recipes now supply the explicit
non-release identifier `typegen-dirty` only when no build commit was provided.
The identifier is scoped to the type-generation process. Production builds
still fail closed when `VITE_BUILD_COMMIT` is missing and continue to require a
Sentry token for a clean, non-CI release build.
