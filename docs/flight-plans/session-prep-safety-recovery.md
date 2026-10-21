# Session Prep safety-rule diagnostics and bounded recovery

Tier 2: this increment handles generated clinical-brief rejection. It changes
neither the four safety patterns nor their check across every output field.

Problem: distinct safety rejections currently share one failure code and one
general regeneration prompt. The measured staging failure was
`output_safety_rejected`; its rejected text was not retained, so its matched rule
and wording remain unknown.

The worker records closed `output_safety_01` through `output_safety_15` codes.
They encode a mask: diagnosis assertion = 1, leading action = 2, medication
action = 4, emergency deferral = 8. Combined matches retain every applicable
rule. The existing error family stays `output_safety_rejected`; no matched text,
field name, offset, source value or model response enters diagnostics.

The existing second call receives fixed instructions for the matched rules and
the unchanged original reviewed-source payload. It receives no rejected draft.
Two model calls, zero SDK retries, the shared 50-second budget, lineage checks,
fidelity checks and source revalidation remain required. Prompt version v5
prevents prior-version artifact/receipt replay. Failed drafts remain unpublished.

Validation: focused offline generator/fidelity/model tests and synthetic safety
cases; exact worker generation-exception boundary executed with an in-memory
seam. No database, vendor, browser or cloud calls. Independent review must bind
the final pushed head before merge; measured test evidence is linked in the PR.

No dependencies, frontend, migration, configuration or deployment changes.
Runtime health counts still include these failed builds; its existing
`no_eligible_sources` exclusion is unchanged. There is no new telemetry payload.
Recovery is a separate reviewed image rollout using the previously accepted
Core image/source; prompt/source identity checks must remain accurate. This PR
does not authorize rollback, deployment or migration execution. A live safe
brief on the updated runtime is still required; these tests do not establish
that the observed staging failure is repaired.
