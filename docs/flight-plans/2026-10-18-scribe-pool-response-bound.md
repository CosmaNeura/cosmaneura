# Scribe pool response allowance correction

Tier 2: bounded transport in the capture path. This is a separate fix from the
prepared-context/admission consolidation.

The exact Core 67 synthetic PG comparison returned two ready/200 admissions with
pool OFF. Pool ON returned 503 after its first admission committed ready, with
consent and session receipts and no admission error. The metadata response plan
allows 32 MiB + 16 KiB, while the pool rejected any plan above 32 MiB + 8 KiB.
The prepared-session response allowance of 32 MiB + 32 KiB had the same defect.
The existing constructor raises before metadata dispatch. The saved failed run and
actual-class proof remain preserved; they do not establish the physical 91d
HTTP status or a performance saving.

Keep the request body ceiling at 32 MiB + 8 KiB. Give response plan validation and
parent raw collection their separate 32 MiB + 32 KiB ceiling. The original worker
still applies each operation's decoded limit, header/status rules, model checks,
and current authority fences. No credential, policy, TTL, SQL, journal, endpoint,
or environment flag changes belong to this PR.

Validate the original pool/deadline/header/compression/close suites plus regressions
for existing response allowances, over-ceiling refusal and unchanged request
rejection. Root must qualify a new immutable image on the exact cached Core 67
base, then run the reviewed real-PG ON/OFF comparison against that candidate.
No dependency installation or changes to the failed diagnostic/image are allowed.
Final substantive push requires independent nonimplementer review before merge.

Release only after source, immutable artifact and real-PG qualification. Keep the
current same Core 67 pool-OFF deployment as recovery. A live pool-ON trial and
physical timing remain separately gated; synthetic timing is not a speed claim.
