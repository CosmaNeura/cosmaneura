# Preserve combined-release rollback revisions

Tier 2: the fixed staging deployment path and its Azure command boundary.
This is operational beta engineering; no application, patient-data schema,
provider cohort, or separate Scribe service changes are included.

On September 25, Core web, worker, and scheduler each had
`maxInactiveRevisions=0` and retained only their current `6a1764c2` revision.
The same zero setting was present in retained PR500 deployment responses.
The canonical recovery procedure copies an exact previous revision's template,
so its earlier successful preflight no longer supplied an available rollback.
The original source of the zero setting is unknown.

Version-2 deployments now set inactive-revision retention to ten in the same
update that selects the reviewed candidate image. After each workload becomes
ready, the deployer checks the retention setting and the exact previous
revision's immutable image and provisioned state. Missing or changed recovery
targets stop the rollout before the next workload and prevent a success claim.
The Azure command boundary accepts only one exact `--max-inactive-revisions 10`
on updates to the existing fixed Core staging workloads. Historical release
invocations and the separate Scribe runtime operations retain their behavior.

Before the next combined release, bind the then-current compatible Core image
and all three exact revisions as its recovery baseline. The current observed
baseline is source `6a1764c222af96733f80067aa9e01535d7735473`, image
`sha256:43b670ba6927c8844a2b2d889c5c298405d56e3ad3aa2c7bfec42f965c416835`;
refresh those identities if staging advances. This change does not recreate
already removed revisions and does not make PR500 or standalone PR501 a suitable
rollback from the newer Scribe fixes.

Validation exercises the real shell deployment/recovery-check functions with
synthetic Azure responses, command-boundary rejection cases, and rollout stop
behavior. No live retention setting, rollout, restart, or rollback is performed
by this source change. The next authorized staging release must verify the
previous revisions survive activation and run its current recover-check before
claiming deployed rollback readiness.

Recovery for this change: retain the positive retention setting and use the
release's verified prior application templates. A source revert must not reset
retention to zero. No database downgrade or new image-restoration mechanism is
introduced.
