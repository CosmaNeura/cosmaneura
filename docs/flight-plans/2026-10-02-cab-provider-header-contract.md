# Validated provider billing-header view contract

Tier 2. This browser-side prerequisite validates the completed-note CAB response before a provider view may display or edit it. It adds no endpoint, identity, migration, activation, or clinical inference.

The response must bind the prepared Core operation, original content hash, projected-note hash, CAB source pin, current header and edit revision. Evidence uses Unicode code-point spans and must exactly match the retained projected note. Code proposals retain their original engine basis; human edits clear primary-order confirmation when diagnosis proposals change. CAB remains the authority for save validation and coding rules.

The subsequent inline provider view consumes this parser through the authenticated Core facade. Test fixtures are synthetic and never authorize a workload or model request. Local validation checks wrong operation/source/evidence rejection, preserved source content, explicit proposals and export provenance; actual CAB accept/generated/edited DTOs are separately parsed. Recovery: revert the unused prerequisite or its consumer without removing stored header/source history. No deployment or real-patient cohort expansion is included.
