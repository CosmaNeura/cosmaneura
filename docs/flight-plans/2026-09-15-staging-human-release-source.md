# Human staging release: source decision primitive (Tier 2)

This is the first implementation slice of the approved Actions-off human release
design. It is NOT a release entrypoint or permission to execute one. No Azure,
build, publication, migration, deployment, account configuration or workflow is
changed. The existing Actions CLI remains founder/manual/protected-master only;
there is no human bypass flag and no invented GitHub event identity.

The existing immutable-candidate/merged-PR/current-protected-master and AST-only
schema checks are shared with an explicit human source context. The new function
requires an externally supplied read transport; it has no default runner. Before
and after source verification it reads the configured GitHub user, requires the
founder login and a positive integer User ID, and compares minimized identity
hashes. It also requires the expected repository origin and a clean controller
checkout before and after. A callback or matching environment string cannot
authenticate itself; invented test responses are not operational evidence.

The future separately reviewed human controller must bind these callbacks to
bounded authenticated read-only transport, preserve original evidence and exact
source pins, and revalidate at each privileged phase. This source result always
sets execution/deployment authorization false. Actual configured Azure user and
same ARM token linkage, role disclosure/acceptance, founder-selected candidate,
independent code/plan review, actual snapshot/ONLINE migration rehearsal, exact
image archive/digest, no-concurrent-release control, expiry, regular Terminal and
human execution remain mandatory. No raw identity is returned by this helper.

Next slices implement the credential-free scratch build and narrowly enumerated
human release using existing staging resources. Do not source deploy.sh as a
library, create a new standing identity/network/builder or extend the snapshot
consumer into a deployment framework. UI/login acceptance comes before cleanup;
Clerk and ordered Zoom validation follow the first usable staging release.

Verification uses only invented callback responses plus affected existing source
verifier tests; no real CLI, account, network, migration or human consumer. This
slice has ten new passing unittest methods and33 affected existing pytest cases
passing; the two unchanged CLI cases were deselected. A strengthened no-human-flag
case passed separately. Root application conftest and plugin autoload were
disabled. Pytest reported two cache-write warnings because /dev/null was the
isolated config; all33 cases passed, no cache or permission change was attempted.
Future isolated invocations should disable cacheprovider rather than replay these
tests. Ruff import ordering was corrected; explicit-interpreter Pyright is clean.
No full application suite or paid CI ran. This
work remains unreviewed and cannot authorize a protected merge or release.
Recovery is leaving it dormant or a normal reviewed source revert; no live state
needs rollback.
