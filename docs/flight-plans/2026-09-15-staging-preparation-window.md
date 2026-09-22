# Read-only staging preparation timing

Tier 2: human launch and source-evidence freshness. The operator explicitly
requested a larger run window after the September 15 preparation refusal.

Read-only preparation now permits up to twenty minutes from the actual Terminal
confirmation to launch and ten minutes across its runtime phases. Its retained
source evidence may be thirty minutes old during revalidation. New source
publication and preparation creation still require source evidence no older
than ten minutes; preparation must still be created within five minutes of
the actual confirmation. A thirty-minute preparation artifact is not renewed.
Its earlier source/artifact expiry can shorten the usable window.

The capture path retains five-minute presence, ten-minute source freshness,
four-minute preparation, ten-minute handoff, four-hour plan and its existing
token and action/cleanup floors. No lifetime is changed by re-rendering bytes.
The caller must recheck current protected source/policy after command review;
late or changed commands cannot be presented. Plan creation after a delayed
preparation needs fresh source evidence under the unchanged ten-minute rule.

The observed refusal was separate from launch expiry: the private CLI copy had
obtained an ARM token absent from the original configured cache. The original
cached ARM token lacked the required remaining lifetime. Before a new attempt,
use normal configured CLI token acquisition for the exact ARM resource and
verify adequate remaining lifetime plus exact original-cache membership using
a secret-free diagnostic. No forced refresh, login, scope change, copy-to-original
writeback or weakened identity check is part of this change. That diagnostic
does not establish identity or authorize capture; the human preparation must
still prove the configured account and same-token authenticated ARM read.

Verification: sixteen targeted new/affected fixture checks cover delayed review,
identical command bytes at fifteen minutes, unchanged artifact expiry, rejection
at the new presence boundary, source publication isolation, unchanged capture
freshness/presence, and a ten-minute total runtime that cannot be reset. Source
and loader pins are updated together and checked against actual source bytes.
Fixtures never invoke the human consumer, credentials, Azure or a real snapshot.
Independent final-head review and normal protected merge remain required.

Recovery is to leave this source dormant or use a normal reviewed revert. Keep
all old expired commands and failed private session artifacts as evidence; no
cleanup, overwrite or automatic retry is authorized.
