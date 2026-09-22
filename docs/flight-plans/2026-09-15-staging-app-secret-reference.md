# Staging app secret-reference response compatibility

Tier 2: validation of the staging application's database-secret reference.

Read-only preparation fails when Azure returns DATABASE_URL with the expected
secretRef and an empty value string. The September 15 app GET had precisely that
shape; the matching revision omitted value. The old validator accepted only an
omitted or null value and therefore refused the app before preparing a snapshot.

Accept an empty string alongside the exact database-url secret reference in both
app and revision templates. Continue rejecting nonempty strings, other value
types, missing or different secret references, duplicate environment names,
unexpected containers, images or revisions. Hash the original app and revision
templates unchanged so later drift is still detected. This changes no Azure
configuration, token linkage, expiry, human-only execution, or review gate.

The new invented-data regression reproduces the old refusal. Four targeted tests
cover empty and null values, both templates, nonempty/nonstring rejection and
mandatory exact references; the production pin check verifies the updated Azure,
loader and runtime bundle together. No real CLI, cache or HTTP I/O runs in tests.
Independent final-head review and normal protected merge precede a new command.

Recovery is a normal reviewed revert. Preserve both failed private sessions and
the completed encrypted workspace. Neither an old command nor an old source
review authorizes execution of this changed bundle.
