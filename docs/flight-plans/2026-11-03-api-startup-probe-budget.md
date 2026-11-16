# Preserve the production API startup budget

Risk: Tier 2, production infrastructure configuration.

The production API's original TCP startup probe failed three times before the
platform stopped the container. The API now runs with a failure threshold of
10 at the unchanged ten-second interval. Revision 22 reached readiness, and
its complete configuration matched the approved poststate. Reversing only
the threshold to 3 reproduced the original configuration hash.

Terraform declared the API startup probe without an explicit failure
threshold. Its image-only lifecycle exclusion does not exclude probes, so a
later infrastructure apply could replace the accepted live startup budget
with the provider's default.

This change declares `failure_count_threshold = 10` only for the production
API startup probe. Its port, transport, other probes and worker startup
configuration stay unchanged. The existing image-only deployment continues
to preserve the probe configuration.

Before a later production infrastructure apply, review its plan against the
then-current live state and require the API startup threshold to remain 10.
This source change authorizes no infrastructure apply or application deploy.

Recovery requires a separate reviewed startup-budget decision. Reverting the
source alone could reintroduce the known insufficient budget; do not lower
the live threshold as an automatic rollback.
