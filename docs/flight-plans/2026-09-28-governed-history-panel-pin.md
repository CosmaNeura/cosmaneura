# Deliver the governed conversation panel

Tier 2: Core serves the independently reviewed Sovereign PR60 panel bytes at
`f7f75c63a5459d441f27ef063183346fa89914a2`. The panel accepts the closed, bounded conversation-history envelope,
restores current-encounter messages, and checks a pending response without
resending the question. It retains the reviewed brief/composer layout and
caption from PR59 and PR57. Old hosts remain compatible with legacy history
projections; the separate Core host adapter enables the protected read endpoint.

Asset SHA-256: `b892f8516c9c08270b0296be41b0601ef9e42636aa7c2ce68b0fdd82bb73695f`.
SRI: `sha256-uJL4UWycCCcLApa+QbBgHvnkJjaqfCzmiw/dgrtzaV8=`. Keep all previous immutable assets for cached clients.

Verify byte identity with the final reviewed upstream source, update SHA, SRI,
commit and exact formatter/linter exclusions together. Run Core asset, host,
auth-expiry, completion, reconnect and load-recovery tests, plus both upstream
panel harnesses against the copied asset. History rendering uses text, enforces
20,000 Unicode code points per message and a separate 192 KiB UTF-8 envelope
bound, and clears state on encounter or authority changes. No browser storage,
new dependencies, schema changes or cohort expansion are introduced.

The current conversation authorizes staging deployment of Session Prep and
visible Sovereign workflows. Release Core PR531 receiver and the separate host
adapter together, then the merged Sovereign PR60 consumer. Before deployment,
reconcile exact source, image, runtime and schema against the accepted Core
PR528 staging baseline. Preserve Scribe and public Sovereign services. Earlier
context/layout-only plans were superseded before deployment; create fresh
reviewed plans for this complete source pair. Local tests do not establish live
acceptance: test grounded questions, history reload and response recovery on
the existing fictional encounter after deployment.

Recovery restores the recorded previous Sovereign provider image first if its
new consumer is active, then the retained Core PR528 image if necessary. Keep
old assets and existing conversation receipts; no database cleanup, schema
downgrade or Scribe rollback is needed. Production and hidden tools are outside
this staging release.
