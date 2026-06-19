# Staging Zoom app configuration — should staging point at the prod Zoom app?

**Date:** 2026-06-09 · **Status:** Decision note (no code change) · **Context:** the dev/ngrok
Zoom app was removed after webhook problems; only the production Zoom app currently works.

## Current state (verified in the env config)

Staging and production use **separate Zoom Marketplace apps** today:

| | Staging | Production |
|---|---|---|
| Credentials (client id / secret / secret token) | `op://Staging/…` (staging Zoom app; item `zoom-cosma-app-staging`) | `op://Production/…` (prod Zoom app) |
| OAuth redirect (`ZOOM_REDIRECT_URI`) | `https://staging-app.cosmaneura.com/external/v1/zoom-oauth-callback` | `https://app.cosmaneura.com/external/v1/zoom-oauth-callback` |
| Webhook endpoint | `https://staging-app.cosmaneura.com/external/v1/webhooks` | `https://app.cosmaneura.com/external/v1/webhooks` |

Webhooks are received at `app/routes/webhooks.py` and verified via the `x-zm-signature`
HMAC keyed by that app's `ZOOM_SECRET_TOKEN`.

## The core constraint

A Zoom Marketplace app delivers its events (meeting.started, participant.joined,
recording.completed, …) to **exactly one "Event notification endpoint URL."** It can also
allow **multiple OAuth redirect URIs**. This splits the question in two:

### OAuth (connecting a Zoom account from staging) — shareable
Pointing staging at the **prod** Zoom app works **if** staging's redirect URI
(`https://staging-app.cosmaneura.com/external/v1/zoom-oauth-callback`) is added to the prod
app's allowed redirect URIs. Doctors could then connect Zoom on staging using prod app
credentials.

### Webhooks (record → transcribe → notes flow) — NOT shareable
The prod app sends all webhooks to **prod** (`app.cosmaneura.com`). Therefore:
- If staging shares the prod app, **staging never receives Zoom webhooks** → the
  Zoom-driven flow does not work end-to-end on staging.
- If the prod app's webhook URL is repointed to staging to test, **production stops
  receiving Zoom events and breaks** (no recording, no notes). Do not do this.
- One app cannot deliver to both environments.

## Risks of pointing staging at the prod Zoom app

1. **Webhook collision** (above) — the decisive issue.
2. **Prod credentials live in staging** → wider blast radius; a staging mistake or leak
   affects the production Zoom app.
3. OAuth installs/authorizations are app-scoped → testing on staging installs/authorizes
   against the **published production app**.

## Key insight: staging does NOT need ngrok

ngrok was only for **local development** (a laptop has no public URL, so a tunnel was needed
for Zoom to deliver webhooks). **Staging has a public URL** (`staging-app.cosmaneura.com`),
so the problem that killed the dev/ngrok app does not apply to staging — a staging Zoom app
can point its webhook directly at `https://staging-app.cosmaneura.com/external/v1/webhooks`
with no tunnel.

## Recommendation

- **Correct (preferred):** keep a **dedicated staging Zoom app** (or use the *Development*
  view of the Zoom app) configured with staging's public URLs (redirect + webhook). This
  keeps staging and prod isolated and lets the webhook flow work in both. The
  `zoom-cosma-app-staging` 1Password item already exists, so this is likely just
  recreating/reconfiguring that app with the staging URLs — no ngrok required.
- **Tolerable stopgap** (only if staging needs OAuth connect for a demo and NOT the
  recording/notes flow): point staging at the prod app **for OAuth only** — add staging's
  redirect URI to the prod app. Hard rules: **never change the prod app's webhook endpoint
  URL**, and accept that Zoom recording/notes features will not work on staging.

## One-line summary

Sharing the prod Zoom app gets staging *OAuth* working but **cannot** make staging's webhook
flow work without breaking production. For real Zoom functionality on staging, use a separate
staging Zoom app pointed at the staging public URLs (no ngrok needed).
