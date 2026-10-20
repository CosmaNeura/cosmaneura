# Cosmaneura web application

The React Router application for provider onboarding, scheduling, session prep,
recording upload, SOAP-note review, practice administration, and billing.

## Local development

Use the Node and pnpm versions pinned by the repository. From `web/`:

```sh
pnpm install --frozen-lockfile
pnpm dev
```

Configuration is loaded through the repository's `direnv` setup. Do not place
credentials, patient data, or production identifiers in client-side `VITE_*`
variables; every such value is embedded in the browser bundle.

The generated API client is derived from `../docs/api.json`. Regenerate it with
`pnpm openapi` after an intentional API-contract change and review the resulting
diff before committing it.

## Quality gates

Run these before proposing a release:

```sh
# From the repository root:
just js_lint
just js_build

# From this directory:
pnpm test
pnpm typecheck
pnpm audit --audit-level high
```

The root build recipe supplies the required immutable `VITE_BUILD_COMMIT`,
regenerates the API client, and compiles the React Router server and browser
bundles. The deployed artifact consists of `build/server` and `build/client`;
deployment is performed only by the reviewed manual workflows described in the
root README.

## UI conventions

- Reuse the shared layout, typography, controls, feedback, and terminology
  components under `app/components/shared` instead of introducing route-local
  visual systems.
- Keep provider/patient terminology sourced from the central terminology
  catalog so every workflow uses the clinician's configured relationship terms.
- Every asynchronous action must expose a pending state, a recoverable error
  state, and a clear success outcome without relying on color alone.
- Preserve keyboard operation, visible focus, semantic labels, reduced-motion
  behavior, and responsive layouts.
- Never render raw service errors, tokens, transcript payloads, or other PHI in
  analytics, console logs, URLs, or generic error boundaries.

## Architectural boundary

The browser communicates with the Cosmaneura application backend. It does not
hold Scribe workload credentials or call the private Scribe service directly.
Recording and session-prep operations are authorized by the backend from the
current authenticated provider, practice, encounter, and feature configuration.

See [customer release readiness](../docs/CUSTOMER_RELEASE_READINESS.md) for the
current verification ledger and remaining environment-owned release evidence.
