# Staging runtime: bounded namespace-prerequisite test

Tier 2. Prepared September 14, 2026. Source/image proposal only; not an execution
receipt, infrastructure plan, platform acceptance or authorization to deploy.
Foundation apply already completed. PRs #385–#387 merged normally at protected
base `e4a18957ca4aad405d79e44b95a59815197df301`; they are not reapplied here.

## Decision and scope

Test the first necessary condition for the proposed offline-verifier launcher:
can an unprivileged, exact-image process in the selected Azure Container Apps
context create its own user/network/mount namespaces and request a child PID
namespace? A denied prerequisite stops this approach before further runtime
implementation. A positive result permits further design work, NOT acceptance.

This PR adds a standalone, synthetic-only executable and mock tests. It does
not wire the probe into a workflow, API, restore adapter, collector or consumer;
does not alter any runtime-policy flag; and does not supply an apply command.
The existing direct `gh` launcher still lacks the required containment boundary.
No actual Linux probe, verifier, container workload or Azure job has run.

## Exact artifact and execution method

The source-adjacent `infra/runtime_capability/image-candidate.json` records the
implementer-verified OCI descriptor/config/layer closure and copied-file hashes.
It now includes complete config/manifest contents, diff IDs and the exact
image-input source head `95cc148479340f3bae461d82af9178070c3414cc` separately
from the base. This is NOT independent artifact verification or acceptance.

- Probe image manifest: `sha256:66c56cb1090d04483fc04bc18e61c68a6d3dedefd090a146d4ff7419a30e7233`.
- Config: `sha256:e8abc9f994ac32f533c33a464683e5228925493891cc37360d0184af6e343a08`.
- Platform: Linux amd64. Image user/group: `65532:65532`.
- Fixed argv: `/usr/local/bin/python3 -I -S -B /probe/staging_runtime_capability_probe.py`.
- Python executable: `d7fc81b2f93779e4278fc7b928190d5e86d52b6c8076706512dc567874adb262`.
- No arguments, overrides, input files, network destinations or caller commands.
- Image is local only: not published, attested, scanned or accepted for execution.

The base is the exact amd64 child of the digest-pinned Python 3.12.11 Alpine
3.22 base already used by the repository's network-smoke image. This standalone
stdlib-only diagnostic is NOT the Python >=3.13 application runtime. Its age,
security posture and exact registry/ACA pull compatibility remain execution-
review considerations; reuse is not a vulnerability or platform acceptance.
The legacy builder emitted OCI base layers and Docker gzip COPY-layer media
types. Every encoded layer digest and uncompressed diff ID was verified.
No MIME conversion or registry acceptance is inferred.

Local assembly used a macOS arm64 CLIENT with bsdtar 3.5.3 / libarchive 3.7.4,
and an existing Colima Linux arm64 Docker Engine 29.5.2 DAEMON. The target was
explicit amd64, with a three-file tar context. There are no Dockerfile RUN instructions,
build secrets, external COPYs, installs or application dependencies. The
builder's intermediate metadata containers were not workload executions.
No emulated or native probe execution was used as Azure evidence.

Corrected reassembly recipe (build-only, NOT a run/handoff) uses `git archive`,
not client-specific tar options. Explicit `tar.umask=0022` is essential: the
default archive mask yielded group-writable source and that candidate failed
inspection. This corrected invocation was exercised on the recorded macOS client:

```sh
git -c tar.umask=0022 archive --format=tar \
  95cc148479340f3bae461d82af9178070c3414cc \
  scripts/staging_runtime_capability_probe.py \
  scripts/staging_offline_verifier_diagnostics.py \
  infra/runtime_capability/Dockerfile |
  docker build --platform linux/amd64 --network none \
    -f infra/runtime_capability/Dockerfile -
```

A rebuild can have a different manifest due to metadata. Use the retained
exact archive; never substitute a rebuild or moving tag for its reviewed pin.
Any publication/manifest conversion must preserve or newly review the actual
registry digest before a later exact execution proposal.

Separately, the official Linux amd64 `gh` 2.97.0 archive checksum was verified
against release metadata and the checksum file. Its streamed executable is a
statically linked x86-64 ELF, SHA-256
`141507c337e8b202ad398550c3b73d72f5af92e86f71665214538a81efd4c409`.
That is NOT the archive checksum. `gh` was not executed and is intentionally
absent from the probe image; a future verifier-runtime image needs its own pin.

## Bounded implementation

1. Require exact Linux x86-64 and real/effective/saved UID/GID 65532; reject
   root, unexpected supplementary groups and extra CLI arguments.
2. Bound descriptor enumeration to 256 entries and close inherited descriptors
   above stdio before creating an anonymous pipe. Do not use the current hard
   rlimit as an upper bound on already-open descriptors.
3. Fork exactly one fixed child; never exec a caller-supplied program. The child
   closes stdio, clears its environment, disables core/file output, limits CPU
   to five seconds and address space to 128 MiB, sets parent-death SIGKILL and
   checks the setup race. Parent enforces a 25-second result/exit deadline.
4. Snapshot this child's own user/net/mount namespace identities BEFORE any
   mutation. This is a before/after comparison, not trust in container PID 1.
5. Set no-new-privileges; create a new user namespace; map only 65532 to itself,
   with mandatory `setgroups=deny`. Private-user-namespace capabilities may be
   present during setup; no host privilege, root UID, helper or fallback is used.
6. Create a new network namespace and observe exactly one kernel-flagged
   loopback interface, down. The fixed diagnostic uses local ioctl only: no
   DNS, bind, connect, listen, send or remote endpoint.
7. Create a new mount namespace; request a new PID namespace for FUTURE
   children. There is deliberately no grandchild or mount mutation here.
8. Drop the entire private capability bounding set, clear ambient capabilities,
   zero the v3 capability sets using x86-64 capset syscall 126, then verify
   nonzero UIDs, all capability sets zero and no-new-privileges in proc status.
9. Emit one finite-schema report, under 4096 bytes, to the parent pipe. No raw
   IDs, proc data, errors, identity values or operational logs are serialized.
   The parent requires both the fixed outcome schema and a clean child exit.
   On failure it kills only that exact child and retries nonblocking reap for
   up to two additional seconds. Failure to reap remains exit 3 and explicitly
   relies on the external backstop; total result-plus-cleanup budget is 27 seconds.

Exit 0 means only `prerequisites_observed`. Exit 2 requires an explicit
prctl/unshare/capset denial or unavailable-syscall return at a kernel-operation
phase. Internal errors, imports/parsers, proc observations, identity-map/context
failures, resources and cleanup are `probe_error`/exit 3 (or no report/exit 3),
even when their errno is EPERM. They are NOT namespace-denial evidence. Every JSON outcome
permanently includes `diagnostic_only=true`, `runtime_acceptance=false` and
`activation_authorized=false`. No verdict authorizes another invocation.

The outer disposable-job deadline is a REQUIRED backstop if a child is stuck
in uninterruptible kernel sleep. Parent-death signaling and nonblocking reap
are not substituted for full cgroup/tree cleanup proof.

Independent review20 of the initial source/image proposal returned FAIL. The
four source/documentation findings are corrected locally; exact re-review is
required. Its image-artifact finding remains OPEN: the reviewer received
metadata, not the full archive. Implementer hash checks are not independent
artifact acceptance. The complete retained archive needs an authorized
independent inspection path; the current review transmission cap is 500 KB.
Do not close that finding by relabeling metadata or by another implementer run.

## Proposed Azure envelope — NOT deployable configuration

Use a separately approved disposable, synthetic-only Container Apps Job in the
exact approved executor environment. Do not run in an existing application/PHI
environment merely to avoid provisioning review. The current disabled empty-
environment source creates no executable probe by itself.

Candidate API envelope: Microsoft.App/jobs `2024-03-01`, Manual trigger,
`replicaTimeout=60`, `replicaRetryLimit=0`, `replicaCompletionCount=1`,
`parallelism=1`, one container, 0.25 CPU / 0.5 GiB, no sidecars, volumes, mounts,
app secrets, workload managed identity, input/env/command overrides or schedules.
No privileged flag, securityContext, extra capability or unsupported API field.
These configuration keys were checked against the published Jobs schema.

Before it is runnable, bind the exact reviewed source, final registry digest,
subscription/tenant/operator identity evidence, region, environment resource
and platform revision, private image-pull mechanism, network policy, job
configuration and bounded capture/cleanup path. Registry pull credentials must
remain outside the workload; do not inject application credentials or grant
workload identity to make an image pull succeed. No registry has been selected
or published to by this PR. Egress and DNS controls must distinguish platform
traffic from workload traffic; internal ingress is not an egress control.

Fresh exact plans, required independent plan/handoff review, current identity
and drift evidence, expiry and human execution are still required. This PR's
source review does not review an unbound Azure context. Do not prepare a
ten-minute handoff until the user confirms current Terminal presence and all
preceding prerequisites are ready. Never reuse consumed foundation artifacts.

## Decision after an authorized observation

| Result | Next permitted conclusion |
| --- | --- |
| First namespace prerequisite denied | Preserve exact image/platform/failure evidence; this method cannot proceed in that observed context. Propose architecture options for an explicit decision; do not weaken controls. |
| All prerequisites observed | Continue separately reviewed read-only-root, writable-mount, actual child-PID/cgroup, inherited-FD and adversarial descendant-cleanup tests. This is not proof those requirements work. |
| Missing report, timeout, context or pull failure | Diagnose the bounded failure. Do not call it namespace denial, retry automatically or claim platform support. |

Only after full containment proof can the actual offline `gh` launch be bound.
Executor and image-publisher evidence require disjoint namespace/filesystem/
PID-cgroup/independent-review records and distinct build provenance. Publication
transport remains outside the offline publisher-verifier namespace.

## Verification, recovery and follow-on milestones

The new probe module contains 193 synthetic cases exercised across focused
runs: 143 initial cases and 50 new review regressions. The correction run passed
126 affected/new cases; the changed fixed-consumer guard passed separately.
The image/source binding is rechecked after each artifact change. No actual
fork, namespace, resource-limit mutation, image execution or network call occurs
in those tests. Pinned Ruff 0.11.6 and Pyright 1.1.399 pass. Existing passing
diagnostic/application tests were not rerun locally; normal new-head CI remains
separate. Exact independent final-source/image-proposal review is required.

Recovery for this dormant source-only change is to leave it unused or revert
its additive files normally. No policy, environment or live-state rollback is
needed. No migration rung applies. A future job has its own reviewed cleanup;
do not delete foundation resources, claims or evidence as probe cleanup.

Dependency order remains: feasibility → full containment implementation and
proof → fresh human runtime provisioning → synthetic restore/migration rehearsal
→ staging release and live Clerk acceptance → Zoom callback/capture/revocation
and alert checks → the required 30-minute observation. This proposal completes
none of those live acceptance milestones and changes no existing approval gate.
