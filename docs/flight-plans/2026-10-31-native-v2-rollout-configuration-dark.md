# Full native v2 rollout configuration — Tier 2, default off

Core's full native visit facade has a default-off runtime gate, but the existing
typed production rollout cannot select it. This leaf adds
`scribe_native_workload_rollout.native_visit_actions_v2_enabled`, defaulting to
false, and projects its literal value only into the existing Core API container
as `SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED`.

Selecting true requires the original dedicated workload, current provider-context
and existing native visit gates. Generic public or secret environment projection
cannot shadow this source-controlled name. Worker, scheduler, global Agent API,
Authority Kernel, Session Prep, consent, capture and Zoom controls retain their
own values. This adds no resource, service, grant, schema or credential.

The receiver-first dependency is Core PR924's closed v2 facade and Scribe PR509's
paired receiver/client negotiation. Scribe's existing supported production profile
generator already accepts the strict `native_visit_actions_v2_enabled` receiver
field and preserves it while returning disabled receiver and client profiles;
Core's existing bundle configuration carries that reviewed receiver JSON.
No separate Scribe generator change or generic environment injection is needed.

The founder owns any enabled production release decision. Before that decision,
verify the exact merged Core/Scribe pair, built artifact and installed clients,
current authority/schema/access/network state, and the bounded release cohort.
This source-only leaf performs no plan against live state, apply, promotion,
runtime change or activation. A future approved plan may add literal false to
the existing API where that setting is currently absent; do not assimilate that
delta into an unrelated production rung.

The focused provider-mocked checks cover default-off projection, explicitly
selected API-only full mode, its existing-visit prerequisite and generic-name
rejection. Independent review and exact-head required checks precede integration.
After a separately approved configuration change, recovery keeps or returns v2
to false while retaining v1 and accepted patient/private-note/retry records;
configuration rollback neither removes data nor substitutes another auth path.
