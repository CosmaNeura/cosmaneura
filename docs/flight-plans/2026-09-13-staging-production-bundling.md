# Staging frontend production bundling

## Outcome and scope

Tier 2, conservatively: change the build boundary without deploying anything.
The legacy public configuration uses `NODE_ENV=staging` as an environment
selector, and the container recipe extracts `/app/build/staging/client`.
Passing that value directly to Vite enables DEV branches even with
`MODE=production`. A local exact-head build reproduced that behavior.

The package build now retains the input target in the private child-process
field `COSMANEURA_BUILD_TARGET`, and invokes route type generation and the
compiler with `NODE_ENV=production`. The build command explicitly uses
`--mode production`. React Router retains the original output target; staging
assets still live in `build/staging/client`. The parent environment and all
public configuration values are unchanged. No new dependency is needed.

The private target field is not a new caller override: the runner rejects an
already-set value. Targets are allowlisted; arbitrary output paths are refused.
Direct staging CLI invocation is refused with instructions to use the package
build. Existing dev/typegen entry points retain their environment behavior.

## Boundaries and compatibility

No workflow, vault reference, public-config allowlist, backend PYTHON_ENV,
provider account, dark flag, Sentry credential barrier or output extraction
path changes. Production-mode Sentry handling stays in the existing Vite
configuration; this patch grants no token or release-upload authority.
Source maps remain controlled by the unchanged production-mode configuration.

Only candidates containing this source correction receive the new behavior.
It does not patch or attest historical candidates, alter their immutable SHA,
or authorize replacing the founder-selected first staging candidate. An older
candidate's build-mode disposition remains a separate release decision; never
silently overlay changed source into an exact-candidate checkout.

## Verification and release holds

Run focused environment/runner regression tests and a fresh local staging-input
build. Verify compiled DEV=false and PROD=true, MODE=production, unchanged
staging target/configuration, and absence of source maps. Record exact source
and output evidence. This local build is not a protected container image,
provider-separation proof, real Clerk smoke or deployment acceptance.

Independent exact-final-head review and protected CI remain required. Keep
the PR separate from Auth #238 and the pinned infrastructure operator checkout.
No merge, image publication, cloud mutation or deployment is authorized here.

Recovery is a separately reviewed source revert before a later explicitly
authorized build/deployment. Do not restore development-mode staging artifacts
as an unexamined fallback. No database/schema change or migration is involved.
