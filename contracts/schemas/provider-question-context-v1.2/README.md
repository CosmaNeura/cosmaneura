# Provider question selected reviewed sources v1.2.0

Opt-in source-only extension: request tool_version=1.2.0 and
detail=reviewed_sources. All other v1.1 fields, headers, reviewed-source bounds,
provider/encounter authority and final-source fences apply. v1.0/v1.1 requests,
results and receipt digests remain unchanged. No generation is started.

The result's state describes **source availability**. READY means the current
selected reviewed sources can support a question, even when preparation failed
or is pending. The added brief_state independently describes optional generated
preparation. With brief_state=ready, prepared_brief and generated_at are present;
with pending/unavailable, both are null and reviewed_sources remain available.
current_through is the latest included source date. UNAVAILABLE means no eligible
reviewed sources: reason_code=no_eligible_sources, brief_state=unavailable,
completeness=none, and every content/provenance/date field is empty or null.

The immutable turn receipt binds the exact version/detail, complete selected
source content/provenance/digest and initial optional brief state. A later model
completion cannot upgrade the same read receipt. A new question can use the new
brief. Source editing, explicit patient-association retargeting, provider
revocation and changed selection invalidate final acceptance and replay.

Final acceptance locks the bounded reviewed appointments, selected note rows
and explicit Scribe patient identity rows with NOWAIT, then revalidates the exact
current reviewed selection. Existing provider-confirmed canonical associations
are honored while original capture patient/appointment provenance remains fixed.
Same-name unmatched patients and other doctors' sources never enter selection.

Deploy the receiver before the consumer. Unsupported versions return the same
404 RESOURCE_NOT_FOUND response before creating a receipt; only that exact
unsupported response permits downgrade to v1.1 or v1.0. Authentication, source
conflict and platform failures must not trigger downgrade. This is selected
reviewed-note evidence, not a full chart, raw transcript or EHR integration.
