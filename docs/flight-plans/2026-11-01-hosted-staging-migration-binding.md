# Hosted staging migration source binding

Tier 2: staging deployment controls. Private source preparation only; source
publication and runtime deployment need their separate approvals.

The hosted canonical workflow previously passed the newly reviewed candidate and
published image to a deployer whose migration gate still compared them with the
historical founder-user candidate. This stopped migrations before job creation.
The hosted step also omitted the PR and controller inputs needed to verify that
handoff inside the deployer.

The service-principal branch now calls the existing canonical candidate verifier
with the exact source SHA, founder-selected PR, expected schema and dispatched
controller SHA. It checks the verifier result and published digest handoff before
deriving the existing migration selectors. The deployer repeats this source
check immediately before the existing identity, configuration and migration
gates. The workflow supplies the missing PR, read-only GitHub token and
controller SHA. The canonical audit inventory and pinned digest are refreshed
without changing its protected-path or self-exclusion policy.

The founder-user verifier and historical candidate/image/schema pins stay in
place. Historical founder-user releases retain their compatibility bootstrap;
founder v2 and hosted current reviewed candidates use the existing plain Alembic
command. Focused tests exercise current migrations/env.py percent handling and
both persistent template modes. This change does not certify arbitrary old
hosted candidates as compatible with current migration startup.

Recovery: retain the current staging images, configuration and schema. Revert
this source change if its checks fail. After admission, changed protected
controls require a new reviewed canonical dispatch; an old in-progress dispatch
cannot continue across this control change. This source preparation runs no
deployment, migration, Azure mutation or Scribe update.

Validation evidence will bind the final private commit and focused normal pytest,
locked formatter/linter, shell syntax and canonical fixed-inventory results.
Independent final-source review remains required; no runtime success is claimed.
