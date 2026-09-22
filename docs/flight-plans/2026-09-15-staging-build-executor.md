# Bounded staging build executor

Tier 2: local build execution and artifact/credential boundary. This adds a
library, not a release command, identity authenticator, publisher or deployment.
It reuses PR416's merged source/public-input primitives and the existing Colima
engine; no cloud builder, infrastructure, identity or dependency installation.

## Authorization and integration

The founder approved scoped actual staging build/deployment in the same task on
September 15. An agent-operated route must record delegated agent execution
truthfully, not impersonate a human or forge GitHub workflow variables. This
library accepts only an externally authenticated reviewed controller: the
callback/dataclasses/source hashes are NOT credentials or authority. No default
callback or live CLI exists. The separately reviewed controller must bind the
founder-selected merged candidate, current protected controller, authenticated
public configuration and runtime, explicit actor/action scope and expiry.

Generic build/deployment approval must not be requested repeatedly. It does not
select a candidate, merge future PRs, approve production/new resources/IAM,
authorize arbitrary data changes, override a refusal or permit automatic retries.
The existing human-only snapshot/firewall/identity/handoff path is unchanged.
Actual snapshot and ONLINE migration rehearsal, exact release review and fresh
identity/expiry controls remain release prerequisites. No live operation is
authorized merely by merging this library or obtaining its source review.

## Exact build path

1. Revalidate PR416's prepared private source, archive/config hashes, every file
   and directory, native Nixpacks1.41.0, pinned Docker29.7.2/buildx0.37.0/ps bytes,
   Darwin/arm64 and previously reviewed owned socket/engine binding. The separate
   controller must disable core dumps for its process before calling the library.
2. Use only the owned HOME, Docker config, plugin path, TMPDIR and explicit public
   build environment. No inherited token/credential variables, Docker context,
   shared user config, login, shell, registry push or Azure calls. The trusted
   OS/runtime can add platform metadata; this is not a hostile-code/host sandbox.
3. Check exact random image/container names absent, then build JavaScript for
   linux/amd64 using the fixed staging/public inputs, no cache and source labels.
   Builds may fetch public dependencies; they do not receive registry credentials.
4. Create one owned stopped, read-only, network-none container by immutable image
   ID; never start it. Validate exact ownership/state/no volumes before copying
   its client tar. Enforce bounded POSIX/PAX paths with the reviewed component
   trie, reject aliases/links and require index.html. Write only fresh public/.
5. Recheck and remove only that exact owned stopped container ID without force,
   prove absence, then build the backend using the generated assets. Preserve
   labels/public configuration, architecture and image-ID evidence.
6. Save that immutable image ID to one exclusive private archive, recheck the
   engine/image/source, hash the complete archive and bind its Docker manifest to
   the image-config digest. Layer decoding is NOT proven by this metadata check;
   a separate publisher must load/verify the archive's actual image/layer identity
   in a credential-separated context before publication.
7. Each command owns one process group, bounded output and deadline. Preserve the
   unreaped leader until group cleanup; never signal a recycled PID. One original
   five-second cleanup allowance, one cancellation signal, no retry. Timer teardown
   drains its pending alarm before restoring the caller's handler and mask.

Maximum build budget is one hour; actual controller chooses a reviewed bound.
Output limits: 64MiB/log, 256MiB/assets, 16GiB/image. Existing private source and
binary are revalidated around commands, not treated as an atomic hostile-OS seal.
The build environment, engine and public source may not change during the attempt.

## Failure, evidence and recovery

No image deletion, cache prune, daemon restart, overwrite or retry. Failed or
ambiguous attempts preserve private logs, partial archive, images and owned
container ID for a separately authorized recovery decision. Killing the local
Docker client does NOT prove the daemon's BuildKit operation stopped. A failed
cleanup, changed source/runtime, collision or expiry refuses acceptance.

build-result.json is evidence only: publication_authorized and deployment_authorized
are always false. A later deadline-teardown failure can coexist with that file;
the controller MUST require the actual successful call outcome and reject any
build-failure.json. Neither result file nor BuildArchive can authenticate a future
publication/deployment. The publisher owes its own fresh exact plan, archive,
runtime/identity and immutable remote-manifest checks. No mutable latest tag.

## Validation and deployment status

All Docker/Nixpacks operations in the executor tests use invented fixtures. The
first 14 methods passed (0.461s); they were not replayed unchanged. Two additional
archive/pending-alarm methods passed (0.003s). Static type narrowing and equivalent
dynamic test imports corrected the initial type errors; no passing batch was
claimed for the earlier shell batch whose trailing wc masked those errors.

Two new native Python-child tests exercise owned cwd, explicit environment,
stderr discard/merge and actual process-group closure; no Docker/network/credential
operation. Their first assertions rejected Darwin's generated encoding variable;
the corrected exact UID/encoding expectation passed both in 0.042s. That initial
failure remains evidence, not a passing test. Production behavior was unchanged.

Independent final source review and current-head validation evidence are required
on the PR. No actual native builder/image/registry operation or staging release has
been performed. Recovery before activation is to leave this additive library
unused; do not delete data, existing containers or staging resources.

## First Cursor review dispositions

Bugbot reviewed06ac0db6 and found two medium-severity issues (PR417 discussions
4016980471 and4016980483). No PASS is claimed for that head. Creation stdout now
retains a valid observed container ID in a finally block BEFORE post-command
validation, including a child cleanup/deadline failure. Failure evidence also
retains the intended unique name; neither is ownership proof or removal authority.
Missing/malformed output preserves that uncertainty, not a guessed ID.

Failure-evidence writes are best-effort and cannot replace the canonical refusal.
The raised refusal exposes whether that write succeeded; absent evidence is never
success. Preserve partial files, no write retry/overwrite. These fixes need a new
independent exact-head review; the first review and its findings remain recorded.
