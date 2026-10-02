import { z } from "zod"

import type { BillingHeaderSource } from "~/configuration/client"

export const cabFactKeys = [
  "visit_type",
  "patient_type",
  "modality",
  "patient_location",
  "place_of_service",
  "provider_location",
  "consent",
  "visit_duration",
  "em_time",
] as const
const text = (max: number) => z.string().max(max)
const hash = z.string().regex(/^[a-f0-9]{64}$/)
const time = z.string().refine((value) => Number.isFinite(Date.parse(value)))
const actor = z
  .object({
    id: z.string().uuid(),
    name: text(100),
    role: z.enum(["provider", "coder", "admin"]),
    identity: z
      .object({
        kind: z.literal("core"),
        issuer: text(500),
        subject: text(500),
        projectionId: z.string().uuid(),
        mappingVersion: z.number().int().positive(),
      })
      .passthrough()
      .optional(),
  })
  .passthrough()
export const cabSourcePin = z
  .object({
    encounterId: text(128),
    encounterVersion: z.number().int().positive(),
    ingestionSource: z.literal("provider_completed_note"),
    noteId: text(160),
    noteVersion: text(40),
    noteSha256: hash,
    bundleSha256: hash,
    supportingSourceDigests: z.array(z.unknown()).max(0),
  })
  .strict()
const evidence = z
  .object({
    id: text(160),
    source: z.literal("note"),
    sourceId: text(160),
    sourceVersion: text(40),
    coordinateSystem: z.literal("unicode_code_point"),
    start: z.number().int().nonnegative(),
    end: z.number().int().positive(),
    text: text(2000),
  })
  .passthrough()
const code = z
  .object({
    id: text(160),
    kind: z.enum(["EM", "ICD10"]),
    code: text(15),
    label: text(150),
    evidenceIds: z.array(text(160)).max(320),
    mapping: z.record(z.unknown()).optional(),
  })
  .passthrough()
export const cabDraft = z
  .object({
    facts: z
      .object(
        Object.fromEntries(
          cabFactKeys.map((key) => [key, text(600)]),
        ) as Record<(typeof cabFactKeys)[number], z.ZodString>,
      )
      .strict(),
    codes: z.array(code).max(20),
    note: text(2000),
    diagnosisOrderConfirmed: z.boolean().optional(),
  })
  .strict()
const revision = z
  .object({
    headerId: z.string().uuid(),
    number: z.number().int().positive().max(100),
    expectedSource: cabSourcePin,
    actor,
    createdAt: time,
    reason: text(600),
    reviewStatus: z.literal("unreviewed"),
    draft: cabDraft,
    history: z
      .array(
        z
          .object({
            id: z.string().uuid(),
            number: z.number().int().positive(),
            actor,
            createdAt: time,
            reason: text(600),
            changes: z
              .array(
                z
                  .object({
                    path: text(180),
                    label: text(150),
                    before: z.unknown(),
                    after: z.unknown(),
                  })
                  .passthrough(),
              )
              .max(51),
          })
          .passthrough(),
      )
      .max(100),
  })
  .passthrough()
const reviewEvent = z
  .object({
    id: text(40),
    subject: text(200),
    status: z.enum(["counted", "duplicate", "held"]),
    reason: text(700),
    evidence_complete: z.boolean(),
    evidence_spans: z
      .array(
        z
          .object({
            source: z.literal("signed_note"),
            text: text(2000),
            loc: z
              .object({
                start: z.number().int().nonnegative(),
                end: z.number().int().positive(),
              })
              .strict(),
          })
          .strict(),
      )
      .max(5),
  })
  .passthrough()
const reviewSection = z
  .object({
    rule_version: text(100),
    level: z.enum(["straightforward", "low", "moderate", "high"]),
    evidence_complete: z.boolean(),
    events: z.array(reviewEvent).max(40),
  })
  .passthrough()
const header = z
  .object({
    schemaVersion: z.literal("provider-billing-header.v1"),
    headerId: z.string().uuid(),
    expectedSource: cabSourcePin,
    createdBy: actor,
    generatedAt: time,
    reviewStatus: z.literal("unreviewed"),
    facts: z
      .array(
        z
          .object({
            key: z.enum(cabFactKeys),
            label: text(100),
            value: text(600).nullable(),
            status: z.enum(["documented", "not_documented", "conflicting"]),
            evidenceIds: z.array(text(160)).max(320),
          })
          .passthrough(),
      )
      .length(9),
    suggestions: z
      .array(
        code.extend({
          basis: text(700),
          reviewStatus: z.literal("unreviewed"),
        }),
      )
      .max(20),
    evidence: z.array(evidence).max(320),
    flags: z
      .array(
        z
          .object({
            id: text(160),
            message: text(700),
            evidenceIds: z.array(text(160)).max(320).optional(),
          })
          .passthrough(),
      )
      .max(30),
    policy: z
      .object({ status: z.literal("not_verified"), message: text(700) })
      .passthrough(),
    dataReview: reviewSection.optional(),
    clinicalReview: z
      .object({
        rule_version: text(100),
        problems: reviewSection,
        risk: reviewSection,
      })
      .passthrough()
      .optional(),
    generation: z
      .object({ inferenceMode: text(80), runId: z.string().uuid() })
      .passthrough(),
  })
  .passthrough()
export const cabView = z
  .object({
    header,
    revisionNumber: z.number().int().nonnegative().max(100),
    revision: revision.nullable(),
    signoff: z
      .object({
        id: z.string().uuid(),
        reviewId: z.string().uuid(),
        headerId: z.string().uuid(),
        headerRevision: z.number().int().nonnegative(),
        expectedSource: cabSourcePin,
        actor,
        status: z.literal("recorded"),
        createdAt: time,
      })
      .passthrough()
      .nullable()
      .optional(),
    previousSignoff: z
      .object({
        reviewId: z.string().uuid(),
        headerRevision: z.number().int().nonnegative(),
        actor,
        createdAt: time,
      })
      .passthrough()
      .nullable()
      .optional(),
  })
  .passthrough()
export const cabSource = z
  .object({
    encounterId: text(128),
    encounterVersion: z.number().int().positive(),
    headerSource: cabSourcePin,
    noteText: text(200_000),
    intake: z.literal(""),
    transcript: z.literal(""),
    completion: z
      .object({
        status: z.literal("completed"),
        confirmedByProvider: z.literal(true),
        completedAt: time,
      })
      .strict(),
    visitContext: z
      .object({
        patientAge: z.number().int().min(0).max(130),
        patientSex: z.enum(["M", "F", "other", "unknown"]),
        encounterType: z.enum([
          "office_new",
          "office_established",
          "telehealth_new",
          "telehealth_established",
        ]),
        placeOfService: z.enum(["11", "02", "10"]),
        providerSpecialty: text(100),
        serviceDate: z
          .string()
          .regex(/^\d{4}-\d{2}-\d{2}$/)
          .nullable()
          .optional(),
      })
      .strict(),
    projection: z
      .object({
        version: z.literal("ordered-note-markdown.v1"),
        excludedRecordingTime: z.boolean(),
        contentSha256: hash,
        textSha256: hash,
      })
      .passthrough(),
    inferencePolicy: z
      .object({ allowed: z.boolean(), reason: text(200).nullable().optional() })
      .passthrough(),
  })
  .passthrough()

export type CabDraft = z.infer<typeof cabDraft>
export type CabView = z.infer<typeof cabView>
export type CabSource = z.infer<typeof cabSource>
export type CabAction = "accept" | "read" | "generate" | "save"
export type CabCommand = {
  expectedSource: z.infer<typeof cabSourcePin> | null
  encounterId?: string
  operationId?: string
  headerId?: string
  expectedRevision?: number
  draft?: CabDraft
  reason?: string
}
export type CabRequest = (
  action: CabAction,
  command: CabCommand,
) => Promise<unknown>
const canonical = (value: unknown): string =>
  JSON.stringify(value, (_key, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(
          Object.entries(item).sort(([a], [b]) => a.localeCompare(b)),
        )
      : item,
  )
export const sameCabValue = (a: unknown, b: unknown) =>
  canonical(a) === canonical(b)
const requireValue = (condition: unknown) => {
  if (!condition) throw new Error("Unconfirmed billing response")
}

export async function readCabResponse(
  input: unknown,
  action: CabAction,
  operationId: string,
  expected: BillingHeaderSource["source"],
  pin?: CabSource["headerSource"],
) {
  const wrapper = z
    .object({
      schemaVersion: z.literal("core-cab-result.v1"),
      purpose: text(80),
      sourceOperationId: z.literal(operationId),
      requestSha256: hash,
      source: cabSource,
      result: z.record(z.unknown()),
    })
    .passthrough()
    .parse(input)
  requireValue(
    wrapper.purpose ===
      (action === "accept" ? "completed-note.accept" : "header." + action),
  )
  const source = wrapper.source
  requireValue(
    source.projection.contentSha256 === expected.contentSha256 &&
      source.projection.textSha256 === expected.textSha256 &&
      source.projection.excludedRecordingTime ===
        expected.excludedRecordingTime &&
      source.headerSource.noteSha256 === expected.textSha256 &&
      source.headerSource.encounterId === source.encounterId &&
      source.headerSource.encounterVersion === source.encounterVersion &&
      (!pin || sameCabValue(pin, source.headerSource)),
  )
  const actualHash = Array.from(
    new Uint8Array(
      await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(source.noteText),
      ),
    ),
  )
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")
  requireValue(actualHash === expected.textSha256)
  if (action === "accept") {
    const receipt = z
      .object({
        operationId: z.literal(operationId),
        encounterId: z.literal(source.encounterId),
        encounterVersion: z.literal(source.encounterVersion),
        headerSource: cabSourcePin,
      })
      .passthrough()
      .parse(wrapper.result.receipt)
    requireValue(
      wrapper.result.current === true &&
        typeof wrapper.result.replayed === "boolean" &&
        sameCabValue(receipt.headerSource, source.headerSource),
    )
    return { source, view: null }
  }
  const view =
    wrapper.result.view === null ? null : cabView.parse(wrapper.result.view)
  if (action === "generate" || action === "save")
    requireValue(view && typeof wrapper.result.created === "boolean")
  if (!view) return { source, view }
  const h = view.header
  requireValue(sameCabValue(h.expectedSource, source.headerSource))
  const refs = new Set(h.evidence.map((row) => row.id))
  requireValue(refs.size === h.evidence.length)
  const characters = Array.from(source.noteText)
  for (const row of h.evidence)
    requireValue(
      row.sourceId === source.headerSource.noteId &&
        row.sourceVersion === source.headerSource.noteVersion &&
        row.end > row.start &&
        row.end <= characters.length &&
        characters.slice(row.start, row.end).join("") === row.text,
    )
  const validRefs = (ids: string[], minimum = 0) =>
    ids.length >= minimum &&
    new Set(ids).size === ids.length &&
    ids.every((id) => refs.has(id))
  requireValue(
    new Set(h.facts.map((row) => row.key)).size === cabFactKeys.length,
  )
  for (const fact of h.facts)
    requireValue(
      fact.status === "not_documented"
        ? fact.value === null && fact.evidenceIds.length === 0
        : fact.value?.trim() &&
            validRefs(fact.evidenceIds, fact.status === "conflicting" ? 2 : 1),
    )
  for (const row of h.suggestions)
    requireValue(validRefs(row.evidenceIds, 1) && !row.mapping)
  for (const row of h.flags)
    requireValue(!row.evidenceIds || validRefs(row.evidenceIds, 1))
  for (const section of cabReviewSections(view)) {
    for (const event of section.review.events) {
      requireValue(!event.evidence_complete || event.evidence_spans.length > 0)
      requireValue(event.status !== "counted" || event.evidence_complete)
      for (const span of event.evidence_spans)
        requireValue(
          span.loc.end > span.loc.start &&
            span.loc.end <= characters.length &&
            characters.slice(span.loc.start, span.loc.end).join("") ===
              span.text,
        )
    }
  }
  if (view.revisionNumber === 0) requireValue(view.revision === null)
  else {
    const r = view.revision
    requireValue(
      r &&
        r.number === view.revisionNumber &&
        r.headerId === h.headerId &&
        sameCabValue(r.expectedSource, source.headerSource) &&
        r.history.length === r.number,
    )
    for (const row of r!.draft.codes)
      requireValue(validRefs(row.evidenceIds, 1))
  }
  if (view.signoff)
    requireValue(
      view.signoff.headerId === h.headerId &&
        view.signoff.headerRevision === view.revisionNumber &&
        sameCabValue(view.signoff.expectedSource, source.headerSource) &&
        ["coder", "admin"].includes(view.signoff.actor.role),
    )
  if (view.previousSignoff)
    requireValue(
      !view.signoff &&
        view.previousSignoff.headerRevision < view.revisionNumber,
    )
  return { source, view }
}

export function cabReviewSections(view: CabView) {
  const h = view.header
  return [
    ...(h.dataReview ? [{ label: "Data reviewed", review: h.dataReview }] : []),
    ...(h.clinicalReview
      ? [
          { label: "Problems addressed", review: h.clinicalReview.problems },
          { label: "Management risk", review: h.clinicalReview.risk },
        ]
      : []),
  ]
}
export function editableCabHeader(view: CabView): CabDraft {
  return structuredClone(
    view.revision?.draft ?? {
      facts: Object.fromEntries(
        view.header.facts.map((row) => [row.key, row.value ?? ""]),
      ) as CabDraft["facts"],
      codes: view.header.suggestions.map(
        ({ id, kind, code, label, evidenceIds }) => ({
          id,
          kind,
          code,
          label,
          evidenceIds,
        }),
      ),
      note: "",
      diagnosisOrderConfirmed: false,
    },
  )
}
export function changeCabCodes(
  draft: CabDraft,
  codes: CabDraft["codes"],
): CabDraft {
  const diagnoses = (rows: CabDraft["codes"]) =>
    rows.filter((row) => row.kind === "ICD10")
  return {
    ...draft,
    codes,
    diagnosisOrderConfirmed:
      draft.diagnosisOrderConfirmed === true &&
      sameCabValue(diagnoses(draft.codes), diagnoses(codes)),
  }
}
export function cabProviderExport(view: CabView, source?: CabSource): string {
  const h = view.header,
    draft = editableCabHeader(view)
  const lines = [
    "BILLING HEADER",
    "Advisory proposals for coder review. Clinical note unchanged.",
    "Header: " + h.headerId,
    "Revision: " + view.revisionNumber,
    "Source SHA-256: " + h.expectedSource.noteSha256,
    view.signoff
      ? "Coder review recorded for this revision."
      : "Coder review pending.",
    "",
    "VISIT FACTS",
  ]
  if (source) {
    requireValue(sameCabValue(source.headerSource, h.expectedSource))
    const visit = source.visitContext
    lines.splice(
      lines.length - 2,
      0,
      "",
      "CONFIRMED VISIT DETAILS",
      "Note status: Provider-confirmed completed note",
      "Service date: " + (visit.serviceDate || "Not specified"),
      "Visit setting: " + visit.encounterType.replaceAll("_", " "),
      "Place of service: " + visit.placeOfService,
      "Provider specialty: " + visit.providerSpecialty,
      "Age at visit: " + visit.patientAge,
      "Documented sex: " + visit.patientSex,
    )
  }
  for (const fact of h.facts) {
    lines.push(
      fact.label +
        ": " +
        (draft.facts[fact.key] || "Not documented / unspecified") +
        (fact.evidenceIds.length
          ? " [" + fact.evidenceIds.join(", ") + "]"
          : ""),
    )
    if (draft.facts[fact.key] !== (fact.value ?? ""))
      lines.push("  Human edit. Original: " + (fact.value || "Not documented"))
  }
  lines.push("", "CURRENT CODE PROPOSALS — require coder review")
  for (const row of draft.codes)
    lines.push(
      row.kind +
        " " +
        row.code +
        ": " +
        row.label +
        " [" +
        row.evidenceIds.join(", ") +
        "]",
    )
  const diagnoses = draft.codes.filter((row) => row.kind === "ICD10")
  lines.push(
    draft.diagnosisOrderConfirmed && diagnoses.length
      ? "Human proposed primary/order: " +
          diagnoses.map((row) => row.code).join(" → ") +
          ". Separate coder acceptance required."
      : "Human primary/order: unconfirmed.",
  )
  if (draft.note) lines.push("Provider/reviewer note: " + draft.note)
  lines.push("", "ORIGINAL ENGINE SUGGESTIONS")
  for (const row of h.suggestions)
    lines.push(
      row.code +
        ": " +
        row.label +
        " — " +
        row.basis +
        " [" +
        row.evidenceIds.join(", ") +
        "]",
    )
  lines.push(
    "",
    "DOCUMENTATION AND POLICY",
    ...h.flags.map((row) => row.message),
    h.policy.message,
  )
  for (const section of cabReviewSections(view)) {
    lines.push(
      "",
      "ORIGINAL " + section.label.toUpperCase(),
      section.review.evidence_complete
        ? "Element level: " +
            section.review.level +
            ". Does not establish the entire E/M level."
        : "Evidence incomplete; summary unavailable.",
    )
    for (const event of section.review.events) {
      lines.push(event.status + ": " + event.subject + " — " + event.reason)
      if (!event.evidence_complete)
        lines.push("Complete source evidence was not located.")
      for (const span of event.evidence_spans)
        lines.push(
          "Note characters " +
            span.loc.start +
            "–" +
            span.loc.end +
            ": " +
            span.text,
        )
    }
  }
  lines.push("", "SOURCE EVIDENCE")
  for (const row of h.evidence)
    lines.push(
      "[" +
        row.id +
        "] " +
        row.sourceId +
        " version " +
        row.sourceVersion +
        ", characters " +
        row.start +
        "–" +
        row.end,
      row.text,
    )
  return lines.join("\n")
}
