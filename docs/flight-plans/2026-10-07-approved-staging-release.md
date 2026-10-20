# Approved staging release — October 7, 2026

Risk tier: 0 for this documentation record. The owner approved the reviewed
Core PRs #686–691 and Scribe PR #270 and instructed execution in this conversation.
This record binds the current reviewed build controller to that selected release.

Core application candidate: `8a5597eb47bcdb354d0c12c00d458ee7f451d5cb` (merge of #691).
Application tree: `045e9c48246e19f0240c33b43321b77d5106c517`.
Scribe candidate: `a4419f92656b72186212a02cb3a3f510a8c471bc` (merge of #270).
Scribe tree: `3eb3bd9cc2eadc950953b038cd147d19b08454d1`.
Both trees match the reviewed integration trees. Core #677 and Scribe #271 are deferred.

Local implementer review and applicable checks replace hosted status and approval
counts under the owner's September 25 policy. Actions was disabled in both
repositories when the batch merged. No workflow was dispatched. This record
changes no executable input, workflow, schema, configuration or access.

Reuse the completed focused checks where their inputs are unchanged. Core had
64 applicable earlier focused checks plus the reviewed image-probe check and three
cleanup checks; Scribe had 48 passing focused Python checks, 25 database-dependent
skips, seven native runner checks and 476 files passing Ruff. These are local
checks, not hosted CI. Implementer review is not an independent review or GitHub
approval. Retain the PR-specific reported broader results with their original
attribution rather than rerunning entire suites for each PR.

Build and publish the exact selected application candidates through the approved
local staging route, qualify the grouped release once and deploy Scribe receivers
before Core. Database heads remain Core `211aefdf709b` and Scribe `0024`; no new
migration is selected. Preserve existing workload identities, configuration,
reviewer membership and the temporary exemption. Retain prior immutable images
for recovery; additive database structures stay in place.

Stripe activation is a separate guarded staging test-mode step after deployment,
fresh account/retired-writer checks and the elapsed waiting period. Live payments
remain off. Production is unchanged. Mac build 47 and iPhone build 61 may be reused
only after verifying their native build inputs are unchanged; signing, notarization
and an actual usable TestFlight invitation remain separate distribution evidence.

Validation for this record: inspect the single documentation diff, run
`git diff --check`, verify the change budget and verify the merged controller's
non-documentation tree matches the exact reviewed head. No tests apply to prose.
