# Generate capture notes directly from their sources

Tier 2: clinical draft path, with no schema, authorization or service change.
An earlier synthetic draft added a medication-continuation instruction that the
source did not state. The existing two-call path generated an action list first,
then told the structured generator to include it. That list is derived model
output, not independent evidence, and adds a serial model call.

Scribe now reuses Core's structured prompt builder with explicitly empty action
items. This bypasses the legacy extractor and generates the draft once from the
complete transcript plus identified typed notes and capture-gap/continuity data.
The system instruction explicitly separates medication history from a medication
decision, preserves stated prescription details and prevents template metadata
from establishing a date, signature or attestation. Legacy non-Scribe generation
is unchanged. The current note editor, exact-source/revision checks, final locked
publication gate, save/reopen and human review requirement are retained.

Local validation uses synthetic sources/model doubles and actual isolated
PostgreSQL persistence. Cover exactly one generation, no legacy extraction,
full long-source delivery, revocation and source/schema mutation during generation,
timeouts, duplicate/lost responses, concurrent preparation and edits/save/reopen.
The former second-model mutation cases disappear because that call is removed;
the remaining generation boundary and final publication race still have tests.
These tests establish plumbing and custody, not model factual accuracy or seconds
of device latency. Cloud synthetic fidelity and device timings remain acceptance
work. This is risk reduction, not a guarantee that a model cannot add information.

Release only through the existing disabled/enabled capture-note configuration and
controlled staging candidate. No deployment is performed by this increment.
Recovery is the prior compatible Core artifact; retained notes, sources, edits and
schemas remain valid. Existing drafts are not regenerated automatically.
