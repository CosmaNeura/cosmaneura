# One fresh Core decision for each preparation dispatch

Tier 2. Metadata and first-window preparation each run the same complete
authorization twice before one downstream request: the gateway before yielding,
then the helper immediately before dispatch. Move this responsibility to the
helper. It authorizes once after the cancellation yield and again after complete
receipt validation. No transaction or permission crosses remote I/O.

Both helpers derive the immutable admitted family from current Core authority.
An internal supplied scope remains correspondence evidence and must match.
Existing binding/workspace, creator/consent, configured-session, closed recipe,
original component keys and post-validation checks remain. Source start,
continuation, legacy reads, ordinary components and durable admission are outside
this change. The native receiver still performs all its original account fences.

Qualify real SQL/lock ordering and receipt/key equivalence, independently committed
revocation before dispatch and after I/O/validation, cancellation, uncertain
reply recovery, and native/shared recording tests. Report exact tested commit.
Local implementer review follows the owner's September 25 merge policy; it is
not independent review or GitHub approval. No permission or access policy changes.

Release the merged Core image through the current source-bound staging build and
publication path. Current Core718 recovery image is
`sha256:92bdb2e4c5f1261c0a0a4738aef42da0d57f630f0daff75b1c2ae7909246fa73`.
Retain prior per-role revisions and configuration/secrets. No schema, dependency,
identity, environment configuration, production, cohort, billing or physical
audio change. Existing Scribe receiver38/API19 and desktop64 are compatible.

Record installed consent-to-ready backend spans separately from UI observation
and actual source startup. The 5–7 second target remains open until repeatable
installed measurements establish it. Phone hardware acceptance remains open.
