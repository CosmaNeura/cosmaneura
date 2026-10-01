import { describe, expect, it } from "vitest"

import { visitPrepScheduleFromLocal } from "./visit-prep-schedule"

const beforeTestDates = new Date("2026-01-01T00:00:00Z")

describe("visitPrepScheduleFromLocal", () => {
  it("keeps the local wall clock and adds the IANA-zone UTC offset", () => {
    expect(
      visitPrepScheduleFromLocal("2026-08-25T14:30", {
        now: beforeTestDates,
        timezoneName: "America/New_York",
      }),
    ).toEqual({
      ok: true,
      scheduledAt: "2026-08-25T14:30:00-04:00",
      timezoneName: "America/New_York",
    })
  })

  it("handles standard time and rejects a repeated fall-back wall time", () => {
    expect(
      visitPrepScheduleFromLocal("2026-12-15T09:00", {
        now: beforeTestDates,
        timezoneName: "America/New_York",
      }),
    ).toMatchObject({ scheduledAt: "2026-12-15T09:00:00-05:00" })
    expect(
      visitPrepScheduleFromLocal("2026-11-01T01:30", {
        now: beforeTestDates,
        timezoneName: "America/New_York",
      }),
    ).toEqual({ ok: false, reason: "ambiguous" })
  })

  it("rejects malformed dates, DST gaps, past times, and unknown zones", () => {
    expect(
      visitPrepScheduleFromLocal("2026-02-30T10:00", {
        now: beforeTestDates,
        timezoneName: "UTC",
      }),
    ).toEqual({ ok: false, reason: "invalid" })
    expect(
      visitPrepScheduleFromLocal("2026-03-08T02:30", {
        now: beforeTestDates,
        timezoneName: "America/New_York",
      }),
    ).toEqual({ ok: false, reason: "invalid" })
    expect(
      visitPrepScheduleFromLocal("2025-12-31T23:59", {
        now: beforeTestDates,
        timezoneName: "UTC",
      }),
    ).toEqual({ ok: false, reason: "not_future" })
    expect(
      visitPrepScheduleFromLocal("2026-08-25T14:30", {
        now: beforeTestDates,
        timezoneName: "Not/A_Real_Zone",
      }),
    ).toEqual({ ok: false, reason: "timezone" })
    expect(
      visitPrepScheduleFromLocal("9999-12-31T23:59", {
        now: beforeTestDates,
        timezoneName: "UTC",
      }),
    ).toEqual({ ok: false, reason: "invalid" })
  })
})
