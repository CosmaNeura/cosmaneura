# Cursor Cloud Agent environment

This environment is for credential-neutral local development. Do not inject production or
staging database credentials, Azure credentials, PHI, or live restore bindings into it.

Every repository-managed command in `environment.json` begins with `/usr/bin/env -i`
before `/bin/bash --noprofile --norc`, so inherited values such as `BASH_ENV` cannot
execute there. `credential-neutral.sh` rebuilds the process environment from an empty
base with reviewed OS values and `COSMANEURA_CURSOR_CREDENTIAL_NEUTRAL=1`; targets
refuse direct use without that marker. Direnv then skips local overrides and every
1Password read, supplies committed non-secret placeholders, and retains a local `op`
denial shim first on `PATH`.

Direnv receives a dedicated empty `DIRENV_CONFIG`. Startup hooks in `$HOME/.direnvrc`,
`$XDG_CONFIG_HOME/direnv/direnvrc`, or `$XDG_CONFIG_HOME/direnv/lib/*.sh` are
forbidden, and any content in the dedicated directory fails closed before Direnv runs.

The dispatcher replaces the real home with gitignored `.cursor/runtime-home/` and
points Git, Docker, curl, pip, npm, Azure, GnuPG, XDG, and uv discovery into it. Every
consumed directory and direct config path must be real and user-owned; symlink and
wrong-type escapes fail closed. This blocks normal host-home credential discovery but
is not an OS sandbox: processes can still read otherwise accessible host paths.

Cursor may use an outer interpreter before any repository-controlled token, which this
configuration cannot clean. The host must therefore remain credential-neutral; this
boundary covers the repository-owned Bash process and its descendants.

## Bootstrap trust

`system-setup.sh` uses `scripts/install_pinned_mise.sh`, which downloads one exact Linux
x86-64 release and verifies archive and binary SHA-256 before execution. Updates require
reviewing both digests. Every entrypoint revalidates the binary, forbids other
`.local/bin` entries, excludes generated shims from `PATH`, and uses exact `mise exec`.

Mise receives empty `MISE_CONFIG_DIR` and `MISE_SYSTEM_CONFIG_DIR` roots, an explicit
nonexistent `MISE_GLOBAL_CONFIG_FILE`, isolated `MISE_SYSTEM_DATA_DIR`, and
`MISE_NO_HOOKS=1`. Its ceiling excludes the repository parent, retaining repository
config while blocking ancestors. Persisted `$XDG_CONFIG_HOME/mise` or content in a
dedicated config/data root fails closed.

Localias is installed through the equivalent repository-pinned installer in
`scripts/install_pinned_localias.sh`.

The Docker apt signing key still comes from Docker's documented distribution
URL, but `scripts/install_verified_docker_apt_key.sh` requires its exact sole
primary fingerprint before dearmoring and atomically installing it. A mutable
key response cannot silently change the trusted apt root.

Python dependencies receive one `uv sync --locked` during installation.
Subsequent Python commands use `uv run --no-sync`, so migrations, seed work,
and startup cannot implicitly resolve or alter that environment.

Cloud installation uses the committed generated OpenAPI client. Automatic
regeneration remains disabled because the current manual recipe reaches a
transitive `pnpm dlx` tool outside the lockfile; regeneration stays an explicit
developer action until that codegen tool is lock-pinned separately.

Playwright itself is lockfile- and hash-pinned and selects a fixed Chromium
revision. Its separately downloaded browser archive is not digest-verified by
this repository, so it remains a dev-only, credential-neutral residual trust
boundary. Never inject credentials or live data into this Cloud Agent.

Cursor Compose always layers `.cursor/docker-compose.yml` over the shared
development file. PostgreSQL 16.9, Redis 6.2.18, and Mailpit v1.31.0 are pinned
to reviewed Linux amd64 manifest digests; tag-only images never execute through
the Cursor path.

Ubuntu and Docker packages remain authenticated by signed apt metadata rather
than repository-pinned package hashes. Mise itself is digest-pinned, while its
exact-version tool downloads remain delegated to Mise and the corresponding
upstream release channels. These are accepted only inside this credential-free
development environment.

## Database boundary

Automatic migration, seed-state inspection, and seeding are wrapped by
the explicit `cursor_database_dev` or `cursor_database_test` boundary in
`database.sh`. Each boundary loads its mode-specific direnv configuration and
then invokes `scripts/assert_cursor_local_database.py`. The guard passes the
same validated environment snapshot to the command, including after the
test-mode direnv reload of committed `.env.test`.

The approved targets are exact strings, not normalized URLs. They require:

- `PYTHON_ENV=development` with an empty `CI` value for dev, or
  `PYTHON_ENV=test` with `CI=true` for test;
- PostgreSQL on `localhost`, `127.0.0.1`, or `::1`, port `5432`;
- the repository's non-secret local Docker credentials; and
- the exact `development` or `test` database name.

There is no remote-database override. If the guard rejects a target, correct
the local Cloud Agent configuration instead of bypassing it. Staging rehearsal,
cutover, and production operations remain outside this environment.
