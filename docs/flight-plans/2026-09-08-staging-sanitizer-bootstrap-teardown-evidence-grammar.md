# Staging sanitizer bootstrap teardown evidence grammar

Tier 2, based on protected `master` `8c5e32e1197e83892c0487be2e259d621d276324`.
This dormant helper validates the closed-world post-teardown evidence bundle
consumed by the separately reviewed bootstrap-identity authenticator. It performs
no network, credential, provider, database, workflow, deployment, or mutation action.

The grammar anchors all reads to one owner-only directory descriptor, rejects
links and replacement races, and verifies exact file digests. It binds every
Container Apps job and PostgreSQL server to the selected subscription, every
administrator to the sole Entra-enabled server, recomputes component hashes,
and requires the raw OpenTofu state bytes to match the embedded state. Nested
state and Azure Foundry response objects are closed-world and type-strict.

The review request binds its exact input, evidence digest, model, reasoning level,
and output ceiling. The response must echo that metadata, match its policy-pinned
content-filter offset, and contain only the exact completed verdict structure.
The helper emits no authority and is inert until imported by a separately reviewed authenticator. Recovery is code-only revert.
