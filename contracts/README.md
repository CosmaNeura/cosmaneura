# Desktop API contract

`desktop-v1.openapi.json` is generated from the routes that the platform
actually mounts under `/desktop/v1`. It is the provider-neutral source of truth
for native desktop client code generation.

The authenticated browser handoff remains a browser concern and is not exposed
to the native client generator. Consequently, routes under
`/internal/v1/desktop-capture` do not appear here.

Run `just desktop-openapi` to regenerate the contract and
`just desktop-openapi-check` to verify it without writing. Do not edit the JSON
artifact by hand.
