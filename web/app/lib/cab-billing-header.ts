import type {
  BillingHeaderOperation,
  BillingHeaderSource,
  PrepareBillingHeaderOperation,
} from "~/configuration/client"

const hash = (value: unknown) =>
  typeof value === "string" && /^[a-f0-9]{64}$/.test(value)
const uuid = (value: unknown) =>
  typeof value === "string" &&
  /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value)

export function usableBillingSource(
  value: BillingHeaderSource | null | undefined,
  note: { id: string; appointment_id: string; doctor_note_schema_id: string },
): value is BillingHeaderSource {
  return Boolean(
    value?.schemaVersion === "core.billing-header-source.v1" &&
      value.state === "review_required" &&
      value.source != null &&
      value.source.noteId === note.id &&
      value.source.appointmentId === note.appointment_id &&
      value.source.noteSchemaId === note.doctor_note_schema_id &&
      hash(value.source.contentSha256) &&
      hash(value.source.textSha256) &&
      value.source.projectionVersion === "ordered-note-markdown.v1" &&
      typeof value.source.excludedRecordingTime === "boolean" &&
      (!value.latestOperation ||
        (uuid(value.latestOperation.operationId) &&
          value.latestOperation.state === "prepared" &&
          Number.isSafeInteger(value.latestOperation.sourceRevision) &&
          value.latestOperation.sourceRevision > 0 &&
          Number.isFinite(Date.parse(value.latestOperation.createdAt)))),
  )
}

export type BillingVisitDraft = {
  patientAge: string
  patientSex: string
  encounterType: string
  placeOfService: string
  providerSpecialty: string
  serviceDate: string
}

export function emptyBillingVisit(): BillingVisitDraft {
  return {
    patientAge: "",
    patientSex: "",
    encounterType: "",
    placeOfService: "",
    providerSpecialty: "",
    serviceDate: "",
  }
}

export function prepareBillingVisit(draft: BillingVisitDraft) {
  if (
    !/^(0|[1-9]\d{0,2})$/.test(draft.patientAge) ||
    Number(draft.patientAge) > 130
  )
    throw new Error("Enter the patient's age at this visit, from 0 to 130.")
  if (!["M", "F", "other", "unknown"].includes(draft.patientSex))
    throw new Error("Select the documented patient sex, or Unknown.")
  if (
    ![
      "office_new",
      "office_established",
      "telehealth_new",
      "telehealth_established",
    ].includes(draft.encounterType)
  )
    throw new Error("Select the visit setting and patient type.")
  if (
    !["11", "02", "10"].includes(draft.placeOfService) ||
    draft.encounterType.startsWith("office_") !==
      (draft.placeOfService === "11")
  )
    throw new Error("Choose a place of service that matches the visit setting.")
  const specialty = draft.providerSpecialty.trim()
  if (
    !specialty ||
    specialty.length > 100 ||
    Array.from(specialty).some(
      (character) =>
        character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
    )
  )
    throw new Error("Enter the provider specialty for this visit.")
  if (
    draft.serviceDate &&
    (!/^\d{4}-\d{2}-\d{2}$/.test(draft.serviceDate) ||
      !Number.isFinite(Date.parse(draft.serviceDate + "T00:00:00Z")) ||
      new Date(draft.serviceDate + "T00:00:00Z").toISOString().slice(0, 10) !==
        draft.serviceDate)
  )
    throw new Error("Enter a valid service date or leave it blank.")
  return {
    patientAge: Number(draft.patientAge),
    patientSex: draft.patientSex,
    encounterType: draft.encounterType,
    placeOfService: draft.placeOfService,
    providerSpecialty: specialty,
    ...(draft.serviceDate ? { serviceDate: draft.serviceDate } : {}),
  } as PrepareBillingHeaderOperation["visit"]
}

/** A successful HTTP status alone does not acknowledge this exact command. */
export function readPreparedBillingOperation(
  value: BillingHeaderOperation | undefined,
  command: PrepareBillingHeaderOperation,
  source: BillingHeaderSource["source"],
): BillingHeaderOperation {
  if (
    !value ||
    value.schemaVersion !== "core.billing-header-operation.v1" ||
    value.state !== "prepared" ||
    !uuid(value.operationId) ||
    value.operationId !== command.operationId ||
    !Number.isSafeInteger(value.sourceRevision) ||
    value.sourceRevision < 1 ||
    typeof value.replayed !== "boolean" ||
    !Number.isFinite(Date.parse(value.completedAt)) ||
    value.transport?.state !== "not_configured" ||
    !Object.entries(source).every(
      ([key, expected]) =>
        value.source[key as keyof typeof source] === expected,
    )
  )
    throw new Error("Unconfirmed billing operation")
  return value
}
