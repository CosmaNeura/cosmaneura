# CAB async action type-flow correction

Risk: Tier 2 because the existing route is a billing path. The correction preserves behavior and changes no billing authority, authentication, consent, configuration, schema, or inference contract.

The owner approved the prepared correction on October 8, 2026: “Ok, I approve the prepared CAB correction.” The approved patch was prepared against `e228003836f58870bac5530db9faa9ea8a0e2538`, the exact merge of Core PR 697. Its SHA-256 is `3109986833e6bb0c71a6a56304afceef57cc9233b7f8d3209474c724630900ae`.

Pyright reports possibly unbound `replay`, `original`, and `generation_action_id` variables in `app/routes/cab_bff.py`. Store the existing return tuple from `begin_async_action` in an initialized optional `async_action`, and narrow that tuple directly in the two later async-only branches. A successful async call returns a tuple; a non-async call retains `None`. Exceptions, replay submission rules, source revalidation, completion receipts, and cleanup retain their existing behavior.

Validate the exact committed SHA with the four existing billing test files against only the owned synthetic PostgreSQL lab, Ruff lint and formatting, and Pyright on the affected CAB production modules. Run the existing focused Scribe authority suites to qualify the combined backend candidate. Perform implementer review and record commands, results, findings, and disposition in the PR under the owner's September 25 local-validation policy. Passing in-scope work may merge under that standing authority.

Recovery: revert the code refactor through a PR. No migration rung applies to this correction. This PR does not activate CAB, change runtime latches, publish an image, or deploy an environment. A new merge changes the immutable backend release candidate; the separately approved Scribe backend release must bind that candidate and qualify its full schema and runtime drift before execution.
