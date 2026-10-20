# Local PR validation and inherited baseline repairs

Tier 2 for the metadata-only migration across existing billing and patient tables.
The owner requested fixes for PRs #776, #765, #547, #548, #763 and #758 and local testing and
review with Actions disabled. On 2026-10-20 the owner approved publishing the
prepared fixes and merging in the reviewed order, with every fix made before
merges. Actions, deployment and Azure mutation remain excluded. Preserve current
PR approval settings, check every exact head, and record independent Azure
review after each final substantive push before merging.
Protected source baseline: 14d98be337aab09357f418ec64a9ade045b54d2e.

The six original PR changes remain separately reviewable. Inherited test repairs
use a separate local baseline branch: an ActiveModel worker runs outside the
connection installed by the test conftest; the recording-availability test uses
the existing allowed synthetic API host; AST route fixtures include the current
response metadata and disabled encounter settings; exact billing constraint
assertions retain the already-merged asynchronous-generation and continuation
fences; the whole-OpenAPI byte pin is refreshed while the public-entitlement
schema and fixture pins and their security assertions remain unchanged.

The generated Alembic revision 80c8b7559f5f reconciles table/column comments only.
It changes no row, type, constraint, nullability, index, permission or service.
Rehearse upgrade and downgrade on the invocation-owned loopback PostgreSQL 16.9
database, compare structural catalog snapshots, run Alembic check and the
migration-graph test. The migration may acquire ordinary DDL locks and requires
a separate release decision before any deployment. Recovery of comments is its
metadata-only downgrade; application recovery is source revert. No patient or
clinical data is used in local fixtures or Azure review inputs.

Local checks use pinned Python 3.13.2, Node 22.13.0, uv 0.7.15, pnpm 9.15.9,
PostgreSQL 16.9 and Redis 6.2.18. Record exact local heads, inputs, commands,
results, findings and limitations in task-owned evidence. Independent Azure
model review is attributed to that actual model and head. It is not GitHub
approval, test execution or deployment authority. Live vendor/browser and
container prerequisites must be reported honestly rather than counted as passes.
