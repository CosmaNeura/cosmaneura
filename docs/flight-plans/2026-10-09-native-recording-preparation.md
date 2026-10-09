# Native recording preparation

Tier 2. Phone recording currently reads the admitted native context and public
encryption key separately. Add a closed `native_recording_preparation` read
that returns both using the existing fresh preparation-metadata receiver.
The admitted Scribe namespace remains distinct from the Core account tenant.

The existing creator/consent validation, three component receipt checks,
deadline cancellation and post-I/O Core authorization remain intact. Require
the same created/capturing state as native context. This read creates no key,
lease, reservation, capture lineage or source permission. It returns no private
key, transcript or renderer-selected namespace. Native custody and source-start
checks remain mandatory in the client.

Release Core first, then the additive Scribe native receiver, then the native
client. Older context/key callers remain supported. The client must consume
the combined result within its existing single-use, short-lived context and
fall back to a fresh key read after expiry. Recovery stops using the new read;
preserve recordings, journals and keys. No migration, dependency or new service.

Validate actual native Core requests against owned synthetic PostgreSQL:
original context/key equivalence, fewer SQL statements, retained lock shapes,
committed revocation during I/O, session substitution, malformed key, remote
failure, cancellation, recording state and closed requests. Record exact final
commit and applicable local results in the PR; implementer review follows the
owner's September 25 merge decision. Device timing remains unverified.
