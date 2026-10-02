// Offline checks of a draft only. No runtime receiver or dependency is installed.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

// Resolve an explicitly supplied, already-installed AJV runtime. Never download.
const dependencyRoot = process.env.VISIT_PREP_SCHEMA_DEPENDENCY_ROOT;
assert.ok(dependencyRoot, "provide the existing schema dependency directory");
const require = createRequire(join(dependencyRoot, "package.json"));
const Ajv2020 = require("ajv/dist/2020.js").default;
const addFormats = require("ajv-formats").default;
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const directory = join(root, "docs/contracts/visit-prep-companion-v1");
const read = (name) => JSON.parse(readFileSync(join(directory, name), "utf8"));
const ajv = new Ajv2020({
  strict: true,
  allowUnionTypes: true,
  allErrors: true,
});
addFormats(ajv);
const request = ajv.compile(read("request.schema.json"));
const result = ajv.compile(read("result.schema.json"));
const ready = () => read("fixtures/result-ready-unlinked.json");

test("draft fixtures validate with closed strict schemas", () => {
  assert.equal(request(read("fixtures/request-unlinked.json")), true);
  assert.equal(result(ready()), true, ajv.errorsText(result.errors));
});

test("request rejects authority selectors, credentials and other actions", () => {
  for (const extra of [
    "patient_id",
    "appointment_id",
    "capabilities",
    "token",
    "consent",
    "model_output",
  ]) {
    const value = read("fixtures/request-unlinked.json");
    value[extra] = "not-authority";
    assert.equal(request(value), false, extra);
  }
  for (const [key, value] of [
    ["action", "visit-prep.send"],
    ["purpose", "session_prep"],
    ["contract_version", "2"],
    ["workspace_id", "bad"],
    ["preparation_ref", "appointment_01h45ytscbebyvny4gc8cr8ma2"],
  ]) {
    const sample = read("fixtures/request-unlinked.json");
    sample[key] = value;
    assert.equal(request(sample), false, key);
  }
});

test("every object rejects unexpected fields and missing required keys", () => {
  const paths = [
    [],
    ["package"],
    ["package", "sources"],
    ["package", "sources", "preparation"],
    ["package", "sources", "basis"],
    ["package", "sources", "topics", 0],
    ["package", "sources", "topics", 2, "choices", 0],
    ["package", "brief"],
    ["package", "brief", "headline"],
    ["package", "brief", "headline", "evidence", 0],
  ];
  for (const path of paths) {
    const value = ready();
    let object = value;
    for (const key of path) object = object[key];
    object.unexpected = "not-permitted";
    assert.equal(result(value), false, path.join("/"));
    delete object.unexpected;
    delete object[Object.keys(object)[0]];
    assert.equal(result(value), false, path.join("/"));
  }
});

test("stale and missing states cannot carry any retained brief or artifact", () => {
  for (const status of ["stale", "missing"]) {
    const value = ready();
    Object.assign(value.package, {
      status,
      build_status: "pending",
      artifact_id: null,
      version: null,
      recorded_at: null,
      brief: null,
    });
    assert.equal(result(value), true, ajv.errorsText(result.errors));
    for (const key of ["artifact_id", "version", "recorded_at", "brief"]) {
      const changed = structuredClone(value);
      changed.package[key] = ready().package[key];
      assert.equal(result(changed), false, key);
    }
    value.package.build_status = "ready";
    assert.equal(result(value), false);
  }
  for (const key of ["artifact_id", "version", "recorded_at", "brief"]) {
    const value = ready();
    value.package[key] = null;
    assert.equal(result(value), false, key);
  }
});

test("limits reject overflow and invalid field types without truncation", () => {
  const edits = [
    (x) => {
      x.package.sources.topics[0].answer = "x".repeat(2001);
    },
    (x) => {
      x.package.sources.topics[0].prompt = "x".repeat(1001);
    },
    (x) => {
      x.package.brief.headline.evidence[0].quote = "x".repeat(301);
    },
    (x) => {
      x.package.brief.headline.text = "x".repeat(701);
    },
    (x) => {
      x.package.sources.basis.original_response_count = true;
    },
    (x) => {
      x.package.sources.current_answers_complete = 1;
    },
    (x) => {
      x.package.sources.topics.push(
        ...structuredClone(x.package.sources.topics),
      );
    },
    (x) => {
      x.checked_at = "2026-10-01T12:00:00";
    },
    (x) => {
      x.package.version = 0;
    },
    (x) => {
      x.package.sources.source_version = "bad";
    },
  ];
  for (const edit of edits) {
    const value = ready();
    edit(value);
    assert.equal(result(value), false);
  }
});

test("partial caregiver and all-skipped schemas preserve honest report states", () => {
  const partial = ready();
  Object.assign(partial.package.sources, {
    actor_role: "caregiver",
    original_interview_completed: false,
    current_answers_complete: false,
    current_answers_reviewed: false,
    current_review_id: null,
    reviewed_at: null,
    missing_question_keys: ["story"],
  });
  partial.package.sources.basis = {
    original_response_count: 3,
    event_sequence: 0,
  };
  partial.package.sources.preparation.lifecycle_state = "in_progress";
  Object.assign(partial.package.sources.topics.at(-1), {
    response_kind: null,
    answer: null,
    initial_response_kind: null,
    initial_answer: null,
    original_response_id: null,
    recorded_at: null,
    initial_recorded_at: null,
  });
  Object.assign(partial.package, {
    status: "missing",
    build_status: "not_requested",
    artifact_id: null,
    version: null,
    recorded_at: null,
    brief: null,
  });
  assert.equal(result(partial), true, ajv.errorsText(result.errors));
  const skipped = ready();
  skipped.package.sources.skipped_question_keys =
    skipped.package.sources.topics.map((topic) => topic.question_key);
  for (const topic of skipped.package.sources.topics)
    Object.assign(topic, {
      response_kind: "skipped",
      answer: null,
      initial_response_kind: "skipped",
      initial_answer: null,
    });
  skipped.package.brief = {
    headline: null,
    summary: [],
    visit_priorities: [],
    medication_and_adherence: [],
    barriers: [],
    uncertainties: [],
  };
  assert.equal(result(skipped), true, ajv.errorsText(result.errors));
  assert.equal(skipped.package.sources.preparation.patient_id, null);
  assert.equal(skipped.package.sources.preparation.appointment_id, null);
});

test("corrected patient wording keeps the original and uses only the current source", () => {
  const value = ready();
  const story = value.package.sources.topics.at(-1);
  const correction = "visit_prep_contribution_01h45ytscbebyvny4gc8cr8mab";
  Object.assign(story, {
    answer: "Invented corrected story: I changed my evening routine.",
    changed: true,
    current_revision_id: correction,
    recorded_at: "2026-10-01T11:15:00Z",
  });
  Object.assign(value.package.sources, {
    source_version: "c".repeat(64),
    current_answers_reviewed: false,
    current_review_id: null,
    reviewed_at: null,
    basis: { original_response_count: 4, event_sequence: 2 },
  });
  Object.assign(value.package, {
    artifact_id: "visit_prep_brief_01h45ytscbebyvny4gc8cr8mac",
    version: 2,
    recorded_at: "2026-10-01T11:45:00Z",
  });
  value.package.brief.headline = {
    text: "Patient reports a changed evening routine.",
    evidence: [
      { source_ref: correction, quote: "I changed my evening routine." },
    ],
  };
  assert.equal(result(value), true, ajv.errorsText(result.errors));
  assert.match(story.initial_answer, /morning routine/);
  assert.notEqual(story.current_revision_id, story.original_response_id);
});

test("schemas are not mistaken for semantic authority validation", () => {
  // This intentionally schema-valid wrong lineage must be rejected by domain
  // and receiver/client checks, not silently blessed by schema acceptance.
  const value = ready();
  value.preparation_ref = "visit_prep_01h45ytscbebyvny4gc8cr8ma3";
  assert.equal(result(value), true);
  const draft = readFileSync(join(directory, "draft.md"), "utf8");
  assert.match(draft, /Draft for owner review; not ratified/);
  assert.match(draft, /package IDs match/);
  assert.match(draft, /Ratification precedes receiver implementation/);
});
