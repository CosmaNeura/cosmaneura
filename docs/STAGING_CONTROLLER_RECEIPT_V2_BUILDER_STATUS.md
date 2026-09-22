# Controller receipt v2 builder status

The dormant pure builder is implemented but is not executable, written to
storage, consumed by a runtime, or wired to a workflow. Its sole data input is
the sealed isolation-inventory authority; it reauthenticates that authority's
full production, launch/runtime, ARM, database, and inventory ancestry at one
trusted whole-second UTC time before building and validating canonical receipt
v2 bytes.

The sealed output retains the exact intent, submission, and newly built receipt
bytes; their individual and ordered-chain digests; the four authority digests;
all policy bindings; resource-set and known-inventory digests; rehearsal and
execution bindings; and the relevant observation, creation, expiry, and
cleanup times. The isolation authority remains private and in process.

This proves content construction and validation plus the upstream authorities'
narrow facts. It does not prove that a controller receipt exists in immutable
storage, that all three records came from storage, create-only behavior, writer
identity use, broad provider facts, execution history or copy absence,
sanitizer/database mutation authority, runtime authority, or workflow
integration. Persisting only the receipt bytes loses the in-process provider
provenance; a separately reviewed conditional writer/consumer boundary is
required before activation.
