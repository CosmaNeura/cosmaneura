# Azure diagnostic output redaction

Tier 2: the owner authorized removing credential material and sensitive diagnostic
values from `debug_azure_blob.py`. CodeQL alert 7 identifies the key-output path.
The tool also echoes credential-bearing URLs, resource identifiers, remote
metadata and raw SDK errors; malformed CLI arguments echo their input values.

Replace configuration values with configured/missing status. Emit fixed error
categories, booleans and bounded counts; do not print key fragments, account
URLs, container/prefix/blob identifiers, returned metadata or exception details.
Keep configuration failures inside the redacted error boundary and make parser
errors value-free. Existing successful/missing-config/operation-error exit codes
remain 0/2/1; invalid arguments retain exit 2 and help retains exit 0.

Keep the existing read-only container, optional blob existence/properties and
bounded listing checks. Actual target/credential/filter values still reach the
SDK unchanged. Listing now reports counts rather than names and properties report
readability rather than metadata. Configuration precedence is unchanged. This
does not change application authorization, Azure settings, dependencies, schema,
deployment workflows or release cohorts.

Prove regressions against the original output first. Use only synthetic sentinels
and a fake SDK with network calls blocked. Cover successful and missing-config
paths, credential-bearing URLs, early and late SDK failures, configuration
exceptions and parser errors. Run type/lint checks and independently review the
final pushed commit; record findings and dispositions on the narrow PR. A passing
PR CodeQL scan does not establish default-branch alert closure; verify that after
the separate merge and default-branch reanalysis. Never execute this diagnostic
against a real Azure account as part of qualification.

If the new output prevents diagnosis, stop using this tool and prepare a reviewed
safe diagnostic improvement. A reviewed revert or prior source is possible, but
restores sensitive output and must not be run against real credentials. No cloud
rollback, migration/rehearsal, backfill or feature-flag rollback applies. No merge,
deployment, production operation or cloud mutation is authorized here.
