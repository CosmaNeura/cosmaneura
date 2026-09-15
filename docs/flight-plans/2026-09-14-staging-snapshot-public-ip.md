# Approved bounded human public-IP check (Tier 2)

## Review40 correction — September15

Independent review40 found P1 at the original head09def321: incrementing the
integer `_attempts` replaced a retained module-global binding. The real
SourceRuntimeBinding callback therefore refused the first public-IP check
before HTTP. No actual live request or human consumer was attempted.

Use one identity-preserving list with a checked integer element. Increment it
before I/O; keep the same process-wide three-attempt limit across instances and
failed requests. No reset API, retry, extra network permission or relaxed source
binding is introduced. The bootstrap pin changes to the corrected exact source.

Two new regressions pass with the real sealed-process/source_bindings and
SourceRuntimeBinding.revalidate paths: three mocked-HTTP checks preserve all
recorded source bindings, the fourth refuses, and replacing the counter object
still refuses before HTTP. Interpreter, native/runtime observations, approval,
CA and file observations are explicitly invented/mocked. No bind_process/main,
human consumer, credential, real IP service or encrypted workspace executes.

The positive regression failed on original source after its missing inert-module
fixture setup was corrected, then passed with the counter fix. Two existing
affected limit/failure tests and the changed source-pin check pass. An initial
combined invocation exhausted the intended process-wide budget across methods;
only the two affected methods were then run in their required separate fresh
processes. No test-only production reset was added. Ruff/format/Pyright pass.

Review40 remains FAIL for its original405/406/408/409 heads. This correction and
all propagated exact heads require a new independent review; no passing review
or merge approval is inferred. Original evidence is retained at
https://github.com/CosmaNeura/cosmaneura/pull/409#issuecomment-5673691514.

## Outcome and authority

Stacked on PR404 at d6132c171ee6bf4c2a7f19a4c40f2c4fa54f709f. Supply the
missing current-public-IPv4 operation for the reviewed human snapshot, using
only the specifically approved https://api.ipify.org endpoint. This component
is dormant, with no executable human consumer, CLI, firewall or Azure mutation.
The approval authorizes disclosure only of public IP/basic connection metadata,
not credentials, application data, deployment or bypass of another gate.

The user's exact approval is recorded locally in
STAGING-IPIFY-APPROVAL-20260914.json, raw SHA256
2a8bdf2eb217c3ad9fee21bfdb3dd00b34efee160de6d77f82e6f43f23b023aa.
The adapter reads that exact source-fixed path through NativeFiles, with its
owned0700 parent,0400 one-link file, protected ancestors, ACL and metadata
checks. It checks the expected digest before HTTP and unchanged identity/content
afterwards and across calls. No caller-selected service or approval hash.

This is service permission, not a reviewed source/plan/handoff or human-presence
receipt. The complete Operations adapter must supply the exact retained
source/runtime revalidation callback and enforce the other gates. This module
does not establish configured-user/ARM-token identity or replace that procedure.
The bootstrap now loads14 exact modules; the public-IP component is source
pinned but not invoked by startup or by an assistant diagnostic.

## Bounded request contract

At most three attempts in one fresh sealed human process, shared across all
adapter instances. Failed permission/network/response attempts consume budget;
constructing another adapter does not reset it. No reset, retry or pagination.
One allowance covers preparation plus the capture core's two consistency checks.
The final launcher must not restart processes to evade that per-run allowance.

GET to exactly https://api.ipify.org with no query/body/Authorization/cookie or
application/session object. Explicit Accept:text/plain and identity encoding.
ProxyHandler({}) and the existing closed no-redirect handler. Ambient proxy,
CA, Python and DYLD overrides refuse. TLS uses only the existing protected,
source-pinned public OS CA, hostname verification and minimum TLS1.2; no default
CA discovery. Source/runtime, permission and CA checks occur before/after HTTP.

One original <=15second absolute alarm covers all checks, DNS/TLS, response
reading, teardown and postchecks. Never nested or reset. The runtime callback
must perform only local retained checks under that existing timer, no child,
CLI, network or token operation. Remaining timeout is passed to urllib.

Accept only200, exact unchanged URL, one text/plain type, no compression or
duplicate lengths, optional Content-Length1..16 matching actual bytes. Read
at most17bytes; accept only a canonical global unicast IPv4 with at most one
trailing LF. Private/shared/documentation/loopback/reserved/multicast/IPv6,
leading zeroes, extra whitespace and oversized bodies refuse. No raw response
or provider exception is printed. HTTP errors are closed; close errors are
also converted to the one fixed refusal code.

## Evidence and limitations

20 new tests PASS with invented permission/CA/IP values and mocked HTTP:
exact request/headers/no proxy, three attempts across instances, failed-budget
consumption/no retries, changed permission before/after/between requests, CA
drift, ambient overrides, weak TLS, redirect/status/URL errors, all five
redirect-handler closure paths, HTTP error/close-error redaction, compression/
duplicate/wrong headers, bad lengths, bounded empty/oversized bodies, invalid
IPv4 classes/format, invalid timeout, swallowed expiry, existing timer and
runtime drift. Native file and process classes are real; their returned approval/
CA observations and all HTTP responses are explicit fixtures, not live proof.

The changed14-module exact-source-pin regression passes; Ruff/Pyright/diff
checks pass. No unchanged broad passing suite was replayed. Actual secretless
macOS copied-Python3.14.7 startup PASS:14source modules,378nativeimages,
15PGfiles,26retainedPGpaths, full runtime binding/revalidation in5.402seconds.
Probe SHA256:2b0da135f7c2c3c3225e99933bc3a2153e4b35628cbd5f1788a398b7cb6be990.
It only loaded the public-IP module; it did NOT construct/call its adapter,
read its approval, make a network request, or run any human consumer.

No ipify request, account/token/secret read, database connection or Azure action
was performed. Live endpoint availability is not established by mocked tests.

## Recovery and next gates

Stop using dormant code on failure; no resource rollback or cleanup is needed.
Preserve the existing30minute human-managed single-current-IP firewall approval
and its human creation/removal requirement. Current plan/handoff/Terminal gates,
normal specifically authorized protected merges and independent exact source
review remain mandatory. Review36 and older source receipts do not cover this
new component. No new merge or deployment authority is inferred.

Next complete the typed Operations and human plan/handoff composition. Actual
snapshot, isolated restore/four migrations, manual staging release, Clerk/Auth
and dependency-ordered Zoom validation plus30minute observation remain undone.
