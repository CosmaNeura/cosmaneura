# Dormant offline-verifier Linux diagnostics

Tier 2, source-only. Base: `e21ba4c1fb1027de8123aca9b8aee25b8a11b612`.

## One outcome

Prepare a bounded read-only observation library and synthetic tests for the
existing offline-verifier containment requirements. No production/executor path
imports it; there is no CLI, deployment workflow, policy mutation or activation.
No cloud job, Docker container, verifier or real kernel capture runs in tests.

The current adapter seals `gh` bytes and bounds its process/output but does not
create its required no-network namespace. Azure Container Apps' exact Jobs API
`2024-03-01` exposes no securityContext/capability/read-only-root controls, and
Microsoft disallows privileged containers with host-level access. This does
not establish whether unprivileged namespace syscalls work on the selected
platform. These diagnostics do not attempt those syscalls or solve containment.

## Observation boundary

- Read only allowlisted bounded `/proc/thread-self` status, root mount and
  cgroup metadata; compare its network namespace with `/proc/1/ns/net`.
- Observe all four UID slots, all five capability sets, NoNewPrivs, optional
  seccomp mode, root mount access and cgroup-v2 membership. Cgroup membership
  is not evidence of a PID limit or process-tree cleanup.
- Intake current interface names from a byte-bounded `/proc/thread-self/net/dev`
  read, rejecting truncation, malformed counters and more than 64 interfaces
  before opening a control socket. Never call unbounded `socket.if_nameindex`.
  Read flags through a local kernel
  ioctl. Do not trust `/sys/class/net` as the current namespace's view after a
  namespace change. The temporary control socket sends no network traffic;
  denied socket/ioctl access becomes unknown rather than a weaker fallback.
- Classify loopback identity from `IFF_LOOPBACK`, never its name. A sole
  non-loopback named `lo` is not favorable evidence; a renamed real loopback
  is classified by its flags. Zero or multiple flagged loopbacks leave the
  loopback-down observation unknown. This is still not isolation acceptance.
- Return booleans/count-free classifications only: no raw UID, cgroup path,
  namespace token, kernel file content or OS exception text leaves the report.
- Missing, malformed, oversized, duplicate or ambiguous observations become
  explicit unknowns. Do not retry, infer success from missing access, or obtain
  broader privileges to observe a value.
- Validate the mandatory mountinfo fields, encoded paths, IDs, unique options
  and known propagation tags before reporting root access. Unrecognized formats
  conservatively become unknown; this is not a universal filesystem parser.
  Validate cgroup hierarchy/controller uniqueness and v1/v2 field relationships.
- Import Linux's ioctl helper only when reading interfaces; unsupported-platform
  import succeeds and the public capture entry point refuses with a fixed code.

Every report permanently says diagnostic-only, runtime-acceptance false and
activation-authorized false, even with entirely favorable facts. This report
is not sealed, authenticated, atomic or trusted authorization input. PID 1 is
not a trusted outer-namespace identity. Root-read-only does not attest every
submount or input/output path; interface flags do not attest inherited sockets,
future state, or all possible communication. Existing independent capture,
image/platform fidelity, full filesystem/process containment and review remain.

## Verification and next step

Run only the new secretless synthetic test module with `--noconftest`; project
application/database fixtures must not load. Cover all capability/UID slots,
malformed and unavailable procfs metadata, namespace ambiguity, loopback up/down,
extra interfaces, bounded read-only access, local-only mocked ioctl behavior,
non-Linux early refusal and absence of workflow/consumer/activation wiring.
Run focused Ruff checks. The exact final source requires independent review.

After that review, any real capture requires a separately authorized exact
Linux image/platform execution context; no such context or job is created by
this PR. A local fixture pass does not satisfy Azure runtime acceptance. A later
confinement implementation must meet the existing contract, not consume this
diagnostic as a substitute or silently broaden the process egress allowlist.

## Recovery and non-goals

Leave this module unimported or normally revert these three source files.
No resources, permissions, secrets, schema/data, retention or deployment state
are changed. Existing operator/source/plan pins are untouched. No new dependency,
privileged helper, namespace creation, network-policy change, plan/handoff
protocol, accepted-base shortcut or human approval exception is introduced.

References: [containers](https://learn.microsoft.com/en-us/azure/container-apps/containers),
[Jobs schema](https://github.com/Azure/azure-rest-api-specs/blob/main/specification/app/resource-manager/Microsoft.App/ContainerApps/stable/2024-03-01/Jobs.json),
[container definitions](https://github.com/Azure/azure-rest-api-specs/blob/main/specification/app/resource-manager/Microsoft.App/ContainerApps/stable/2024-03-01/CommonDefinitions.json).
