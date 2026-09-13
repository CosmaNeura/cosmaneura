# Flight plan: dormant launch-receipt mailbox receiver

- Tier **2** adds the receiver-side path from one Container Apps execution UAMI to its execution-addressed immutable receipt and `.signature.json` envelope. Paths derive only from policy and `CONTAINER_APP_JOB_EXECUTION_NAME`; authentication is offline and returns the sealed `AuthenticatedBrokerLaunchReceipt`.
- Use only the job UAMI's exact client ID at the local identity endpoint and storage audience; forbid ambient credentials, proxies, redirects, listing, query, and caller-selected URIs.
- GET receipt then envelope. Retry only 404 every three seconds: 20 attempts/object, 40 total, one 60-second deadline, and at most 10 seconds per token-plus-Blob exchange.
- Bind exact HTTPS host/path, Blob version, identity encoding, JSON media type, bounded body, sealed request/response, storage account/host, and container resource IDs. Discard bounded error bodies.

- Executor wiring, sender/broker changes, Azure/Terraform/workflows, runtime-record verification, and live calls remain out of scope; every implementation and activation flag stays false.
- Receiver-before-sender remains mandatory. Hold activation for separately reviewed private endpoint/DNS/RBAC evidence, execution-name integration, executor wiring, create-only publication, and live interop.
- Abort on ambiguous token binding/expiry, redirects, partial/duplicate/compressed/oversized responses, unexpected status, timeout, environment mutation, invalid signature, or activation-bit change. Dormant recovery is a code revert.
- Tests cover exact routes/UAMI, token compatibility/cache/expiry, polling/deadlines, HTTP bounds, sealed-response binding, mutation/tampering, and bounded errors. Run broad restore tests, policy validators, Ruff, Pyright, and budget checks.
