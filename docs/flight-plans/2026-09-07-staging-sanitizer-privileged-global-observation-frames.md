# Staging sanitizer privileged/global observation frames

This Tier 2 slice is stacked on exact query-grammar commit `b721cbce3cd66936aea51d3532627e5c8a381ffe`. It implements only the process-local structural decoder for future privileged/global catalog results. It opens no database connection, obtains no credential, executes no SQL, calls no provider, and grants no staging or production authority.

The decoder accepts exactly two ordered tuple sections: 11 server-once frames and up to 64 ordered database entries, each with an exact OID, UTF-8 name, and two per-database frames. Frame names, ordering, columns, scalar types, nullability, per-frame row bounds, total rows, total queries, row bytes, database-key bytes, and total canonical bytes are inherited from the hash-pinned query grammar and enforced before sealing.

Duplicate database OIDs or names and duplicate rows within a frame fail closed. Raw role, identity, database, object, ACL, session, and label values remain inside an immutable, non-copyable, non-pickleable object. Its public surface contains only bounded counts, contract hashes, a frame-set digest, and explicitly non-authoritative claims.

Public construction remains terminal at `activation.sanitizer_privileged_global_database_observer_unimplemented`. Bootstrap identity, maintenance database, disabled-database handling, the connection adapter, observer, offline authenticator, stability authorities, post-provision descriptor, and final join remain separate reviewed work.

Observation-frames contract SHA-256: `6cbff47fbd162b52c0bc6e22d71d28adea3d91666ef09c84d8125172b0e1b1bf`.

Recovery is a code-only revert. No external state is created.
