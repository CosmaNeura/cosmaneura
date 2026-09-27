# Deliver the reviewed completion panel through Core

## Outcome and scope

The provider dashboard loads a same-origin, content-addressed copy of Sovereign's
panel. Deploying Sovereign PR53/54 alone does not change that copy. Pin Core to
the exact reviewed `app/panel.js` at Sovereign commit
`78fd74277037d4233878e89546d81aeb2d4ada98` so dashboard users receive bounded
automatic completion, request timeout/retry, and the chat-focus fix.

Tier 2: this asset renders provider content and owns record lifecycle behavior.
This change adds the byte-identical asset and updates its SHA-256, SRI, source
commit, formatting exclusions, and integration checks. It changes no server
contract, data, schema, cohort, environment, or Scribe implementation. Existing
Session Prep and provider authority gates continue to apply.

## Source and release order

Core base: `be4ee79e518378274303e2ab9a97132a03e5eed9`, including PR487 and
the independently merged staging reconciliation/release work in PR488/489.
The original workspace and the separate staging release checkouts are untouched.

Sovereign dependencies:
- PR53: `33b9c403414f6b911782c99d32ac203f4a7377c1`.
- PR54: `78fd74277037d4233878e89546d81aeb2d4ada98`, based on PR53.

Merge PR53, then PR54, before releasing this integration. Verify that the
upstream source remains reachable from Sovereign main. Deploy the combined
Core candidate through the separately coordinated staging release. Keep its
Scribe changes and align Core web, worker, and scheduler. The panel consumes
the existing deployed Session Prep API; no new server endpoint is required.

The pinned asset digest is
`c9ac2270d55c76e020df46d7a988cc734e00b2c9ef43df4864f557f3714953b9`.
Retain the prior asset for cached frontend references. A later Sovereign panel
change requires another explicit pin update; deploying its service is insufficient.

## Verification and recovery

The new React-host integration test reproduces the old dashboard remaining
pending after the server is ready. It loads the exact vendored bytes through
the real host, observes pending-to-ready after one click, checks new turn IDs
and minimal request bodies, and verifies host unmount cancels further checks.
Run existing host and first-401-lock integration tests plus Sovereign's panel
contract and completion harnesses against the copied asset. Validate TypeScript
and a local production frontend build with inert settings. GitHub Actions is
disabled; obtain independent Cursor review of the final committed source.

After the coordinated release, confirm the loaded script URL has the new
content digest and complete the synthetic upload/review/later-visit journey.
Local tests do not establish live device, model, or paired rollback acceptance.

Recovery: revert this Core pin PR or restore the preceding coordinated Core
image. The old asset remains available. No schema/data rollback or Scribe
resource replacement is required. Stop release if the loaded digest differs,
another record's brief appears, or checks continue after leaving the panel.
