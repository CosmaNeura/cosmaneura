# Production Session Prep panel pair

Risk: Tier 2, provider frontend and cross-repository release preparation.

The production provider consumer checks the browser's served panel bytes. Core
currently retains an older compiled panel; the provider's reviewed current main
ships a newer compiled delivery. This change vendors that exact delivery from
provider ab750355e9cb6d900ac0b30ef7d898f61a91c9c1, updates its SHA/SRI/source pin
together, and retains all older immutable assets for existing browser sessions.
It introduces no new panel behavior beyond merged provider PR107.

The paired provider source repair verifies the compiled delivery manifest rather
than comparing served bytes to readable source. Both source changes require
final independent review and normal checks. Their merges do not deploy anything.
The current Core base includes the merged Scribe PR960 subprocess repair; all
Scribe backend/runtime bytes and current configuration remain preserved.

Before production promotion, deploy and test the matched Core/provider pair in
staging, then prepare production-specific frontend settings and immutable images.
Create the dedicated provider service through the existing provider-only
foundation path; verify migration head, identity, TLS and recovery, then activate
for every active account's own visits under the founder's direction. This
source checkpoint grants no production apply or activation.

Recovery uses a verified compatible image pair; keep the additive schema and
old browser assets. Do not disable shared Authority Kernel/Agent API gates as a
Session Prep-only recovery, because those gates can also serve Scribe.

Validation: exact upstream manifest/source/delivery hashes and existing panel
asset/SRI/retention tests. Clinical acceptance remains separate; current factual
review refusal is an observed staging limitation, not resolved by this asset pin.
