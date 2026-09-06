# Flight plan: dormant controller-receipt-v2 production authority

- Tier **2** adds a downstream companion policy, an exact two-record
  intent/submission private-WORM prefix reader, and a sealed provision-only
  authority. It is stacked on private-reader base
  `1edf3832c082d260c7a0799f112782b296d96b29` (merged PR #315).
- The public data plane accepts only authenticated provision launch material.
  It accepts no caller action, rehearsal, path, URI, mapping, or policy
  selector; it requests one managed-identity token and performs exactly two
  fixed GETs in intent/submission order.
- Exact launch record, authenticated broker receipt, runtime record/signature,
  provision job image/configuration, execution, source, identity, request, raw
  prefix bytes, digests, WORM metadata, and non-regressing clocks are rebound
  before, between, and after I/O.
- The result asserts only `record_source_authenticated=true`. It explicitly
  keeps controller-receipt existence, provider facts, runtime authority,
  writer identity/private route, and executability false.
- ARM/network/database observation, controller builder/writer, downstream
  consumers, delete authority, sanitizer mutation, workflow integration,
  infrastructure, deployment, and all live-provider calls are out of scope.
- Current deployed state, feature-flag rollout, schema/data sequencing, canary,
  and PHI rehearsal are **N/A**: the public factory is disabled and this slice
  changes no deployed behavior, cloud resource, credential, schema, or data.
- Hold after focused/adjacent tests, Ruff, Pyright, compileall, policy/hash and
  diff-budget checks, independent exact-head review, protected CI, and merge of
  PR #315. Recovery while dormant is a code-only revert.
- Later reviewed slices must add authenticated provider observation before a
  builder/writer, then narrowly scoped consumers, workflow wiring, an approved
  staging rehearsal, and a separate intentional activation decision.
