import { describe, expect, it, vi } from "vitest"

import type { ZoomMeetingResponse } from "~/configuration/client"

import { meetingsToSidebarEntries } from "./schedule"

vi.mock("~/configuration/client", () => ({ scheduleList: vi.fn() }))

const NOW = new Date(2026, 9, 8, 15, 0)

function meeting(
  id: string,
  start: Date,
  overrides: Partial<ZoomMeetingResponse> = {},
): ZoomMeetingResponse {
  return {
    id: `zmtg_${id}`,
    zoom_meeting_id: id,
    topic: `Meeting ${id}`,
    start_time: start.toISOString(),
    duration: 30,
    join_url: null,
    status: "scheduled",
    appointment_id: null,
    capture_authorized: false,
    ...overrides,
  } as ZoomMeetingResponse
}

describe("schedule sidebar entries", () => {
  it("lists today and upcoming soonest first, then past most recent first", () => {
    const entries = meetingsToSidebarEntries(
      [
        meeting("past-old", new Date(2026, 9, 1, 9, 0), { status: "ended" }),
        meeting("next-week", new Date(2026, 9, 12, 13, 0)),
        meeting("later-today", new Date(2026, 9, 8, 16, 30)),
        meeting("past-recent", new Date(2026, 9, 7, 10, 0)),
        meeting("now", new Date(2026, 9, 8, 14, 50)),
        meeting("tomorrow", new Date(2026, 9, 9, 9, 0)),
      ],
      NOW,
    )

    expect(entries.map((entry) => [entry.id, entry.group])).toEqual([
      ["now", "Today"],
      ["later-today", "Today"],
      ["tomorrow", "Upcoming"],
      ["next-week", "Upcoming"],
      ["past-recent", "Past"],
      ["past-old", "Past"],
    ])
  })

  it("shows the day and time with a status a pending meeting can wear", () => {
    const [today, tomorrow, later] = meetingsToSidebarEntries(
      [
        meeting("a", new Date(2026, 9, 8, 16, 30)),
        meeting("b", new Date(2026, 9, 9, 9, 0)),
        meeting("c", new Date(2026, 9, 12, 13, 0)),
      ],
      NOW,
    )

    expect(today.date).toBe("Today · 4:30 PM")
    expect(today.statusLabel).toBe("Upcoming")
    expect(tomorrow.date).toBe("Tomorrow · 9:00 AM")
    expect(later.date).toBe("Mon, Oct 12 · 1:00 PM")
    expect(later.preview).toBe("30 min")
  })

  it("treats a meeting past its scheduled end as ended", () => {
    const [entry] = meetingsToSidebarEntries(
      [meeting("x", new Date(2026, 9, 8, 13, 0))],
      NOW,
    )
    expect(entry.status).toBe("completed")
    expect(entry.statusLabel).toBe("Ended")
    expect(entry.group).toBe("Past")
  })
})
