# Inline provider billing header

Tier 2. The Cosmas MD note button continues a provider-confirmed completed note through Core's authenticated CAB facade, then displays an editable advisory header inside the provider application. The existing clinical note, original coding suggestions and evidence remain unchanged.

The browser uses the current provider session for operation read, source acceptance, explicit generation and human edits. It receives no workload token or configurable remote destination. Opening an existing operation accepts/reads it without a model request. Every header response passes the source/evidence validator introduced by PR636; CAB owns coding and save validation. A coder signoff is displayed only for its matching saved source/revision.

Network uncertainty retains the exact command UUID and draft for explicit retry. Header edits lock navigation and dialog dismissal until saved, discarded, or resolved. Equivalent loader refreshes must preserve the mounted editor; changed source/operation/access retains the draft and prevents stale actions. Current visit details are shown separately from extracted clinical facts. Updating them requires a fresh explicit note confirmation, preserves earlier records, and generates only on another explicit action. Canceling that form returns to the existing saved header.

Release remains dormant unless Core preparation admission and both dedicated bridge channels are configured, together with CAB receiver registration, current actor mapping and source model policy. This source change neither configures those controls nor authorizes real-patient admission. Session Prep remains separate.

Local checks cover explicit generation, exact uncertain saves, revision conflicts, evidence/source validation, unchanged originals, generation-policy expiry and refresh lifecycle. The loopback Core/CAB browser workflow uses fixed synthetic provider/workload identity, real PostgreSQL storage and fake model transport. It must cover generation, editing/reload, context correction and stale operation rejection on desktop/mobile. These are technical workflow checks; clinical review, actual deployment identity and external vendor acceptance remain separate.

Recovery: disable the Core bridge flags to stop new provider actions; retain all source, action and header history. Reverting this consumer leaves CAB's stored headers accessible under its existing authority. No schema change, backfill, deployment, live outreach or environment mutation is part of this PR.
