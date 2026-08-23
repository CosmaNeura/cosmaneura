import { describe, expect, it } from "vitest"

import {
  DEFAULT_TERMINOLOGY,
  TERMINOLOGY_SETS,
  getTerminology,
} from "./terminology"

describe("getTerminology", () => {
  it("falls back to doctor/patient when unset", () => {
    expect(getTerminology(null).provider).toBe("doctor")
    expect(getTerminology(undefined).patient).toBe("patient")
    expect(DEFAULT_TERMINOLOGY).toBe("doctor_patient")
  })

  it("returns the therapist/client label set", () => {
    const labels = getTerminology("therapist_client")
    expect(labels.provider).toBe("therapist")
    expect(labels.providerTitle).toBe("Therapist")
    expect(labels.patient).toBe("client")
    expect(labels.patientTitle).toBe("Client")
    expect(labels.patientPlural).toBe("clients")
    expect(labels.patientPossessive).toBe("client's")
  })

  it("returns the counselor/client label set", () => {
    const labels = getTerminology("counselor_client")
    expect(labels.provider).toBe("counselor")
    expect(labels.patient).toBe("client")
  })

  it("returns the nutritionist/client label set", () => {
    const labels = getTerminology("nutritionist_client")
    expect(labels.provider).toBe("nutritionist")
    expect(labels.providerTitle).toBe("Nutritionist")
    expect(labels.patient).toBe("client")
    expect(labels.displayName).toBe("Nutritionist / Client")
  })

  it("every label set defines every variant", () => {
    for (const labels of Object.values(TERMINOLOGY_SETS)) {
      expect(labels.provider).toBeTruthy()
      expect(labels.providerTitle).toBeTruthy()
      expect(labels.patient).toBeTruthy()
      expect(labels.patientTitle).toBeTruthy()
      expect(labels.patientPlural).toBeTruthy()
      expect(labels.patientPossessive).toBeTruthy()
      expect(labels.displayName).toBeTruthy()
    }
  })
})
