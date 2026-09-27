# Verify reviewed squash commits for the staging release

The authorized MCD beta staging update contains squash-merged Core changes.
The local build source verifier currently accepts only two-parent controller
merges, preventing that release from using the existing source-bound build path.

Tier 2: staging source provenance. Permit a one-parent controller commit only
for the existing local-review path, with both commit objects independently
hash-verified and their complete tree IDs identical. Continue checking the
authenticated merged PR and exact reviewed head, current protected master,
ancestry, unchanged non-documentation source, local module bytes, and the
hash-bound attributed review. The Bugbot path retains its two-parent rule.

Validate successful squash proof and rejection of changed trees, corrupt commit
objects, unsupported parent counts, changed current source and the Bugbot path;
run existing build source and controller tests. No credentials, cloud calls or
deployments occur in these tests.

Recovery: retain the previous controller and stop release preparation. This
change neither deploys an image nor changes runtime configuration or schema.
