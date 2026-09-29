# Remove manual location entry from recording setup

Tier 2. The owner requested removing the confusing recording setup field if it
is not technically necessary. It is participant-region metadata, not timezone.
The existing Scribe policy requires all-party consent universally; it does not
select a consent rule using the supplied regions. No location should be inferred
from a device timezone or fabricated to satisfy a form validator.

Allow an explicitly empty participant_regions collection in Core attestation and
Scribe workflow request/receipt validation. Preserve the field, existing supplied
values and all other validation. Empty means not collected. Explicit true consent,
current authorization, attestation freshness, recording-session binding, revocation
checks and durable admission recovery remain required. No database migration or
rewrite: Scribe already stores a non-null text array that permits an empty array.

Rollout: expand all Scribe readers/writers first, then Core, then remove the manual
input in the native app. Old clients with supplied regions continue to work. Keep
the expanded validators on rollback so new records with empty metadata still read;
the prior UI can be restored independently. Do not deploy an older strict reader
after producing empty-region records. No production activation or new fixture.

Validate admission/recovery with both existing and empty metadata, including false
or missing consent rejection. Verify generated contracts, focused authority tests,
and Scribe API/queue/UI consistency before staging activation. Physical microphone
testing is unnecessary for this setup simplification.
