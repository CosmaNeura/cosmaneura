# Staging Zoom for the two synthetic lab providers

Tier 2: this changes the explicit staging cohort for an external recording
integration. The user requested Zoom connector access and bot-backup testing
for the two already onboarded Cosmas MD synthetic providers in the virtual lab.

## Change

Add only `scribe-lab-a+clerk_test@cosmaneura.com` and
`scribe-lab-b+clerk_test@cosmaneura.com` to staging Zoom access. Preserve the
existing reviewer and production cohorts. The frontend addition requires the
exact HTTPS staging origin and is separate from the shared beta-feature list.
The server still requires current practice access, a normal Zoom OAuth
integration, and explicit capture authorization bound to the selected visit.
There are no schema, OAuth, webhook, token, or bot-lifecycle changes.

## Validation

Run the isolated backend cohort tests with
`pytest --confcutdir=tests/authority_kernel tests/authority_kernel/zoom_staging_cohort_test.py`.
Run frontend Zoom and shared-beta tests with the committed Node22/pnpm9 lockfile.
Verify both synthetic accounts are denied in production, unknown/lookalike
accounts are denied, malformed or other frontend origins are denied, and
unrelated beta features remain disabled for the two accounts.

## Release and acceptance

Actions remain disabled. Before any staging release, independently review the
final commit, merge it into protected master, reconcile current deployed source,
immutable images, runtime configuration, and schema, then publish a reviewed
immutable candidate. The worker executes Zoom lifecycle jobs and must receive
the staging allowlist change along with the web application. Inspect scheduler
source and preserve unrelated existing drift; do not blindly replace workloads.
This PR does not authorize an unreviewed rollout or change the provider record
cohort required separately for Scribe encounter creation.

Use normal hosted Settings -> Connect Zoom for each approved synthetic profile.
Do not copy credentials or create DoctorVideoIntegration rows directly. The
current model is unique only by doctor/provider, while webhook host routing
expects a unique host: connecting the same host to A and B concurrently is not
a valid test setup. Use separate approved synthetic Zoom hosts, or explicitly
disconnect and finish all active capture for one profile before sequentially
connecting that host to the other. Verify no other staging integration owns
the chosen synthetic host; never borrow a production or clinician host.

Acceptance requires actual scoped OAuth status, a synthetic meeting, explicit
visit-bound capture authorization, bot join/recording behavior, final note
persistence, and normal dashboard verification. Unit tests and connector
visibility alone do not satisfy those gates. Preserve failures and independent
post-push review evidence.

## Recovery

Keep created synthetic visit and audit evidence. For cohort recovery, revert
only these additions on current protected trunk and build a new immutable
artifact after fresh reconciliation. Do not restore an older image over
unrelated note, Session Prep, configuration, or migration changes. Use normal
Disconnect Zoom to revoke a test profile's integration when it is no longer
needed; do not delete provider records or shared data.
