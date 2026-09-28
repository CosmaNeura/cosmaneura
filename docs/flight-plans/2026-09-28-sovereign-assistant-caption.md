# Deliver the Sovereign assistant caption through Core

The dashboard should show the title **Sovereign** and subtitle **Your personal
AI assistant**. Core serves a same-origin, content-addressed panel asset, so
the Sovereign source change alone does not update the dashboard.

Tier 0: the only panel change is display copy. Core pins the exact
`app/panel.js` bytes from Sovereign PR57, commit
`690e31bf0eb64870584e50d8070cd46fa58a1c8b`. The local synthetic walkthrough
label and access/record-status messages remain. Host, authorization, record,
context, Session Prep, and Scribe behavior are unchanged. No schema, data,
dependency, environment, cohort, or wire-contract change is included.

The new SHA-256 is
`b6e2ec405f5deed72b26b3486e1ee657142d0e7d96f586584b658320c09f9ae7`;
SRI is `sha256-tuLsQF9d7tcrJrNIbh7mVxQtDn2W9YZYS2WDIMCfmuc=`.
The previous asset, digest
`c9ac2270d55c76e020df46d7a988cc734e00b2c9ef43df4864f557f3714953b9`,
and all earlier immutable assets remain available for cached frontend versions.

Independently review each final commit, then merge Sovereign PR57 followed by
the Core pin. Verify the upstream source remains reachable from Sovereign
`main`. The existing receiver already supports embed version `1.0`; no new
receiver behavior or Sovereign service deployment is required for this static
Core asset.

The founder has authorized this staging release in the current work session.
After review and merge, use the canonical local build/publish/deploy path under
that existing staging delegation while GitHub Actions remain disabled; no
additional workflow dispatch or user approval is required. The fresh baseline
is recorded in
`/private/tmp/cosmaneura-sovereign-staging-20260928/baseline-20260928T130854Z.json`.
Reconcile any change from that Core/Sovereign/Scribe receipt before deployment
and preserve the current Scribe capabilities. Production promotion and cohort
expansion are outside this change. No new feature flag is needed for this
copy-only update; existing access controls and the immutable asset pin remain.

Verify exact upstream bytes, content digest, SRI, exclusions, and upstream
commit with the existing asset test. Run the existing host, terminal-401,
completion, reconnect, and load-recovery tests plus the upstream panel
contract/completion harnesses against the copied asset. Record syntax, lint,
format, and test results with the final PR review evidence.

After release, use a synthetic record to confirm the loaded asset digest and
the requested title/subtitle. Confirm unavailable access and missing-record
states still show their status messages. Stop acceptance if the asset digest
differs, the panel fails to load, or existing record/completion behavior fails.
Local tests alone do not establish live Scribe or clinical workflow acceptance.

Recovery is a Core pin revert to the retained previous digest and a canonical
local staging release under the same delegation, or restoration of the recorded
previous Core image. Keep immutable assets; no Scribe resource, database,
schema, or stored-record rollback is needed. No irreversible cleanup is planned.
