# Persistent production PostgreSQL authentication

Tier 2. Core's production PostgreSQL configuration previously defaulted Entra
authentication to false. Enabling it through a one-time management operation or
optional varfile could therefore be undone by a later ordinary Core plan. Scribe's
production runtime needs that authentication to remain available after migration
administrator cleanup.

The production root configuration now defaults the existing Entra input to true
and rejects false input overrides. The server still retains password
authentication and the exact existing tenant. The migration administrator remains
false and its identity binding remains null by default. The Scribe workload,
native login, workflow and cohort gates remain separate and disabled. No staging
configuration, identity grant, database, schema, networking or hosting is changed
by this source implementation.

The founder-only `Terraform Production Plan` and `Terraform Apply` workflows load
this same production root. Apply requires the current protected master, exact
configuration/provider/state attestation and equality with the newly reconstructed
reviewed plan. Thus an older default-false plan cannot satisfy the canonical
apply path after this source change. The bare local `just tofu_apply` convenience
recipe now refuses execution instead of providing a saved-plan bypass. Direct
cloud operations or arbitrary old binaries are outside this supported source
contract and do not inherit execution authority from this change.

Before any production authentication mutation, independently review the final
merged source and the applicable apply entrypoints; obtain fresh existing-server
metadata, retain password auth, record the explicit production decision and
reconcile Core state. A source merge is not the production mutation or its
authorization. Apply must preserve every other existing resource attribute and
must stop for any host/configuration drift. Migration administrator registration,
database bootstrap, schema changes, least-privilege grants and Scribe activation
remain later separately scoped steps.

Validation uses the existing secretless provider-mocked production plan: default
dual authentication without an administrator, administrator cleanup retaining
dual authentication, and explicit false overrides rejected with and without an
administrator. These tests establish source behavior, not effective Azure
identity permissions or a live production acceptance result.

Recovery leaves the additive Entra setting enabled and retains password access.
Disable the Scribe workload/cohort if necessary while preserving its schema and
runtime access configuration. Removing Entra authentication requires a separate
review confirming no dependent runtime and an explicit owner recovery decision;
flipping a deployment input to false is deliberately unavailable.
