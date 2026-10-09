import { describe, expect, it } from "vitest"

import {
  appointmentDateFormat,
  calendarDayOffset,
  isInCurrentWeek,
  meetingTimeLabel,
  relativeDayLabel,
} from "./date"

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

// Local-time constructors keep these independent of the test machine's zone.
const NOW = new Date(2026, 9, 8, 15, 0) // Thu, Oct 8 2026, 3:00 PM

describe("relative day labels", () => {
  it.each([
    [new Date(2026, 9, 8, 0, 5), "Today"],
    [new Date(2026, 9, 8, 23, 55), "Today"],
    [new Date(2026, 9, 7, 23, 55), "Yesterday"],
    [new Date(2026, 9, 9, 0, 5), "Tomorrow"],
    [new Date(2026, 9, 5, 12, 0), "Monday"],
    [new Date(2026, 9, 2, 12, 0), "Friday"],
    [new Date(2026, 9, 1, 12, 0), "Oct 1"],
    [new Date(2026, 9, 20, 12, 0), "Oct 20"],
    [new Date(2025, 11, 30, 12, 0), "Dec 30, 2025"],
  ])("labels %s as %s", (date, label) => {
    expect(relativeDayLabel(date, NOW)).toBe(label)
  })

  it("counts calendar days rather than 24-hour periods", () => {
    expect(calendarDayOffset(new Date(2026, 9, 7, 23, 59), NOW)).toBe(-1)
    expect(calendarDayOffset(new Date(2026, 9, 9, 0, 1), NOW)).toBe(1)
  })

  it("counts calendar days across a daylight-saving change", () => {
    const afterChange = new Date(2026, 10, 2, 9, 0)
    expect(calendarDayOffset(new Date(2026, 9, 31, 9, 0), afterChange)).toBe(-2)
  })
})

describe("meeting time labels", () => {
  it.each([
    [new Date(2026, 9, 8, 14, 30), "Today · 2:30 PM"],
    [new Date(2026, 9, 9, 9, 0), "Tomorrow · 9:00 AM"],
    [new Date(2026, 9, 12, 13, 0), "Mon, Oct 12 · 1:00 PM"],
    [new Date(2027, 0, 4, 13, 0), "Mon, Jan 4, 2027 · 1:00 PM"],
  ])("labels %s as %s", (date, label) => {
    expect(meetingTimeLabel(date, NOW)).toBe(label)
  })
})

describe("current week", () => {
  it.each([
    [new Date(2026, 9, 4, 0, 1), true], // Sunday
    [new Date(2026, 9, 10, 23, 59), true], // Saturday, late
    [new Date(2026, 9, 3, 23, 59), false],
    [new Date(2026, 9, 11, 0, 1), false],
  ])("treats %s as in this week: %s", (date, expected) => {
    expect(isInCurrentWeek(date, NOW)).toBe(expected)
  })
})
