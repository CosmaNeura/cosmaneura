# Scribe native workload projection — Tier 2

Prepare a default-off singleton receiver identity and Core audience for the
native Scribe facade. The existing production projection tied its workload
authentication and Authority Kernel availability to global Session Prep. A later
Scribe-only activation can now select its own reviewed identity without enabling
Session Prep or the global Agent API/Authority Kernel. This source authorizes no
production plan, apply, identity grant, registry/bootstrap, migration, deployment,
native client activation, or practice/cohort enrollment.

`scribe_native_workload_rollout` defaults to disabled/empty and projects only to
Core API. Disabled runtime selects the exact previous Agent API/capture policy
objects. Enabled runtime requires canonical nonzero tenant/audience/caller UUIDs,
a receiver distinct from the provider Sovereign caller, and the existing valid
Clerk issuer/token-lifetime settings. Both native facade workload checks retain
RS256, Entra issuer/audience/tenant, `idtyp=app`, `AgentApi.Access` and singleton
caller enforcement. Clerk remains in its separate existing Authorization header.
Only that facade receives a copy of the complete Authority Kernel settings with
its availability enabled; global settings, Session Prep and other routes remain
unchanged. Capture and encounter gates remain independently required and off.

The first release rung is source only. Review the final exact commit and focused
offline signed-token/configuration/route tests, plus provider-mocked Terraform
projection. The founder must separately supply the actual receiver client ID,
Core audience, app-role assignment and Clerk issuer, then review a current saved
plan containing only the intended API environment/revision delta. All Session
Prep, capture, encounter, Zoom, legacy handoff and cohort decisions remain held.

This does not create the native receiver authority registry, its SELECT-only
roles or current account/encounter mappings. It does not solve the no-new-paid
worker topology, production Clerk session claims/callback bindings or image/schema
qualification. Those concrete gaps remain separate. Native capture's existing
Core ownership/session/consent checks remain in force; this setting grants no
patient access by itself.

Before activation, recovery is discard/revert the source. After a separately
approved deployment, set this object disabled while capture/encounter remain
off, or stop new capture and drain accepted work before disabling it. Recover
only a migration-compatible retained Core image; preserve schema/audit/recording
data and existing Session Prep settings. Default-off source is not security or
data rollback. No schema change or migration is part of this rung.
