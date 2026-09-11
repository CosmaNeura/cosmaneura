# Staging rehearsal base recovery closure — 2026-09-11

Classification: non-PHI infrastructure metadata. Digests and aggregate facts
only; no raw state, saved plan, provider body, credential, log or database data.

## Disposition

The human-operated recovery apply completed: **11 added, zero changed, zero
destroyed**. Recorded post-apply verification closes the incomplete reversible
base documented in the [September 4 record](STAGING_REHEARSAL_BASE_PARTIAL_APPLY_2026-09-04.md).
This closes **base recovery only**, not the staging workstream or any later
action-time approval. The consumed saved plan and handoff must never be reused.

- Reviewed source: `85f2dae47af996bf69ec2a9a5a47a54517b4bf9a` (merged PR #379).
- Human claim recorded at `2026-09-11T14:05:35.463078Z`, before its handoff
  expiry; the successful apply completed around `14:07:15Z`.
- Post-apply state captured at `2026-09-11T14:12:41.260609Z`: serial **13**, all
  **39** expected instances (37 Azure objects and two `terraform_data`).
- All **28 original complete attribute maps are unchanged**; the **11 added
  configurations** match the reviewed source and recorded provider readback.
- Primary ARM capture: **78 GET operations**, completed
  `2026-09-11T14:38:15.046780Z`. Seven private containers, exact private
  endpoint/NIC/DNS/subnet graph, ten control-group ordinary resources, and
  **zero target-group ordinary resources** passed the closed-world checks.
- Effective RBAC supplement: **11 scopes**, completed
  `2026-09-11T15:19:31.536180Z`. Direct and inherited assignment sets match;
  eight inherited tuples remain unchanged. Five owned custom roles are exact;
  two unrelated subscription custom roles were observed, not claimed absent.
- No active legal hold, WORM policy, management lock, marker-create grant,
  executor, restore, migration, cutover or deployment was established by this
  recovery. Provider `Deleted` immutability-policy tombstones with zero days
  are recorded absence, not active retention.
- **85 negative regression cases** pass: 48 ARM, 12 RBAC, 16 state-check blocks,
  nine claim checks; all five positive cases pass.

## Exact evidence bindings

| Artifact | SHA-256 |
| --- | --- |
| Consumed recovery saved plan | `af89ec31aaa3c4ac4a75e7c963f2de91eb3f0fffe980a062ee856e6608c613d1` |
| Post-apply state, serial 13 | `9dc733ff79bea67570ee5eb57ac281396c7198a8cbea4ecfa813acbf00376004` |
| Primary ARM capture v2 | `0aa2660273a0c4d7be91d140e90a55d3526e12ec0a73085b1bd8944691b4edbe` |
| RBAC supplement v1 | `d885c12cf3bd790068b192f890250ff47fd6179e9261ca1050d5394c8f8b76de` |
| Final state/claim verifier v5 | `81e29a29ff69b29d085b4f2dc6b501caaf32a4a4fa59a3af83f52f69f8888643` |
| Exact current consumed claim | `a121ae8c9d8e7183ebdea12a86470078505db83fc9f06c3847ef2d9edff40929` |
| Final independent review response | `ece944eb527f55c3245edbc8aab84f6a27e6510409155c295cda8f811a2d8d85` |
| Private archive closure manifest v1 | `11a9e41e7154cf6f8cbcc5785e26ba67143185a01a2388938da1d204e3008ff9` |

The owner-only, non-Git recovery archive contains 41 hash-verified artifacts
and this closure manifest. It contains no credential cache or execution
runtime. Copied claims/logs retain their original file identities in the
manifest; archive-copy inodes are not execution evidence. The archive is not
a retry packet and raw artifacts must not be attached to a PR.

## Independent review and limits

The authorized `codex-auto-review` deployment completed final review at
`2026-09-11T15:48:30.470200Z`, response
`resp_0a2cff555e4513ca006aa422a641988195a86c6cc8b2c4605e`.
Verdict: **qualified pass for the historical closure, pass for the final claim
delta, no remaining actionable P0/P1/P2 findings**. Prior actionable coverage
and claim-shape findings were corrected and regression-tested.

These qualifications remain part of the result, not deferred claims of proof:

- Captures are historical, non-atomic observations, not future authority.
- The primary ARM capture binds the reviewed CLI account-name SHA only; it
  does **not** establish immutable-principal-OID attestation. The later RBAC
  supplement binds only its own current token/home-account/resource-tenant
  identity, not the earlier capture or human apply.
- The consumed claim is exact-schema version 1 with `apply_outcome="unknown"`
  and matches the first recorded **post-apply** digest. This is **not** a
  cryptographic non-rewrite attestation from creation or a pre-apply seal.
  Successful apply is established separately; verification did not rewrite
  the claim or change its recorded outcome to success.
- Independent review received code and aggregate/digest summaries, not raw
  provider bodies. It is not independent provider attestation or GitHub approval.

## Remaining work and authority

Trusted restore/sanitizer/private-executor bindings, approved live rehearsal,
staging cutover, Auth #238 acceptance, Zoom #239/#251 validation and intentional
production promotion remain unfinished. Registry/network source and mock tests
do not prove live runtime readiness.

Every later operation requires its own fresh identity, state, exact-source,
plan and provider evidence. Human-only Azure applies and founder-only manual
staging/production boundaries remain unchanged. Nothing in this historical
record authorizes another apply, retention, restore, runtime or deployment.
