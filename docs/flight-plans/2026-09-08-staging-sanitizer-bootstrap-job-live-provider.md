# Staging sanitizer bootstrap job live provider

This Tier 2 slice pins the dormant Azure-read policy and compiles a
non-authoritative request plan for the future sanitizer bootstrap-job observer.
It deliberately implements no token acquisition or network transport. It does
not create, read, start, stop, update, or delete an Azure resource, request a
database token, connect to a database, publish an image, or enable a workflow.

The compiler accepts only shape-validated scalar inputs and produces one of
three policy-defined descriptive GET sequences:

- initial absence: exact job, UAMI, and private-registry `AcrPull` assignment;
- presence: exact UAMI, `AcrPull` assignment, job, and execution collection;
- final absence: exact job, `AcrPull` assignment, and UAMI.

The subscription, control resource group, UAMI, job, API versions, management
origin, OAuth resource, status expectations, order, and request ceiling are
pinned. The resulting process-local object is not authentication evidence and
must never be accepted as execution authority. The future authenticated runtime
binding must supply the exact private registry ID, the create-only control plane
must supply the provider-observed role-assignment UUID, and the future
authenticated renewed identity must supply the exact client ID.

The policy specifies one token request and at most four sequential ARM GETs,
with exact origin, audience, API versions, order, statuses, deadlines, response
limit, and no redirects, retries, ambient proxies, or caller-supplied wire
fields. Those are specifications only in this slice. The module imports no HTTP
opener, exposes no token source, retains no provider response, and has no
callable ARM path.

Python object names, slots, closures, and tracebacks are not a capability
boundary against same-process callers. Therefore the one-shot transport remains
unimplemented until it can be joined to the authenticated runtime, identity,
provider-observation, image, attachment, and semantic-observer inputs inside an
isolated trusted execution boundary. That later boundary must sanitize failures
after transport frames unwind and return only completed immutable semantic
metadata. Public activation remains terminal at
`activation.sanitizer_bootstrap_job_live_provider_unavailable`.

Credential-free verification:

```sh
uv run pytest tests/infrastructure/test_staging_restore_sanitizer_bootstrap_job_live_provider.py
uv run ruff check scripts/staging_sanitizer_bootstrap_job_live_provider.py tests/infrastructure/test_staging_restore_sanitizer_bootstrap_job_live_provider.py
uv run pyright scripts/staging_sanitizer_bootstrap_job_live_provider.py tests/infrastructure/test_staging_restore_sanitizer_bootstrap_job_live_provider.py
```

Policy SHA-256:
`sha256:54dec3a650054d955b41c41af4c9ee0fe62f53cfb7c720b40b5a0bb4b30e9678`.

Recovery is a code-only revert. No Azure state changes in this slice.
