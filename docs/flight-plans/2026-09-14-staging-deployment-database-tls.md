# Staging deployment database TLS correction

## Outcome and scope

Tier 2: this strengthens a credential-bearing deployment connection boundary.
Resolve the exact existing staging DSN from the unchanged secret source, then
use hostname-verifying TLS with the explicit public CA bundle in the Linux
application image. This removes one known staging configuration blocker; it
does not establish synthetic data, migration, recovery or release acceptance.

The user approved prioritizing a bounded, candidate-specific catch-up over a
general-purpose recovery platform. This PR implements only the TLS prerequisite.
No new service, registry, network, resource, role or dependency is introduced.
No production file or protected workflow is changed. A source merge alone
does not authorize a staging deployment or change the stored vault value.

## Evidence and cause

The old pinned staging application uses sslmode=require. A metadata-only read
through its existing runtime confirmed PostgreSQL 16 and the sole Alembic
revision 89c31f0ae275 with verify-full and the explicit Linux CA path. The same
attempt with sslrootcert=system failed its trust setup. The image contains
/etc/ssl/certs/ca-certificates.crt; relying on the library's implicit system
location was insufficient. No live DSN, data, network or resource was changed
by those diagnostic reads. Operational output and connection material are not
included in this source change.

## Implementation and invariants

- The helper is called only after the staging backend template's first secret
  resolution block, and its stdout is captured as DATABASE_URL. Failure stops
  template sourcing. Never invoke the helper interactively with live material
  or add command tracing, output logging or a credential-bearing argv.
- Input must select exactly the staging PostgreSQL hostname, port5432 and
  database staging with nonempty URI credentials. Only require/verify-full
  and the exact known CA path are accepted; duplicate or unknown options,
  alternate hosts/databases, fragments, raw whitespace, malformed percent
  escapes, invalid UTF-8 credentials and Unicode controls/separators are refused.
- Preserve the credential authority and URI scheme. Output pins verify-full
  and /etc/ssl/certs/ca-certificates.crt; neither credential value nor vault
  configuration is rewritten. Refusals emit only a constant diagnostic.
- This is a Linux deployment DSN, not a portable laptop connection recipe.
  The accepted candidate image must contain a usable CA bundle at that path.
  Before migration, the existing live schema probe must succeed with the exact
  new image/connection. A missing or unsuitable CA fails closed; never fall
  back to require, disable verification or install an unreviewed trust root.
- Existing exact candidate/image, provider identity, schema, synthetic-data,
  recovery, independent review and human dispatch gates remain unchanged.

## Verification

The initial ten secretless unittest methods cover fixed-target normalization,
idempotence, encoded credentials, both supported URI schemes, target/port
refusals, duplicate/unknown/weaker TLS options, control characters, bounded
CLI errors and staging-only template integration. Four corrective methods add
raw/encoded Unicode controls/separators, malformed percent escapes, invalid
UTF-8, constant CLI refusals and valid encoded credential preservation. Only
those four new methods and the one affected prior control-character method
are rerun for this correction. No application/conftest,
network, real credential, migration or existing passing test is invoked.
Required protected current-head checks and independent exact final-source
review must pass before normal source merge.

## Release and recovery

Use the current protected-master controller only after this exact change is
reviewed and normally merged. The application candidate remains separately
selected and immutable. Its initial catch-up still has four migration files
and is not a routine code-only release. Existing populated staging records
must have independently approved synthetic/workforce provenance before any
copy or migration; this helper supplies no such approval.

If resolution fails, no deployment is authorized. If post-publication verified
database access fails, stop before migrations; preserve existing revisions,
data and evidence. Do not overwrite a published candidate tag or replace its
bytes. Correct source/configuration under a fresh reviewed candidate/attempt.
Do not weaken TLS as a rollback. Live resource/configuration changes and the
bounded migration/recovery procedure require their separate exact review and
human execution authorization. General recovery automation and cleanup stay
outside this PR.
