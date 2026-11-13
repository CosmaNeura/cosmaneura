# Restart a frozen production API automatically

Risk: Tier 2, production infrastructure configuration.

On 2026-11-05 the production API accepted TLS but answered no request, including
`/healthcheck`, until Azure's ingress returned `504 stream timeout` after 240s.
A manual restart of the active revision restored service. The API's liveness
probe was TCP-only, which passes while uvicorn holds port 80 even when its event
loop no longer serves requests, so the platform never restarted it.

This change makes only the API liveness probe an HTTP `GET /healthcheck` on port
80 with `Host: app.cosmaneura.com`, every 10s with a 5s timeout and three
failures before restart. `TrustedHostMiddleware` covers `/healthcheck`; without
the public host the probe would receive 400 and restart a healthy container.
Startup (TCP, threshold 10) and readiness probes, the worker, and the scheduler
are unchanged. Liveness runs only after startup succeeds, so the accepted
startup budget is not shortened.

Before a production infrastructure apply, the plan must show only this probe
change on `cosmaneura-prod-app` and the startup threshold still at 10. Watch the
new revision's restart count and system logs for liveness failures for at least
ten minutes. This source change authorizes no apply or deploy.

Recovery: if liveness restarts a healthy API, set the liveness probe back to
TCP on port 80 through a reviewed apply. Do not lower the startup threshold.
