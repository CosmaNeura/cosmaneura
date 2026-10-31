# Core provider context read — Tier 2

The standalone Scribe receiver currently cannot discover its actor, tenant or
catalog without five separately provisioned OS registry tables. Core already
owns the reviewed identity/provider mappings and catalog. This stacked source
rung adds `/service/v1/agent/scribe/provider-context` to the existing Core API;
it does not provision another database, role, service or container.

The route accepts only `connection` and `catalog`, a request nonce, the existing
Clerk Authorization header and the separate native workload header. It reuses
both normalized native workload policies and the existing Clerk verifier. A
new default-off `provider_context_enabled` value requires the dedicated native
workload boundary and projects only to API. Global Authority Kernel, Session
Prep, capture, browser catalog and encounter creation flags retain their prior
values. Only this private, closed read receives available authority settings.

Initial identity/Doctor/provider-link SQL discovers exactly one candidate
tenant; it confers no permission and takes no early authority locks. The
existing current-provider resolver then validates reviewed identity, immutable
provider mapping, current membership, provider access and view-as exclusion in
its existing Practice-before-Doctor lock order. It is re-run before returning;
missing, revoked, ambiguous or changed mappings deny. The connection transaction
uses no autoflush and always rolls back. Catalog invokes only the existing
`CatalogRequest` read branch with server-derived actor/tenant, then checks the
returned account and resolves current connection again. No mapping repair,
default practice, compliance attestation, new registry DDL or bootstrap occurs.

This source is based on the independently reviewed native workload rung plus
its test-typing correction. Review this complete additional diff independently
before publication. Offline checks cover real discovery SQL with synthetic
rows, exact existing-resolver invocation/revalidation, rejected mutation and
authority inputs, bounded no-store HTTP output, default-off behavior and mocked
API-only environment projection. They do not qualify live PostgreSQL rows,
production Clerk claims or actual runtime operation.

The receiver still needs an explicit Core-authority consumer mode that replaces
its mandatory engine/registry startup and brackets native requests with this
read. That integration must preserve existing staging registry behavior and
must fail closed while the Core route is dark. No container revision, receiver
mode, image publication, production login, SQL, role assignment, migration or
native activation is authorized by this source. Strict reuse of the three
existing Core containers also remains a separate source requirement: current
Scribe Terraform declares extra apps/jobs and private endpoint resources.

Recovery before deployment is revert/discard this source commit. After a
separately approved runtime rollout, set `provider_context_enabled=false` on
the same existing API projection and retain the previously qualified image.
That disables new context/catalog reads without changing mappings, schemas,
recordings, Session Prep or capture flags. Stop or drain accepted capture work
under its existing recovery procedure before any subsequent capture change.
