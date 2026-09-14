# Fixed-target read-only staging snapshot adapter (Tier 2)

## Outcome and exclusions

One dormant read-only ARM/identity component for the approved staging snapshot.
Reuse existing app/PostgreSQL resources and current normally merged runtime
attestation helpers. No new service, network, role/grant or general recovery
platform. No CLI, human consumer, firewall mutation or live execution exists
here. Direct module execution refuses. These interfaces are not authority.

The owner approved one HUMAN-managed current-IPv4 window, at most30minutes,
human creates/removes the exact rule and accepts concurrent-edit risk. The
adapter can only read rules. It cannot PUT/PATCH/DELETE, register a provider,
create/delete claims, retry a write, or close the window automatically.
Existing rules must be preserved. No atomic ownership, native TTL or crash-proof
closure guarantee. Human terminal/removal attestations remain in PR396's core.

## Identity and secret ordering

The complete future HUMAN bootstrap must irreversibly lower soft and hard core
limits to zero BEFORE any real cache/account/token/DSN read or reader creation.
Preload/attest the complete pinned source/runtime closure without retrieving
credentials, then seal the fresh dedicated isolated/no-site single-threaded
process through PR397. Never run in a shared/embedded/threaded host. The Azure
constructor, every timed read and credential-read entry require that seal and
core-limit invariant. Full bootstrap and native no-worker assumptions still
need independent source review; these fixtures do not implement a live launcher.

ExpectedIdentity comes only from trusted pinned source configuration: exact
subscription/tenant and configured-username SHA, never a plan-supplied identity.
Check Enabled AzureCloud user account; canonical scope; token issuer/audience,
tenant, UUID object ID, nbf/expiry; exactly one MSAL home account matching the
case-folded configured username; all exact-token cache matches must agree on
home account, tenant realm, public Azure CLI client and AccessToken kind.
Duplicate JSON/NaN/malformed or ambiguous cache records refuse. Protected cache
is read through the existing pinned0600 reader; never print token/cache values.

JWT decoding is NOT signature verification. Identity evidence explicitly says
that, denies historical apply identity/human authority, and requires a successful
authenticated read using the SAME exact token. Before and after each HTTP read,
refresh configured account and runtime evidence, reread protected cache, and
reject changed exact token, principal/home binding or CA closure. Only digests,
timestamps and target-match booleans leave public observations.

The240second per-read token floor is not a30minute access lease. The final exact
plan/bootstrap must require adequate token lifetime for the entire proposed
operation/cleanup, bind current principal/home identity and fresh token evidence,
and preserve actual4h plan timestamp and10minute handoff expiry. No renewal or
cached receipt can extend those gates.

## Exact provider contract

Fixed ARM origin management.azure.com; one attempt per operation, no redirects,
proxy use, retry, pagination, alternate endpoint or response-body logging.
Verified TLS context, pinned CA hash, hostname verification and CERT_REQUIRED.
Require200, exact final URL, one fresh UTC Date within60seconds, JSON content
type, identity encoding, bounded length/body at1MiB, strict JSON and no nextLink.
The supplied absolute timeout bounds runtime checks, HTTP/body and postchecks;
server/rules at most30seconds, full credential sequence at most120seconds.

Microsoft.App API2024-03-01: GET exact existing app and recovery revision;
POST exact app/listSecrets is the documented READ-ONLY listing operation with
an empty body. It does not create/change a resource. Public specification blobs:
ContainerApps3ad3dcb0026f82c0048583cfe8ccc8a7056a3404;
ContainerAppsRevisions2f571b07aac5dd1ff70f63af290963af74cd1d7b.
Microsoft.DBforPostgreSQL API2024-08-01: GET fixed server and its firewall rules.
The server must match exact ARM ID/type/FQDN, PostgreSQL16, Ready and public
network enabled. Rules require exact IDs/types, canonicalIPv4 ranges and unique
case-insensitive names; preserve the baseline rather than normalize its meaning.

## Credential provenance

Only existing cosmaneura-staging-app, its exact recovery-475908dbb4c revision,
and the one expected -web container. The full reviewed immutable registry image
digest must come from the exact trusted plan, never a tag or moving master.
Both app/revision templates must reference database-url for DATABASE_URL,
without inline value, and have unique environment names. The revision is active
and is both latest and latestReady. Observe these before and after listSecrets;
template/configuration binding changes refuse.

From the unavoidable complete in-memory listSecrets response, select exactly
database-url, reject Key Vault indirection/missing/duplicate values, then use
PR397's fixed PostgreSQL host5432/database staging DSN parser. No secret values
or secret response digest appear in repr, public evidence or logs. The returned
credential object is memory-only and repr-hidden. No PG client is invoked here.
Provider authentication plus unchanged binding is not a claim of atomic app
configuration or credential rotation exclusion. The final live plan must assess
concurrent source edits and fail on observed drift; do not claim a locked app.

## Verification and recovery

Eight pure identity-fixture tests previously PASS; only import-path relocation
and type/lint checks followed, not replaying unchanged tests. Eighteen new Azure
fixture methods initially PASS. After the constructor/core gate changed,19
affected/new methods PASS using dedicated sealed children. An intermediate six
case selection failed in parent fixture setup before test bodies; the harness
now delegates setup and test to the same child. All CLI/cache/HTTP/credentials
are invented/mocked; only short-lived self-thread reads/core-limit lowering and
self-timing in the test processes are real. No Azure/PG/encrypted workspace was
used. Ruff/Pyright and diff checks pass; final CI/independent review remain gates.

Stop using this dormant component to recover; no deployed state changed.
Never delete existing resources/backups, renew a failed window/receipt or alter
approval gates as recovery. Still required: full protected human launcher and
runtime closure, actual native encrypted binding/current public IP, normal
protected source merges, exact independent source/plan/handoff reviews and human
execution; then actual snapshot/isolated restore/four migrations, staging release,
Clerk/Auth and ordered Zoom acceptance. No live acceptance is claimed here.
