# Restore the saved API snapshot used by the approved backend build

Tier 2 because the generated snapshot describes an existing billing path. No endpoint implementation, authorization, session, consent, runtime configuration, or database structure changes in this PR.

The approved backend candidate's immutable image build fails because `web/openapi.json` predates its existing continuation route. The build regenerates the browser client from that saved file and cannot export `billingHeaderGenerationContinue`, which the existing UI imports. Regenerate the saved snapshot from the actual `internal_api_app`, using the established isolated synthetic bootstrap with all socket connections and DNS forbidden. The result adds the already implemented continuation route and its schemas, status purpose, and default-false transport fields.

Validate the exact committed snapshot with API regeneration equivalence, the existing five focused CAB browser suites, full web TypeScript, and a production-bundled staging-target browser build. Perform implementer review and record exact source, commands, results, and disposition in the PR under the owner's September 25 policy. Keep this correction separate from runtime implementation.

Recovery: revert the generated snapshot through a PR; that restores the known broken build for this candidate. No migration rung applies to this snapshot-only correction. The release still requires the existing immutable merged-PR/source controller, full database rehearsal, current runtime/schema reconciliation, and retained-image recovery. No CAB activation or production promotion is selected.
