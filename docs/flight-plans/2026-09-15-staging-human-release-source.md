# Human staging release: source and build inputs (Tier 2)

This is the first implementation slice of the approved Actions-off human release
design. It is NOT a release entrypoint or permission to execute one. No Azure,
image build, publication, migration, deployment or account operation is executed.
The existing Actions workflow/CLI remains founder/manual/protected-master only;
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

Next slices bind a credential-free build executor and narrowly enumerated
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

## Public build-input preparation, not a build executor

`staging_human_build_inputs.py` adds strict public frontend configuration checks,
exact native Nixpacks archive verification, safe source-archive extraction into a
new private owned directory, empty home/Docker-configuration directories, unique
image/container names and fixed shell-free build argv. No existing checkout,
container or image is removed or overwritten. Failures preserve partial state;
there is no automatic retry or cleanup. Untrusted source tar entries cannot be
absolute/traversing paths, links, devices, duplicate entries, conflicting file
parents, `.git`, or pre-existing generated `public`/`.nixpacks` contents.

The caller must authenticate the exact candidate and source archive hash using
the reviewed human source procedure. A tar commit comment or a caller-supplied
digest is NOT Git provenance or founder selection. The public configuration
must already be resolved and hash-bound externally. No secret resolver, shell
evaluation, environment inheritance, process runner, network or CLI is provided.
The standard seven-variable `.env` is checked against its exact tracked hash;
its public values are supplied explicitly, never sourced as shell code.

The same Nixpacks1.41.0 release has a native arm64 Mac asset. Its pinned archive
SHA256 is `0c6799690ad0b7328691fe3e457ec93ddc250ab229b7422fe8d8cb6ecc72a70c`,
3299388bytes; sole regular0755 member `nixpacks`,8194488bytes. Binary SHA256 is
`412128c77876b8c96bdde0c01c0fa411d935ae80bea8bec523d65692904a14fc`.
The downloaded exact archive, structure and arm64 Mach-O header were verified
read-only; the binary was NOT installed or executed. The public release CLI
source confirms `--platform` and `--no-cache` support. No package manager ran.

Generated commands preserve linux/amd64 and production-bundled staging frontend
semantics: input NODE_ENV=staging, existing wrapper selects production bundling
and the staging output directory. Backend PYTHON_ENV=staging and source labels
remain explicit. No mutable latest tag, registry, remote cache, build secret,
Sentry upload credential, push or deployment is included.

Thirteen NEW invented-fixture tests PASS0.030s: wrong/missing/duplicate public
fields, wrong staging targets, bad hashes/architecture/tar members, source/path
collisions, changed shared environment, unsafe parent, existing-directory
collision, private modes, unique names, and exact argv with metacharacters kept
as one public argument. Ruff/format/Pyright0errors0warnings/diff PASS after three
initial type-narrowing errors were corrected. No unchanged suite was rerun.
A read-only diagnostic also verified the real native asset and exported1298
files from proposed (NOT founder-selected) PR385 merge866bd4bc. Source tar:
18206720bytes/SHA256 `2548cbecccc585aa87ac11d7ad6de03646272ac9e8b05cd126cc50a634b6f990`.
No actual build workspace/image, credential, account or live action was created.

This layout is NOT a host sandbox or an execution barrier by itself. The next
reviewed human executor must enforce actual macOS/arm64 and pinned tool/runtime
identity, a fresh credential-free environment/Docker session, source/config
revalidation, bounded owned child/container handling, exact frontend extraction,
image/archive integrity and closure BEFORE separate credentialed publication.
OS/same-user integrity remains trusted. Publication, migration and deployment
are separate human phases; none is authorized by this source/input helper.

## Independent review 53 — corrections pending new exact review

Review53 of `a6c727f257204cd55d762578529dfd871b7649e5` returned FAIL:
two P1 findings (macOS path aliases/nested Nixpacks output; missing final local
controller HEAD/origin observations), and one P2 (Unicode controls/surrogates).
The failed original review remains evidence; prior passing tests did not cover
these defects. Review52's connection-reset record and unavailable response are
also retained and not represented as a code verdict.

Source names now use conservative Unicode NFD/case-folded comparisons before
any workspace creation. Canonical `.git` and `.nixpacks` components are forbidden
at every depth, canonical top-level `public` is forbidden, and duplicate or
file-parent aliases refuse. Original names and exact required source anchors
remain unchanged. Public values and source paths require printable Unicode,
rejecting C1/format controls and unpaired surrogates.

Human verification now reads local controller HEAD and exact origin again
after the source/GitHub observations and before its final clean-status check.
Existing protected-master and configured-user checks remain. This is sequential
observation, not an atomic filesystem lock; later privileged boundaries still
require their own revalidation under the approved no-concurrent-release rules.

Ten new/affected methods PASS: four source-decision cases in0.001s and six
build-input cases in0.020s. This includes case-only and Unicode-normalization
aliases, canonical file-parent collisions, nested generated paths, Unicode
controls/surrogates, valid printable Unicode, and clean checkout/origin switches.
Ruff/format, explicit-interpreter Pyright (zero errors/warnings) and whitespace
checks PASS. No unrelated suite, Nixpacks/Docker, account, human consumer or live
operation ran. The corrected exact commit requires a new independent review.

Review54 of `e77ab290732954cb31054625cb2c91023a55693b` returned FAIL for one
remaining P1: implicit parent directory aliases with different child filenames
could collapse even though complete member names were distinct. Preparation now
tracks every cumulative canonical prefix and requires one original spelling per
prefix, before any write; prefix count is bounded at20,000. Complete-member
duplicate and file-parent checks remain. New host-independent regressions cover
case and Unicode-normalization aliases without explicit directory members, nested
aliases, and a positive case with multiple children using the same exact spelling.
This further correction requires its own final exact independent review; previous
FAIL decisions remain retained, not relabeled as passes.
Five new/affected cases PASS (four in0.019s, new prefix-count boundary in0.007s),
with Ruff/Pyright/whitespace checks clean. Corrected source validation also accepts
the same actual1,298-file proposed candidate archive read-only; no build occurred.
