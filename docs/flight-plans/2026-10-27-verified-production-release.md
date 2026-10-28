# One-time production release with retained acceptance

Tier 2: production promotion control. The owner explicitly instructed release
and then instructed that successful tests must not be repeated.

Run 40591309946 validated source 955527ff7c3f91a9fb0fc5529a4de82aeff696e2.
Its application shard passed 4586 tests, skipped 1639 existing cases and failed
one exact equality assertion that still expected the preexisting Plan header.
The merged Plan renderer deliberately emits the complete attributed entry as a
bullet. Repair only that expected string and verify only its failed parameter
locally; retain every other assertion and all application behavior.

`Release verified production` performs no acceptance reruns. Before protected
build credentials, its guard requires that original run to finish, every other
named validation job to succeed, exact original source/actor/run provenance,
the single known failure and unchanged completion totals. Missing, duplicate,
rerun, incomplete or additional failed jobs are rejected. The original deploy
must have been skipped without executing a step.

The full Git tree must equal clinical merge 4ff9318298e332f67b18117ccbabfd3764f569d7
except the four new reviewed release-control/test/document files. Clinical PR #854
already includes the exact equality repair and separately accepted 15-file delta.
Every delta file is SHA-256 bound; its exact reviewed head, merged PR metadata,
comment ID/author and accepted review-body hash are required before credentials.
The original run covers unchanged source; it does not cover the clinical delta.
That delta retains its 217 focused author tests, 18 independent production tests
and exact 17-response replay. No successful acceptance or model call is repeated. The repair
reviewed commit must be an ancestor of master with an identical tree. Two additional read-only token permissions allow the gate to inspect the pinned
PR and review comment; production credential scopes are unchanged. Existing
manual founder, Apply ancestry/configuration, callback, dark inbox, current-master,
credential scoping and shared deployment concurrency controls remain in force.
Build, publish, migration preflight and ordered Azure deployment steps are copied
from the existing reviewed production workflow. No other workflow is enabled.

Verify all three Core workloads at the release SHA/attempt revision, ready and
healthy; API traffic must select the new revision and older worker replicas must
drain. Read-only database metadata must show sole Alembic head a659469204e2 and
the preference table constraints. Record registry digest separately from runtime
tag metadata. Check availability and public page load only, and compare staging.

Recovery retains the expanded schema and migration lineage. Use a reviewed
forward code revert retaining migrations or selected workload-only restoration.
Do not run the old 8fa image through `deploy.sh` after expand: its old Alembic
graph cannot resolve the new head. Refresh retained post-Apply revision metadata
before selecting recovery. This workflow is restricted to the named historical
acceptance run and exact repair; it is not a general acceptance exemption.
