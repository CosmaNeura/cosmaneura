# Staging sanitizer bootstrap job live provider

This Tier 2 slice implements only the dormant Azure transport beneath the
future sanitizer bootstrap-job observer. It does not create, start, stop,
update, or delete an Azure resource, request a database token, connect to a
database, publish an image, or enable a workflow.

The provider accepts one process-local sealed plan and one exact bootstrap-UAMI
managed-identity token source. A plan is limited to one of three policy-defined
GET sequences:

- initial absence: exact job, UAMI, and private-registry `AcrPull` assignment;
- presence: exact UAMI, `AcrPull` assignment, job, and execution collection;
- final absence: exact job, `AcrPull` assignment, and UAMI.

The subscription, control resource group, UAMI, job, API versions, management
origin, OAuth resource, status expectations, order, and request ceiling are
pinned. The future authenticated runtime binding must supply the exact private
registry ID, the create-only control plane must supply the provider-observed
role-assignment UUID, and the future authenticated renewed identity must supply
the exact client ID. There is no public plan or provider constructor in this
slice.

One plan permits one token request and at most four sequential ARM GETs. The
adapter supplies every URL, query, header, method, and timeout; callers cannot
override them. The production construction path creates the hardened opener and
deadline internally, and recompiles the hash-pinned scalar transport policy at
consumption time. It disables ambient proxies and redirects through the
existing hardened runtime adapter and never retries. Successful response bytes
are validated and digested inside the transport, then discarded; 404 bodies are
discarded without reading. Returned objects contain only authenticated metadata
and body digests. A future semantic observer must consume response bytes inside
the transport before metadata is returned, without adding a body-bearing bridge
to the returned object. Any mismatch, redirect, malformed or oversized
response, clock failure, timeout, reuse, or unexpected provider behavior
produces a bounded content-free refusal and no partial result.

The provider transport and sealed request model are implemented, but semantic
job observation, attachment authentication, live activation, control-plane
mutation, and workflow integration remain false. Public construction remains
terminal at
`activation.sanitizer_bootstrap_job_live_provider_unavailable`.

Credential-free verification:

```sh
uv run pytest tests/infrastructure/test_staging_restore_sanitizer_bootstrap_job_live_provider.py
uv run ruff check scripts/staging_sanitizer_bootstrap_job_live_provider.py tests/infrastructure/test_staging_restore_sanitizer_bootstrap_job_live_provider.py
uv run pyright scripts/staging_sanitizer_bootstrap_job_live_provider.py tests/infrastructure/test_staging_restore_sanitizer_bootstrap_job_live_provider.py
```

Policy SHA-256:
`sha256:13b0ee5ee2fca44ac54da442927eed22358eeff08bc165112f93820c89a33d65`.

Recovery is a code-only revert. No Azure state changes in this slice.
