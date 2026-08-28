# Public capability entitlement contract v2

V2 preserves every v1 actor, gate, seat, capability, and reason-code field. It
adds machine-enforced product semantics that the v1 prose documented but its
schema did not constrain:

- every `rollout: disabled` decision is denied, has `data_mode: none`, includes
  `rollout_disabled`, and cannot include `allowed`; and
- `cab.operation_mode` is exactly `advisory_only`.

CAB remains billing intelligence for human review. This contract does not
authorize EHR writeback, 837P generation or submission, clearinghouse or payer
enrollment, claim or appeal filing, or patient contact. NPI remains absent and
is not an authentication, credential, payment, entitlement, or tenant-authority
input.

The v1 files remain byte-pinned for existing receivers. New receivers must
validate v2 before accepting the runtime `public-capability-entitlements.v2`
response; unknown contract versions fail closed.

Canonical schema SHA-256:
`a51843ed0810a2b43fd16cf6e66eb2830717c8381aa7fea3f8d05b9e55cb1bad`.
