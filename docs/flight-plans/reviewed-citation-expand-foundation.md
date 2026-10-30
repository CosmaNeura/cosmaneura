# Reviewed citation expand foundation — Tier 2

This dormant rung adds nullable bounded JSONB to accepted final phases. It
does not enable citation admission, viewing, provider roles or beta activation.
The official Alembic-generated ID is `d4de1ee4bb02`, after `3d40e4e90e05`.
Generation used the canonical template in an empty owned script directory to
avoid historical revision imports of application setup; the real head was
bound statically afterward. No ID or unrelated migration was changed.

1. Require exact-head independent review, the unique migration graph and
   application migration/constraint regressions. The prior actual owned
   PostgreSQL target-table rehearsal applies this exact migration; it does not
   replace a full application rehearsal or prove a live schema.
2. Apply the additive rung BEFORE starting any image with the new ORM field.
   Old rows remain NULL without backfill; existing append-only protection stays.
3. Integrate the separately reviewed retention/admission and display consumers
   before one coordinated beta package. This foundation alone is dormant.

Recovery: revert application images and leave the column and accepted values
intact. Never drop, backfill or rebind this column as immediate rollback.

The ORM uses `JSONB(none_as_null=True)` so legacy Python `None` remains SQL NULL; JSON `null` is refused by the unchanged binding check.
