# Preserve exact native preparation timeout caps

Risk: Tier 2 (security-sensitive operational tooling). Subtracting fractional
monotonic timestamps can round a nominal 30/120-second allowance above its cap.
For example, `(31.7 + 30) - 31.7` exceeds 30. The strict downstream timeout
validator then refuses preparation even though the overall deadline is valid.

Clamp the two human snapshot/preparation remaining-time calculations to the
requested maximum. Preserve the positive-time checks, fixed overall deadlines,
native process/alarm guards, authorization, expiry, cleanup and fixed error codes.
This reduces the possible allowance; it does not renew or extend a deadline.

Update only the changed human module's exact bootstrap source pin, then recompute
the loader and canonical bundle digests. Verify all 16 other component pins are
unchanged. Historical operational reviews bind older bytes and cannot authorize
execution of this new bundle; exact human-command review remains required.

Two deterministic tests exercise both fractional origins with invented adapters
and real isolated native alarms/process checks. The test translates the synthetic
clock origin into the native clock domain without replacing those native guards.
Both fail before the clamp and must pass afterward, alongside all human snapshot,
preparation, command and deadline tests. No actual human consumer, account/cache,
network, database, firewall action, deployment or artifact acceptance is executed.

Independently review the final pushed commit before merge. No rollout cohort,
feature flag, migration or schema sequence applies. Revert this source commit if
qualification fails; retain strict caps and do not accept a refusal as successful
capture. Human operational execution and production promotion remain separately
authorized actions.
