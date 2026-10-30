# Canonical staging runner capacity

Tier 2: staging protected credentials move from the local validation runner to
standard GitHub-hosted Ubuntu 24.04. The repository's ordinary PR checks already
use that pinned hosted image. The selected canonical run 41067057002 queued at
Authorize because no repository runner was registered for its local labels.

The change replaces only the ten canonical job runner labels. Dispatch inputs,
founder and rerun-actor checks, protected current-master controls, immutable
merged-PR candidate provenance, test prerequisites, credentialless image build,
archive verification, staging-only registry and OIDC identity checks, runtime
configuration preservation, schema/recovery gates and evidence stay intact.
Other staging and production workflows retain their existing runner controls.

The three protected jobs still use the staging environment. Configuration and
deployment use its existing staging service-account credentials; publication
uses its existing staging registry credential. Candidate code builds in a
separate job without those credentials; publication loads the verified archive
without executing it. The change grants no new secret, identity or permission.

The staging registry is Basic with public network access enabled, as observed
read-only on October 30. No registry or firewall setting changes. Schema probes
and migrations continue through the canonical owned Azure job inside the
existing staging environment, with its existing private database path; the
hosted runner needs no direct database connection or firewall exception.

Validation runs the existing compute-policy and canonical staging contracts.
Independent final-head review and normal required PR checks precede merge.
After merging the controller change, the founder dispatches the same immutable
candidate and reviewed ten inputs from current protected master. An old queued
run cannot pick up the changed workflow and must not be mistaken for the new
attempt. Only its owner may cancel it after confirming no job started.

Recovery is a source revert of this runner-only change. It changes no deployed
app, schema, credentials or infrastructure. A failed live deployment remains
subject to the unchanged canonical recovery and no-replay controls; runner
capacity alone does not qualify the Scribe or Zoom journey.
