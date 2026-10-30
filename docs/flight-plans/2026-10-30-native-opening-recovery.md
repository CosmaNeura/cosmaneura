# Native opening recovery

Risk: Tier 2 because this crosses a consent-adjacent service boundary. The durable
admission journal and all ready-admission recording checks remain unchanged.

An interrupted admission leaves `consent_unconfirmed`. Opening discovery must
not turn that into `not_prepared`, a ready receipt, or another consent request.
For a native mount only, retain its independent encounter authorization and
publish the existing closed `provider_operation_conflict` recovery disposition
after the final fresh encounter check.

Deploy the compatible Scribe native-receiver change first. Then deploy this
merged Core change using the canonical staging candidate controller. Verify
opening an interrupted synthetic visit reaches explicit original-journal
recovery or the separate-visit recovery screen, with no consent/session/source
mutation during opening. Ordinary read and recording operations must continue
to refuse the unconfirmed admission. Then complete capture, Save, reopen and
playback using a ready admission. Source checks do not qualify those live gates.

On failure, stop qualification and retain the original request and uncertain
admission. Restore Core's prior image before restoring the receiver's prior
image. Do not mutate the journal, fabricate consent or replay admission on open.
