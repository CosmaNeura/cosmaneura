# Synthetic Zoom note evaluation tools

Tier 1: additive offline fixtures/oracles only. No application, model, schema,
deployment, authentication or Session Prep change. GitHub Actions stays disabled.

The fixtures preserve synthetic named-participant model diagnostics using the
actual A/B SOAP templates/examples. Those labels are classifier output, not proof
of a live Recall participant identity. Original failed outputs are not repaired.
`observedFinalRequestArtifactSHA256` hashes the whole retained `call-4.attempt.json`
file (request plus attempt metadata), not its prompt string. Default prompt hashes
bind pre-pilot source `624f0c98179405386e97a663013347f8194cdd9f`.

The prose oracle detects listed known errors; it missed broad unsupported clinical
exclusions during actual evaluation. A finite pass is never a clinical certificate.
The selection oracle adds pinned source-index coverage and correction/context
dependencies within each field for R2/R3. Omitting facts can fail even when every emitted word is
verbatim source. These declarative fixture rules are evaluation only: no runtime
code imports them or uses test truth to repair a note. Other cases, unknown source
hashes, section usefulness and unlisted context dependencies need separate review.

Validation: run both `tests/job/zoom_bot_*oracle_test.py` with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `--noconftest`; they
import only local fixture/oracle code. No database, model, provider or audio calls.
Final pushed-head independent review is required before merge. Recovery removes
only these tooling files; existing application behavior and notes remain untouched.
