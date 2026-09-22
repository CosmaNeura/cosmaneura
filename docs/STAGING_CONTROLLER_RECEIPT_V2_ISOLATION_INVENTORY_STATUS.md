# Controller receipt v2 isolation inventory status

The dormant isolation-inventory observer is implemented but not executable or
wired to a workflow. It consumes only a sealed database-observation authority,
reauthenticates the complete upstream chain, performs one managed-identity
token exchange and four exact read-only ARM collection requests, and retains
the exact response bytes only inside its sealed in-process authority.

The output authenticates point-in-time target-resource-group isolation counts.
It does not prove execution history, Blob/Redis copy absence, broad provider
state, receipt existence, writer identity, mutation authority, or runtime
authority. Nested DNS link and zone-group visibility is optional because the
generic Azure resource-group inventory contract does not establish complete
nested-resource enumeration; `unknown_resource_count=0` is scoped only to the
complete, non-paginated response returned for that exact collection request.

All execution, live-provider, workflow, builder, writer, consumer, and
promotion paths remain disabled or absent. Live use additionally requires an
approved exact read-only target-RG inventory grant and accepted provider
contract evidence.
