# Local provider workspace browser qualification

This opt-in rehearsal verifies the Scribe provider UI against actual local Core
routes and a local Scribe receiver. It creates only fictional records and
generated audio, runs existing migrations in new private databases, and stops
its PostgreSQL cluster afterward. It does not touch staging or shared databases.

## Coverage and limits

The 390-pixel and 1280-pixel Chromium cases check consent before capture,
encrypted audio upload/decryption, English/Spanish translation review before
note preparation, Core note generation, suppression of an unsupported plan,
editing, saving and reopening. Core retains one transcript, the original and
replacement note, and one current version. Reopening cannot start recording.
Both cases check horizontal overflow and retain screenshots for inspection.

The native identity/storage host, audio input, Speech/translation providers and
note model are explicit offline fixtures. Actual browser encryption, PostgreSQL
migrations, Scribe processing and Core authority/note routes run. The Core-chart
link is a test-only read of the current persisted note, not the real chart UI.
This is not authenticated staging, Stripe, session-prep, physical-device,
real-provider, complete accessibility or crash/retry qualification. Related
review-wire and recovery tests remain separate evidence.

## Prerequisites

Use the reviewed Core harness branch and a compatible current Scribe checkout;
do not silently combine an older Scribe receiver with newer Core operations.
Install each checkout's declared dependencies using its pinned toolchain.
Provide Core's Python 3.13 environment, Scribe's declared Python environment,
Node 24, PostgreSQL 16 binaries and an installed Playwright Chromium browser.
The initial local qualification used Playwright 1.49.1 from an existing external
test installation; that tool is not added to either repository's lockfile.
Record a changed browser/tool version as a new qualification, not reused proof.

Provide existing absolute paths for these non-secret values: `CORE_PYTHON`,
`SCRIBE_ROOT`, `SCRIBE_PYTHON`, `SCRIBE_NODE`, `QA_PG_BIN`,
`QA_PLAYWRIGHT_MODULE` (Playwright `index.mjs`) and
`QA_PLAYWRIGHT_TEST_MODULE` (Playwright `test.mjs`). Preserve the virtual
environment executable path; do not replace it with the resolved base Python.

From the Core checkout:

```sh
"$CORE_PYTHON" tests/browser/run-provider-workspace.py \
  --core-python "$CORE_PYTHON" \
  --scribe-root "$SCRIBE_ROOT" \
  --scribe-python "$SCRIBE_PYTHON" \
  --node "$SCRIBE_NODE" \
  --pg-bin "$QA_PG_BIN" \
  --playwright-module "$QA_PLAYWRIGHT_MODULE" \
  --playwright-test-module "$QA_PLAYWRIGHT_TEST_MODULE"
```

The runner needs permission to bind loopback HTTP ports, launch Chromium and
create a Unix socket. It initializes a new owner-only directory under
`/private/tmp/scribe-configured-core.provider.*`, disables PostgreSQL TCP
listening and uses socket port 55458. Scribe gets a unique non-superuser,
non-RLS-bypass application role. Existing database connections are never reused.
It deliberately bypasses Core's shared root test fixtures with `--confcutdir`.

## Evidence and recovery

Require exit zero and two passing browser cases, plus both passing Scribe
receiver receipts. `result.json` is written only after tests pass and the private
database stops; it records source revisions and dirty-state flags. Retain the
JUnit XML, receiver evidence, metadata-only request statuses and screenshots in
the printed directory. Inspect screenshots; a file's existence is not UI review.
Initial failures are not passing evidence. No skipped case counts as a pass.

The runner stops only its newly created cluster in `finally`, including ordinary
test failure. It preserves synthetic database files and receipts for debugging.
After abrupt termination, resolve the exact printed directory and stop only its
cluster with `pg_ctl -D <printed-directory>/data -m fast -w stop`. Never stop a
shared service or recursively delete a broad temporary directory.

Reverting this test-only change removes the harness; no app configuration,
schema source, account, deployment or billing state needs rollback. Running the
test file normally skips these cases unless explicitly enabled by the runner.
