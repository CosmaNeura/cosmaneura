# Receive larger Scribe visits before enabling capture

Tier 2: bounded native capture/source/recovery wire contracts. Owner requested
three-hour visit capacity and authorized local implementation, checks and merge.
No deployment, new cohort, data write, migration or long-lived authority issued.

Core accepts legacy 1,000-chunk/64 MiB capability receipts unchanged and an exact
2,048-chunk/384 MiB alternative. Larger positions work through reservation,
registration, completion, native starts, recovery and continuity. Short leases,
90-chunk batches, per-chunk encryption bounds, creator/consent checks and the
separate acknowledged 60-minute profile are unchanged. Scribe still enforces
actual remaining budgets. A 3-hour single mixed/mono track needs 1,080 chunks;
384 MiB covers its 353,894,400-byte conservative reservation allocation with
headroom for partial chunks. Speaker count does not multiply stored tracks.

Rollout: merge this receiver compatibility first. Prepare matching canonical
Scribe/client limits and bounded assembly next; deploy receivers before clients
under a later explicit release decision. Do not advertise three-hour recording
until native renewal, background lifecycle, disk checks and text limits are
integrated and qualified. Use accelerated tests, not a real-time three-hour soak.

Recovery: before larger recordings are admitted, retain/revert to the previous
Core release. After admission, keep this compatible reader while disabling new
long-visit starts; never strand retained larger recordings by downgrading their
reader. No key, audio, journal, session or terminal job is reset.

Validation: contract boundaries, old fixtures, scoped gateway/source/recovery
regressions and local implementer review. Record exact SHA and commands in PR.
