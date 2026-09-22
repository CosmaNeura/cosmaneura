# Exact source/runtime snapshot startup (Tier 2)

## Outcome and scope

Stacked on PR402, base db57fdeffd313fc45bc1e7114707c48fbe8f0d5b. Connect the
12 existing snapshot source modules to one exact, fresh Python3.14.7 startup,
native preload, permanent process seal and retained source/runtime binding.
This is NOT a complete consumer or a live authorization. No credential, CLI,
network, database, native encrypted-image or Terminal operation is performed by
the new binder. No infrastructure, service, role, grant or dependency is added.

The eventual reviewed human command must verify the loader bytes BEFORE exec,
use the exact closed clean environment and normally merged reviewed source.
Its loader digest is independently supplied by the reviewed handoff, not read
from the same file and accepted as authority. The binder compares that digest
before source execution and again through the sealed native protected read.
It never claims to prove Git ancestry, branch protection or independent review.

## Independent runtime authority

The fixed existing copied Homebrew closure remains unchanged. Its expected
manifest is 4dc3443055fef6f2112c96178fd86df3bf5a675ccdfaac6cd597f2bda13ebb0f.
This is now independently DERIVED, not an observed-copy self-attestation:

- Normal protected-master source: 16ad15538b218f9e5bfd9cb4a5970cbfaef90a82.
- Validator Git blob: 9853f6aabccb8a1f62e7d7b0318cbe09d5466c51.
- Validator raw SHA256: 9e368b3840f3336ce0b0ba5ce049a64c24e4a886a491da233b201233b8b62243.
- Original closure pin: eb13dfaf7731f89ed7bf68591cfc6da4b6df4f27752354db4d3be8caa7049383.
- Read-only derivation completed 2026-09-14T22:47:06.288853Z. It selected only
  inspected pure/read-only AST functions and literal pins from that exact
  validator, checked the original closure, derived the fixed pyvenv/relocated
  Python/signature transformations, compared all36268 copied entries, then
  rechecked the original closure and source. No legacy mutation helper ran.
- Diagnostic script SHA256: adced824e686f45f699c5e5fe843c359b993cc2f40eda871371ea701a11d845d.

That derivation is preparation evidence for independent review, not execution
authority. Changed source/runtime requires new review, never a rebaseline of
whatever bytes happen to be present. Runtime hash binding still precedes any
secret-bearing object; every later operation must invoke the retained checks.

## Startup and retained checks

Require macOS arm64, unprivileged unchanged UID, exact CPython3.14.7 executable,
prefixes, three standard search paths, -I/-S/-B, no optimization/trace/profile.
Exact env-i-equivalent environment selects only the pinned copied libraries,
no OpenSSL config, and an explicit UID-derived CoreFoundation text encoding.
macOS otherwise inserts that setting during native loading; do not accept its
observed value or an arbitrary inherited environment as authority.

Disable core dumps. Read all12 exact0400 one-link public source files before
executing any, refuse preexisting module names, compile the checked bytes in
dependency order without source-path imports/pyc. Preload standard/native
libraries and initialize the built-in OpenSSL provider, then seal. Only then
read source with the actual protected ancestor/ACL checks and bind the entire
runtime against the derived source constant. The pre-seal and sealed guards
share ONE original90second maximum deadline; they never nest/reset it.

Remove only the three exact bootstrap-owned loader/OpenSSL settings after
checking that the entire startup environment is still exact. Session transport
keeps its existing rejection of ambient overrides. Record full native image
inventory, module origins and source function/class/code-object identities;
recheck those, protected source bytes/metadata and the complete retained runtime
before later operations. pyexpat's four origin-less data-module aliases must be
the exact errors/model objects of its pinned parent extension; no general
origin-less module exception is accepted.

## Python3.14 native-call compatibility correction

The real startup test found that Python3.14 emits ctypes.call_function for
ordinary prebound ACL calls. The older all-ctypes-event ban therefore refused
the actual native protected read although Python3.13 tests passed. Resolve a
CLOSED seven-function set from /usr/lib/libSystem.B.dylib before installing the
seal: acl_get_fd_np, acl_get_entry, acl_get_tag_type, acl_free, __error,
_dyld_image_count and _dyld_get_image_name. Keep the library/addresses in the
guard closure; there is no caller-provided address or registration API.

Permit only that event's exact address/tuple shape for those existing functions.
All dynamic native loading/symbol lookup, other ctypes events/functions, Python
workers, subinterpreters, fork and newly imported modules remain refused.
NativeFiles/NativeImages retain fixed prototypes. This is an interface invariant
for trusted pinned code, NOT an adversarial same-UID/native-code sandbox.
Review36's old PG source verdict does not cover this correction.

## Verification and recovery

29 distinct new startup tests pass across affected runs: source/hash/mode/link/
race/size, dependency order, shadow imports, runtime origins, exact pyexpat
aliases, function/class rebinding, parent facts, env policy, one-shot startup,
native inventory drift, shared deadline and isolated actual timer teardown.
Composition unit tests mock interpreter/native/runtime/seal boundaries; they
are not native proof. Nine affected process-seal tests pass, including two new
closed-native-set regressions; the Python3.14 native protected-read regression
passes with actual invented local files. No unchanged broad suite was rerun.

Separately, the actual fresh copied Python3.14.7 native startup fixture passed:
12 source modules,378 native images, source-derived full runtime binding,
permanent seal and retained revalidation in5.126seconds. Public source was
copied to an isolated0700/0400 test fixture; no credential/session/CLI/capture
method ran. Its audit guard refused network, writes except /dev/null and all
child commands except fixed read-only ps thread counting. Directory-relative
reads rely on the exact native source's held FDs; Python audit lacks dir_fd and
is not claimed as another sandbox. Temporary public fixtures were removed;
no user data/runtime/cache was changed. Exact diagnostic/source pins are in
the same-task checkpoint. Independent final exact source review and CI remain
required. No source PASS, merge decision, live plan/handoff or release inferred.

Recovery: stop using this dormant source. No deployed state changed and no
rollback mutation/cleanup is needed. Specific normal protected merge approval
is still absent. The full typed Operations adapter, PG closure binding, current
public-IP observation, protected source provenance and actual reviewed4h plan/
10minute handoff remain gates. Preserve the existing human-managed30minute
single-IP approval; ask Terminal presence only when a reviewed action is ready.
Actual snapshot, isolated restore/four migrations, staging release, Clerk/Auth
and dependency-ordered Zoom validation/30minute observation remain incomplete.
