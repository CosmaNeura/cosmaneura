# Bounded capture retention evaluation

This is a synthetic model diagnostic, not a rebuilt-client or end-to-end test.
`run.py` calls the actual `ConfiguredNoteModel`, existing structured prompt,
response envelope, `PlanGrounding.resolve`, and final note validator. It constructs
unsaved Core objects and uses no encounter query, live database, or saved-note
regeneration. The isolated authority-test bootstrap replaces unrelated services;
the configured model request is live, with the existing 30-second timeout and no
SDK retries. Never run the repository-wide conftest for this diagnostic: its
session hooks can truncate databases and delete test users.

Fixtures are invented visits, retaining every observed saved ASR passage from
the October 24 synthetic R2/R3 runs, with all observed roles Unknown. The 27 schema
headings match the observed note, but schema guidance/customization are explicit
synthetic inputs. This does not qualify an unobserved provider customization.

From the repository root, after restoring the locked environment:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  --confcutdir=tools/capture-retention tools/capture-retention/test_oracle.py -q

# Supply the already-authorized staging key in process environment; never echo it.
AZURE_OPENAI_ENDPOINT=https://openaitestvhs.openai.azure.com/ \
  .venv/bin/python tools/capture-retention/run.py \
  --output /absolute/owned/results --batch primary
# Run edges only after primary results have been reviewed.
AZURE_OPENAI_ENDPOINT=https://openaitestvhs.openai.azure.com/ \
  .venv/bin/python tools/capture-retention/run.py \
  --output /absolute/owned/edge-results --batch edges
# Separate constructed known-role cases, including a clinician's own history.
AZURE_OPENAI_ENDPOINT=https://openaitestvhs.openai.azure.com/ \
  .venv/bin/python tools/capture-retention/run.py \
  --output /absolute/owned/known-role-results --batch edges --roles known
# The same bounded edge cases with a compact eight-field schema including ROS.
AZURE_OPENAI_ENDPOINT=https://openaitestvhs.openai.azure.com/ \
  .venv/bin/python tools/capture-retention/run.py \
  --output /absolute/owned/ros-results --batch edges --ros
```

Primary mode makes 12 calls: full R2/R3 × baseline/candidate × three repetitions.
Edge mode makes eight candidate calls. The default 61-second interval bounds load
on the shared staging model. No automatic retry conceals model or transport
failures. A 401/403/404 stops the batch with a preserved failure receipt.

Resume by repeating the command with the same source and output directory. Existing
receipts are reused only with identical source/diff, fixture, schema, customization,
model, grounding and relevant code identities. A changed input needs a new directory.
An exclusive process lock prevents competing runs. Each attempt is written before
the request: an interrupted attempt with no result blocks automatic resubmission.
The manifest preserves complete synthetic inputs and candidate grounding. Each
attempt also retains its actual source entries, including constructed role variants,
before making a request. Files are
created exclusively with mode 0600 and contain synthetic model output; do not
substitute clinical transcripts. Console output contains metadata only. Cleanup
requires no external action: this lane creates no visits, participants, service
resources, or saved clinical records.

`tests/authority_kernel/scribe_capture_retention_model_test.py` adds an opt-in separate
actual-model-to-local-persistence check. It requires the existing guarded
`W8_CORE_CAPTURE_TEST_URL` disposable database, `SCRIBE_RETENTION_MODEL_KEY` in
process memory, and a fresh absolute `SCRIBE_RETENTION_OUTPUT` JSON path. Run it
with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p
pytest_asyncio.plugin --confcutdir=tests/authority_kernel` and that test filename.
It records the final stored draft, validates semantic retention, reopens it, and
checks a prepare retry makes no second model call. It uses a synthetic downstream
capture fixture, so this is not a native or rebuilt-client recording test.

The finite oracle checks clinically relevant propositions in narrative fields;
copies in prospective plans or metadata cannot satisfy missing history. It is
conservative about unfamiliar paraphrases and must be accompanied by independent
content review. Its fixtures prove that the archived omissions, wrong numbers,
wrong symptom timing, negation inversions, and person swaps fail. An oracle PASS
alone never establishes universal note completeness or deployment readiness.

If an independently justified oracle correction is needed, preserve original
receipts and run `grade_saved.py --run /absolute/primary-run --output
/absolute/new-regrade.json`. It requires all 12 primary results, records their
hashes and both original/current assessments, and fails if any candidate remains
failed. Regrading makes no model request and cannot turn a missing/failed model
response into a pass. Independent review must approve the oracle correction and
actual content; never overwrite the original results.
