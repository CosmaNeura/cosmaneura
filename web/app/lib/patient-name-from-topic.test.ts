import { describe, expect, it } from "vitest"

import { guessPatientNameFromTopic } from "./patient-name-from-topic"

describe("guessPatientNameFromTopic", () => {
  it.each([
    ["Appointment with Jane Doe", "Jane Doe"],
    ["Follow-up for Jane Doe", "Jane Doe"],
    ["Call with Jane Doe", "Jane Doe"],
    ["Dr. Smith and Jane Doe", "Jane Doe"],
    ["Jane Doe & Dr. Smith", "Jane Doe"],
    ["Jane Doe - Follow up", "Jane Doe"],
    ["Telehealth: Jane Doe", "Jane Doe"],
    ["Appointment with Jane Doe - Telehealth", "Jane Doe"],
    ["Jane Doe", "Jane Doe"],
    ["Jane Doe-Smith", "Jane Doe-Smith"],
    ["J. Doe", "J. Doe"],
    ["José García - consult", "José García"],
  ])("extracts the name from %j", (topic, expected) => {
    expect(guessPatientNameFromTopic(topic)).toBe(expected)
  })

  it.each([
    // no person-shaped name present
    ["Cardiology Follow Up"],
    ["Team standup"],
    ["New Patient Visit"],
    ["Weekly sync"],
    // only a provider is named
    ["Meeting with Dr. Smith"],
    ["Dr. Smith"],
    // ambiguous: more than one plausible patient name
    ["Jane Doe and John Roe"],
    ["Jane Doe: John Roe"],
    // lowercase names are not confidently person-shaped
    ["appointment with jane doe"],
    // degenerate input
    [""],
    ["   "],
  ])("returns null for %j", (topic) => {
    expect(guessPatientNameFromTopic(topic)).toBeNull()
  })

  it("handles null and undefined", () => {
    expect(guessPatientNameFromTopic(null)).toBeNull()
    expect(guessPatientNameFromTopic(undefined)).toBeNull()
  })
})
