# Local Session Prep release with enabled GitHub Actions

Tier 2: staging source admission and provenance. The local ReleaseV2 controller
currently refuses when the owner enables GitHub Actions, although the local
build does not dispatch a workflow. Accept the actual boolean in local review
mode and bind it into source and build receipts and the protection fingerprint.
An enabled state also requires successful Backend, Frontend and change-budget
checks on the exact independently reviewed head through the existing validator.

Keep the retained Bugbot/version1 path Actions-off. Preserve protected master,
founder JohnClem, the normally merged selected PR, independent review, exact
executable tree and archive closure, read-only transport and phase revalidation.
The change performs no GitHub settings, hosted workflow, cloud, production,
configuration, credential or database mutation. It grants no execution authority.

Validate both boolean states, malformed permissions, permission drift, unchanged
protection controls, and exact-head CI failure, pending, missing and wrong-head
cases. After independent review and normal merge, rebind release inputs to the
new merged source; existing source and rehearsal receipts keep their original
identities. Recovery before invocation is to retain the prior local controller;
no deployed workload or schema changes are part of this source amendment.
