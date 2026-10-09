# Native preparation setup, Core receiver first

Tier2. Installed Core714 prepares in17.562/10.309s. Combine existing Core lookup,
context exchange, mount and binding setup in one server-only facade call for
admission and first window. Keep every fresh policy decision and close each
transaction. This call performs no capture mutation and returns only existing
negotiation credentials to the native server, never to a device.

The native server must freshly recheck its account after setup and directly
before dispatching the ordinary admission/window route with its original key
and body, then recheck after response and before publication. Other mutations,
legacy workspaces, resume and physical source start retain their existing paths.
No context or authority cache, automatic retry or fallback is introduced.

Deploy Core receiver before native sender; recover sender first, then Core.
Retain immutable prior images. No schema, dependencies, config, cohorts, grants,
identity, consent policy, billing or production changes. Virtual audio only.
Prove setup revocation and cancellation, no device credential exposure, ordinary
receipt recovery and real SQL. 5–7s remains open until installed repeats pass.
