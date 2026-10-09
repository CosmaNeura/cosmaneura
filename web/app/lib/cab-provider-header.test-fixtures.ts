import { createHash } from "node:crypto"

import type { BillingHeaderSource } from "~/configuration/client"

import {
  type CabSource,
  type CabView,
  cabFactKeys,
} from "./cab-provider-header"

export const op = "11111111-1111-4111-8111-111111111111"
const noteText = "Synthetic fertility follow-up. Counseling documented."
const textSha = createHash("sha256").update(noteText).digest("hex")
export const expected: BillingHeaderSource["source"] = {
  noteId: "note_synthetic",
  appointmentId: "appointment_synthetic",
  noteSchemaId: "schema_soap",
  contentSha256: "c".repeat(64),
  textSha256: textSha,
  projectionVersion: "ordered-note-markdown.v1",
  excludedRecordingTime: true,
}
export const pin = {
  encounterId: "source-encounter-v1:synthetic",
  encounterVersion: 1,
  ingestionSource: "provider_completed_note" as const,
  noteId: "22222222-2222-4222-8222-222222222222:note",
  noteVersion: "1",
  noteSha256: textSha,
  bundleSha256: "b".repeat(64),
  supportingSourceDigests: [],
}
const actor = {
  id: "33333333-3333-4333-8333-333333333333",
  name: "Synthetic provider",
  role: "provider" as const,
  identity: {
    kind: "core" as const,
    issuer: "https://core.example.test",
    subject: "provider_synthetic",
    projectionId: "44444444-4444-4444-8444-444444444444",
    mappingVersion: 1,
  },
}
export const source: CabSource = {
  encounterId: pin.encounterId,
  encounterVersion: 1,
  headerSource: pin,
  noteText,
  intake: "",
  transcript: "",
  completion: {
    status: "completed",
    confirmedByProvider: true,
    completedAt: "2026-10-02T17:00:00Z",
  },
  visitContext: {
    patientAge: 34,
    patientSex: "F",
    encounterType: "office_established",
    placeOfService: "11",
    providerSpecialty: "Family medicine",
    serviceDate: "2026-10-02",
  },
  projection: {
    version: "ordered-note-markdown.v1",
    excludedRecordingTime: true,
    contentSha256: expected.contentSha256,
    textSha256: textSha,
  },
  inferencePolicy: { allowed: true, reason: null },
}
export function header(): CabView {
  return {
    header: {
      schemaVersion: "provider-billing-header.v1",
      headerId: "55555555-5555-4555-8555-555555555555",
      expectedSource: pin,
      createdBy: actor,
      generatedAt: "2026-10-02T17:01:00Z",
      reviewStatus: "unreviewed",
      facts: cabFactKeys.map((key) => ({
        key,
        label: key === "visit_type" ? "Visit type" : key,
        value: null,
        status: "not_documented",
        evidenceIds: [],
      })),
      evidence: [
        {
          id: "e1",
          source: "note",
          sourceId: pin.noteId,
          sourceVersion: "1",
          coordinateSystem: "unicode_code_point",
          start: 0,
          end: noteText.length,
          text: noteText,
        },
      ],
      suggestions: [
        {
          id: "s1",
          kind: "ICD10",
          code: "Z31.69",
          label: "Procreative counseling",
          evidenceIds: ["e1"],
          basis: "Synthetic documented counseling.",
          reviewStatus: "unreviewed",
        },
      ],
      flags: [
        {
          id: "f1",
          message: "Synthetic example; clinician and coder review required.",
          evidenceIds: ["e1"],
        },
      ],
      policy: {
        status: "not_verified",
        message: "Payer policy requires separate review.",
      },
      generation: {
        inferenceMode: "mock",
        runId: "66666666-6666-4666-8666-666666666666",
      },
    },
    revisionNumber: 0,
    revision: null,
    signoff: null,
  }
}
export function reply(
  action: string,
  view: CabView | null = null,
  currentSource = source,
) {
  return {
    schemaVersion: "core-cab-result.v1",
    purpose: action === "accept" ? "completed-note.accept" : "header." + action,
    sourceOperationId: op,
    requestSha256: "a".repeat(64),
    source: currentSource,
    result:
      action === "accept"
        ? {
            current: true,
            replayed: false,
            receipt: {
              operationId: op,
              encounterId: pin.encounterId,
              encounterVersion: 1,
              headerSource: pin,
            },
          }
        : { view, ...(action === "read" ? {} : { created: true }) },
  }
}
