# Cursor Cloud Agent environment

This environment is for credential-neutral local development. Do not inject
production or staging database credentials, Azure credentials, PHI, or live
restore bindings into it.

Every repository-managed command in `environment.json` begins with
`/usr/bin/env -i` before invoking `/bin/bash --noprofile --norc`, so inherited
variables such as `BASH_ENV` cannot execute in that Bash process. The command
then runs through `credential-neutral.sh`, which rebuilds the process
environment again from an empty base with only reviewed OS values and
`COSMANEURA_CURSOR_CREDENTIAL_NEUTRAL=1`. Target scripts refuse direct use
without the marker. Direnv honors the marker by skipping every local override
file and every 1Password probe/read, then supplies only committed non-secret
development placeholders. A repository-local `op` denial shim remains first on
`PATH` for defense in depth.

The dispatcher also replaces the real user home with the gitignored,
per-worktree `.cursor/runtime-home/` directory and points Git, Docker, curl,
pip, npm, Azure, GnuPG, XDG, and uv configuration/cache discovery into it.
Every fixed directory and direct configuration-file path consumed there is
required to be real and user-owned; symlink and wrong-type escapes fail closed.
This prevents normal tool configuration lookup from reaching credentials in
the host home. It is an environment/configuration boundary, not an operating
system sandbox: processes can still read otherwise accessible host paths.

Cursor may use its own outer command interpreter to launch the command string.
That interpreter necessarily runs before any repository-controlled token and
cannot be cleaned by this configuration. The Cloud Agent host itself must
therefore remain credential-neutral; this boundary covers the repository-owned
Bash process and all descendants it launches.

## Bootstrap trust

`system-setup.sh` installs Mise through `scripts/install_pinned_mise.sh`. The
installer downloads one exact Linux x86-64 release archive and verifies both
the archive and extracted binary SHA-256 before installing or executing Mise.
Updating Mise requires reviewing a new immutable release and updating both
digests together.

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
same validated environment snapshot to the command, including after the test
mode reloads `.env.test.local`.

The approved targets are exact strings, not normalized URLs. They require:

- `PYTHON_ENV=development` with an empty `CI` value for dev, or
  `PYTHON_ENV=test` with `CI=true` for test;
- PostgreSQL on `localhost`, `127.0.0.1`, or `::1`, port `5432`;
- the repository's non-secret local Docker credentials; and
- the exact `development` or `test` database name.

There is no remote-database override. If the guard rejects a target, correct
the local Cloud Agent configuration instead of bypassing it. Staging rehearsal,
cutover, and production operations remain outside this environment.
