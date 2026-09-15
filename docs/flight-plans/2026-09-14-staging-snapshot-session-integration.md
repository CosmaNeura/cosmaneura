# Exact snapshot session and fixed ARM integration (Tier 2)

## One dormant outcome

Replace the snapshot adapter's OLD helper/prepared-reader interface with the
actual owned encrypted-copy CLI session from PR400. Stacked on PR400; current
dependency c276e82d9b9e0661eea683f83ae5322728f74f89 includes only a test/docs
portability correction after800b8008. This is source preparation, NOT a live
human launcher, authority receipt, snapshot or deployment. No new resource,
network,role,grant,dependency or general recovery platform is added.

## Explicit identity, original source and trust

Require the dedicated sealed/core-disabled process and exact OwnedAzureCLI,
NativeFiles,SnapshotRuntime,BoundConfiguration/ConfiguredSource classes before
access. Expected subscription,tenant and configured-user SHA are source literals,
not caller-,plan-,environment- or token-selected authority. Fixed reviewed CLI
version and AzureCloud fingerprints precede token access. Obtain the token only
from the configured CLI's encrypted private copy; preserve the original cache.

Read the original protected MSAL cache through ConfiguredSource. Its configured
home-account record and ALL exact-token records must link to the same username,
tenant,CLI client and exact ARM token. A newly refreshed token found only in the
copy is refused. Validate token audience,issuer,tenant,OID,time and configured
account scope/kind. Stable identity/token hashes cannot change between reads.
No force refresh,login,cache writeback,token echo or historical apply-OID claim.

Initial preparation requires >2100seconds credential life. This is NOT a delayed
30-minute action authorization: the eventual protected human bootstrap must
recheck sufficient life for the ENTIRE original action/cleanup window immediately
before showing rule-creation instructions. Per-read240seconds is only a read
floor. No action clock is created,extended or renewed by these libraries.

CA source is fixed /private/etc/ssl/cert.pem and the recorded SHA256
9dae8d76e55cb08991f2b672d58999ea15560d910759c16b544f843bdffbb994.
Protected ancestors,NOFOLLOW owned descriptors,root regular leaf,one link,
no public write/ACL,4MiB limit and exact before/after metadata/content. No default
trust search or arbitrary current-file self-attestation. Client TLS loads only
those exact bytes,verifies certificate and hostname,minimumTLS1.2/no compression.
Reject ambient proxy,TLS/keylog,Python/loader settings. Preload IDNA before seal.

## ARM and deadline integration

SnapshotAzure now accepts only SnapshotSession; no old helper/wrapper callback.
Original closed literal GET app/revision/server/rules and read-only empty-body
POST app/listSecrets remain. Final Request method/URL/body assertion retained.
No PUT/PATCH/DELETE,firewall command,registration,pagination,redirect or retry.

Account/CLI,CA/TLS,HTTP and postread checks run with separate non-nested guards
sharing ONE original absolute deadline. HTTP receives only remaining time,
never a fresh timeout after preparation. HTTP response teardown finishes before
postread CLI checks. Bound headers,date,body/JSON and source image/revision/
secret-reference before/after semantics remain; secret body is never hashed or
included in printable evidence. Any change or expiry refuses rather than retries.

Only successful fixed server read supplies same-token authenticated ARM evidence
alongside the original configured-user/home/token hashes. JWT parsing is NOT
independent signature validation. Evidence still denies human authorization,
historical apply identity and plan/handoff/receipt authority. This is not a
malicious in-process/native-code sandbox or proof of missing bootstrap gates.

## Verification and limits

15 new session/CA methods PASS: exact original linkage,copy-only token refusal,
version/cloud/envelope/lifetime rejection,source/token/runtime/CA drift,ambient
settings,shared deadline,wrong object types,TLS verification and later full-window
lifetime without refresh. All account/cache/CLI values invented. Public OS CA
bytes used ONLY as TLS-parser input; native CA ownership metadata explicitly
mocked. No real account,cache,token,CLI,HTTP or encrypted workspace executed.

24 Azure methods affected by replacing the session interface have passed across
the revision series. Initial23PASS and one stale old-constructor fixture failure;
corrected fixture plus three affected core/deadline/token regressions PASS.
Three newly added adapter tests cover unbound sessions,one absolute deadline,
and preflight time not restored to the HTTP budget. No unchanged suite replay.
Ruff/Pyright/diff PASS; required final-head CI and independent review pending.

Current-head Linux CI exposed a test-data setup defect: hosted Python's compiled
default CA file is absent even though the public OS certificate bundle exists.
All15 isolated session/CA tests stopped at that fixture assertion before their
test bodies. The fixture now reads only the conventional public Linux/macOS
bundle paths before sealing; it does not select production trust, change a
runtime pin, download data or skip a test. Two new fixture regressions exercise
both OS paths without consulting Python defaults and refusal when neither exists.
The15 failed tests nowPASS locally; both new fixture regressions PASS. The first
restricted local run reached the native thread-count check but sandbox policy
refused /bin/ps; only those15 failed methods were repeated with that read-only
access. No guard was mocked or relaxed to pass. Ruff/Pyright/diff PASS. Current
Linux CI remains required. Review36's source PASS applies to prior head d635e16f,
not this later test edit; both production source files remain byte-identical.

## Recovery and remaining execution gates

Stop using dormant code; no deployed state changed. Preserve original/partial
private files; no cleanup,permission weakening,forced merge or gate bypass.
Source PASS is neither GitHub approval nor capture/release authority. Specific
normal protected merge approval and current CI/provenance remain required.

Still missing: protected full human bootstrap/source/runtime/native workspace/
current-public-IP binding and independently exact-reviewed source/actual4h plan/
ten-minute handoff. Confirm actual regular Terminal before any fresh handoff.
Human alone creates/removes the approved single-IP rule within30minutes; no
nativeTTL,atomic ownership or crashproof closure guarantee. Actual bounded
snapshot,isolated restore/four migrations,staging release,Clerk/Auth and ordered
Zoom callback/capture/revocation plus30-minute observation remain outstanding.
