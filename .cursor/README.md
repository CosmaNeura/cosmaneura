# Cursor Cloud Agent environment

This credential-neutral, synthetic-only environment must never receive staging
or production credentials, PHI, or live restore bindings. Entrypoints use
`env -i`; the dispatcher blocks inherited secrets and 1Password, redirects host state
to gitignored `.cursor/runtime-home`, validates real user-owned paths, and uses
the physical checkout for Mise trust/ceiling. Dedicated Direnv/Mise roots and
`MISE_NO_HOOKS=1` block ambient configuration.

The digest-pinned Mise launcher uses explicit state roots. Managed executables
must be canonical, user-owned, executable, install-root confined, and never
shims. Residual dev-only trust includes Mise/upstream payloads, signed apt,
Playwright's unverified browser, and writable dependencies. OpenAPI generation
stays disabled because it reaches unlocked `pnpm dlx`.

Compose images are tag-and-digest pinned. A validated Mise Python runs the DB
guard after Direnv; only exact local targets pass. Docker access is limited to
the current agent's primary group, and Localias trust is installed only in the
dedicated NSS database beneath the credential-neutral runtime `HOME`. This is
not an OS sandbox: the checked-out branch and outer interpreter remain trusted.
Keep the host credential-neutral; staging, production, PHI, and restores remain
out of scope.
