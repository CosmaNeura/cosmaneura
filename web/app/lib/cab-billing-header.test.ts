import { describe, expect, it } from "vitest"

import type { BillingHeaderSource } from "~/configuration/client"

import {
  emptyBillingVisit,
  prepareBillingVisit,
  usableBillingSource,
} from "./cab-billing-header"

const note = {
  id: "note_synthetic",
  appointment_id: "appointment_synthetic",
  doctor_note_schema_id: "schema_soap",
}
const source = {
  schemaVersion: "core.billing-header-source.v1",
  state: "review_required",
  source: {
    noteId: note.id,
    appointmentId: note.appointment_id,
    noteSchemaId: note.doctor_note_schema_id,
    contentSha256: "a".repeat(64),
    textSha256: "b".repeat(64),
    projectionVersion: "ordered-note-markdown.v1",
    excludedRecordingTime: false,
  },
  visit: {},
  latestOperation: null,
} as BillingHeaderSource

describe("billing source and visit contract", () => {
  it("hides the action without a current server-admitted source", () => {
    expect(usableBillingSource(undefined, note)).toBe(false)
    expect(
      usableBillingSource({ ...source, state: "not_configured" }, note),
    ).toBe(false)
    expect(usableBillingSource(source, { ...note, id: "note_other" })).toBe(
      false,
    )
    expect(
      usableBillingSource(source, {
        ...note,
        appointment_id: "appointment_other",
      }),
    ).toBe(false)
    expect(usableBillingSource(source, note)).toBe(true)
  })
  it("has no implicit clinical defaults", () => {
    expect(
      Object.values(emptyBillingVisit()).every((value) => value === ""),
    ).toBe(true)
    expect(() => prepareBillingVisit(emptyBillingVisit())).toThrow("age")
  })
  it("rejects contradictory settings and invalid ages/dates", () => {
    const visit = {
      ...emptyBillingVisit(),
      patientAge: "34",
      patientSex: "F",
      encounterType: "office_established",
      placeOfService: "11",
      providerSpecialty: "Family medicine",
    }
    expect(() =>
      prepareBillingVisit({ ...visit, placeOfService: "10" }),
    ).toThrow("matches")
    expect(() => prepareBillingVisit({ ...visit, patientAge: "131" })).toThrow(
      "age",
    )
    expect(() =>
      prepareBillingVisit({ ...visit, serviceDate: "2026-02-30" }),
    ).toThrow("date")
    expect(
      prepareBillingVisit({ ...visit, serviceDate: "2026-10-02" }),
    ).toHaveProperty("serviceDate", "2026-10-02")
    expect(prepareBillingVisit(visit)).not.toHaveProperty("serviceDate")
  })
})
