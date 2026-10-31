# Legacy recorded-note source fidelity

Risk tier: **Tier 2**, patient-output path. Local source preparation only; independent review, public source approval and required CI remain pending.

## Problem and outcome

Ordinary uploaded recordings use Core's legacy note builder. Its source-only instruction conflicts with the default requirement to infer a clinical purpose for every plan item. The role text also speaks as though the model conducted the encounter. Saved fictional drafts show unsupported rationale and attestation claims; exact model/ASR/template causation is unproven.

When `require_plan_reason=True`, include only a purpose explicitly recorded and leave an absent purpose unstated. Preserve the setting, action content, conditions, attribution, terminology and other numbered rules. Legacy role wording describes an unreviewed draft from recorded material. A final source constraint follows template/example/custom text: those inputs cannot authorize system participation, personal review, signing, attestation or medical-necessity claims. An explicit clinician assertion remains attributed source speech.

Indexed capture keeps its existing rendered role text and grounding; its effective `require_plan_reason=False` branch and capture/Scribe source files remain unchanged. No route, flag, schema, model, generation parameters, authorization or finalization/retention flow changes. Returned model JSON is preserved without post-generation appending or repair.

## Qualification and limits

- Fifteen new pure real-function cases exercise four accepted185 indexed prompt baselines, complete synthetic source inputs, terminology, absent/stated purposes, template/example/custom constraints and fake-SDK result/parameter preservation.
- Seven existing augmentation cases verify recording duration does not become care-time evidence and that augmentation adds only the AI-assistance disclosure.
- The historical default numbered block stays intact in the existing test as a baseline; only its inferred-reason rule is replaced in the expected new prompt.
- These tests use fixed repository AST functions and synthetic settings/fake SDK. No application, DB, external model or actual patient authority is exercised. Normal existing DB-backed customization tests and required public CI remain separate.
- Prompt instructions do not guarantee hallucinations are eliminated. Current fictional drafts still need actual supported correction and review; six Session Prep answers, citation opening/persistence and live authority acceptance are separate gates.

## Release and recovery

Publish only after independent final-source review and source authorization, then normal required CI. Deploy a reviewed merged candidate through existing release controls after actual package/desired-configuration/clinical gates; this plan authorizes no runtime action. No migration or configuration change is required.

Recover by reverting this complete four-file source outcome and releasing a reviewed candidate through the same controls. Existing notes/review snapshots remain immutable; neither rollout nor rollback silently regenerates, repairs or marks historical/current drafts reviewed. A source rollback restores the prior prompt limitation and must be reported honestly.
