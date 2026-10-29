# Qualify patched DNS parsing

Tier 1: raise the existing transitive dnspython dependency from 2.7.0 to
2.9.0 and constrain future resolution to `>=2.9.0,<3`. The dependency arrives
through email-validator; this introduces no service or runtime configuration.

[GHSA-45c3-73f7-pv4m](https://github.com/advisories/GHSA-45c3-73f7-pv4m)
reports excessive CPU consumption from crafted DNS compression chains in
versions below 2.9.0. The upstream patch bounds pointer-chain length.

Qualify a fresh frozen installation, verify no unrelated package versions
change, exercise bounded wire parsing without network access, and run existing
email/onboarding/practice validation regressions on owned synthetic services.
Require independent review of the final pushed commit and hosted checks before
inclusion in an owner-selected release candidate.

No schema/data sequence, feature flag, cohort change or migration applies.
Hold promotion if qualification fails. A source revert restores the affected
parser and is not a completed security recovery; retain the deployed artifact
while preparing a corrected candidate. No production action is authorized here.
