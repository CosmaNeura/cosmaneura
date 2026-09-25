import { describe, expect, it } from "vitest"

import { appointmentDateFormat } from "./date"

describe("appointment calendar display", () => {
  it.each([
    "2026-09-25T00:45:00",
    "2026-03-08T07:30:00",
    "2026-11-01T05:30:00",
  ])("interprets offset-free API datetime %s as UTC", (value) => {
    expect(appointmentDateFormat(value)).toBe(
      new Date(`${value}Z`).toLocaleString("en-US", { dateStyle: "short" }),
    )
  })
  it("retains explicit offsets and calendar-only compatibility", () => {
    expect(appointmentDateFormat("2026-09-24T20:45:00-04:00")).toBe(
      appointmentDateFormat("2026-09-25T00:45:00Z"),
    )
    expect(appointmentDateFormat("2026-09-24")).toBe("9/24/26")
  })
})
