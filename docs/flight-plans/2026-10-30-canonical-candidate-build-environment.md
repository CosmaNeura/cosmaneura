# Canonical candidate build environment

Risk class: Tier 2 (deployment controller and scoped environment trust).

Canonical run 41075366120 attempt 2 passed all candidate checks and public
configuration resolution, then failed while building candidate 24b75868.
The nested candidate `.envrc` had not been allowed. Its build-argument producer
failed, but the recipe's xargs consumer still started Nixpacks without the
required public arguments and failed on `OPENAPI_JSON_PATH`.

The canonical controller now checks the exact candidate SHA, clean checkout,
and committed `.envrc` bytes before allowing that one file on its disposable
build runner. It preflights the existing argument producer into a private
temporary file before starting the existing build. Failure or empty output
stops the build. An EXIT handler denies that exact trust entry and removes the
temporary file; cleanup failure fails the job.

Candidate 24b75868 and its build recipes remain unchanged. The public input
allowlist, candidate tests, credential separation, immutable image publication,
deployment, custody, schema and recovery gates remain unchanged. No production
configuration or traffic is affected.

Validation executes the actual workflow shell with local command doubles for
successful build, wrong source, failed/empty producer, failed builder and failed
cleanup. The existing canonical contract checks remain required. This does not
claim a built image or a live deployment; the reviewed controller must merge
and a newly authorized canonical run must pass the real build and rollout.

Recovery: revert this controller change through reviewed normal integration.
Do not rerun a retired controller or weaken publication/deployment gates.
