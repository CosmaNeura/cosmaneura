# Frontend security dependency qualification

Tier 2: the dependency migration touches upload validation and source-bound CAB
response schemas. It preserves existing patient matching, authorization and
billing semantics. No schema/data or environment change is included.

The 2026-10-28 requalification also raises the declared Vite floor to 6.4.5
(GHSA-8g5g-mxqh-grgj) and overrides affected transitive qs releases to 6.16.1
(GHSA-6pq8-jhm2-2vcw). The qs artifact and its two Express/body-parser consumers
come from pnpm resolution; every other previously qualified package artifact
is retained. Validate this minimal graph with a fresh frozen install and audit.

Release one reviewed source candidate containing the patched Vite, Happy DOM and
Zod graph, compatible form resolvers and assistant UI, and the required Zod 4
syntax repairs. Preserve exact patient names and existing validation messages.
Do not replace vulnerable Zod 3 transitive packages with incompatible overrides.

Before inclusion in a release: frozen install, dependency audit, TypeScript,
full frontend suite, and production-mode bundle must pass. Check upload guidance,
intake send/retry/completion, onboarding billing recovery, and source-bound CAB
parsing. Test DOM cancellation handling retains the original rejected promise;
it does not suppress other animation errors. Real-browser acceptance remains a
separate exact-candidate release gate.

Recovery: keep the currently deployed image until the candidate is qualified.
If qualification fails, hold promotion and repair the candidate. A source revert
would restore vulnerable dependencies and is not a security-complete recovery.
No feature flag, migration rung, backfill or cross-repo sequencing applies.
