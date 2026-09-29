# Provider edits to note template headings

Tier 2: note-generation and saved-note editing paths. One additive Core release;
no migration, dependency, credential, cohort or service change.

Problem: extraction from an example can import a date or attestation as a
required heading. Providers can now edit existing headings and guidance in
Settings > Note Types. Loading is on demand; saving is explicit. No automatic
renaming or template backfill occurs. Existing Core authentication, practice
access and support-view restrictions apply to the nested internal routes.

The server locks the provider-owned, nondeleted schema, compares an ordered
content revision, and rejects competing updates. Identical replay recovers a
lost save response. Bounds and unique headings prevent silent dictionary loss;
section count is preserved. Examples and notes are not rewritten. Existing
Scribe notes validate later edits against their own saved field structure,
while retaining source, version, ownership and consent checks.

Validation: generated OpenAPI/client and route types; focused frontend tests,
TypeScript, Ruff/Pyright, real disposable PostgreSQL endpoint concurrency and
Scribe note-edit/prepare regressions. No live model, clinical data, microphone,
staging database mutation, or marking notes clinically reviewed is involved.
Exact commands/results and the tested commit belong in the PR evidence.

Release backend and generated frontend together. The editor is an explicit
opt-in action in existing settings. Observe ordinary request success/conflict
rates without logging template content. Revert the editor/routes to remove the
feature. Keep the saved-note compatibility fix if any headings have been edited;
reverting that fix can block old-note edits until template headings are restored.
Recover a template by saving its prior headings through the same explicit UI;
never rewrite saved notes or silently restore someone else's template.

This enables deliberate correction, not automatic clinical factuality. It does
not fix unsupported claims generated inside note bodies. Existing example
content remains intact; legacy generation may still consult old examples.
