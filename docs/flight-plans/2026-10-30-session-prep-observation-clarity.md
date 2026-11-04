# Session Prep reviewed-source panel clarity

- Tier 2: provider record UI; clarify missing structured observations while retaining reviewed-note recap, limitations and the source-reading path.
- Pair: provider dab1d8e839f0832248017e771cab8800b189ab00 and the exact asset/SHA/SRI recorded in SovereignPanelAsset.ts.
- Boundaries: isolated source only; no deployment authority, new facts, model/schema/flags/account changes, Scribe changes or production actions.
- Sequence: independently review final heads; after separate staging authorization, update the paired provider service followed by Core web and verify immutable identities.
- Checks: asset/SRI and host/history/reconnect/expired-session tests; live ready/pending/empty-observation views and source access remain a release gate.
- Recovery: restore recorded prior Core/provider images and retain every older immutable panel, including 2c82702, for already-open sessions.
