import { describe, expect, it } from "vitest"

import type { Appointment } from "~/configuration/client"

import { appointmentsToSidebarEntries } from "./appointment"
import { relativeDayLabel } from "./date"

const appointments = [
  {
    id: "named_capture",
    patient_name: "Fictional unfinished capture",
    appointment_name: "Scribe qualification",
    appointment_date: "2026-09-24T12:00:00Z",
  },
  {
    id: "unnamed_note",
    patient_name: "Fictional prepared visit",
    appointment_name: null,
    appointment_date: "2026-09-23T12:00:00Z",
  },
] as Appointment[]

const linkTarget = (id: string) => `/notes/${id}`

describe("note status in the appointment sidebar", () => {
  it("shows current note state independently of the visit title", () => {
    const entries = appointmentsToSidebarEntries(appointments, linkTarget, {
      named_capture: "not-started",
      unnamed_note: "completed",
    })

    expect(entries.map(({ status }) => status)).toEqual([
      "not-started",
      "completed",
    ])
    expect(entries[0].url).toBe("/notes/named_capture")
    expect(entries[0].preview).toBe("Scribe qualification")
  })

  it("hides an unknown status with an older backend instead of guessing", () => {
    const entries = appointmentsToSidebarEntries(appointments, linkTarget)

    expect(entries.map(({ status }) => status)).toEqual(["none", "none"])
  })

  it("keeps statuses tied to their own appointment when the list changes", () => {
    const entries = appointmentsToSidebarEntries(
      [...appointments].reverse(),
      linkTarget,
      { named_capture: "not-started", unrelated_visit: "completed" },
    )

    expect(entries.map(({ id, status }) => ({ id, status }))).toEqual([
      { id: "unnamed_note", status: "none" },
      { id: "named_capture", status: "not-started" },
    ])
  })
})

describe("note sidebar labels", () => {
  it("shows relative dates and a status label for each visit", () => {
    const now = new Date("2026-09-24T18:00:00Z")
    const entries = appointmentsToSidebarEntries(
      appointments,
      linkTarget,
      { named_capture: "not-started", unnamed_note: "completed" },
      now,
    )

    expect(entries.map(({ statusLabel }) => statusLabel)).toEqual([
      "No note yet",
      "Note ready",
    ])
    expect(entries[0].isoDate).toBe("2026-09-24T12:00:00.000Z")
    expect(entries[1].preview).toBe("")
    expect(entries[0].date).toBe(
      relativeDayLabel(new Date("2026-09-24T12:00:00Z"), now),
    )
  })
})

describe("defensive note status lookup", () => {
  it("treats a present but empty status as unknown", () => {
    const [entry] = appointmentsToSidebarEntries(
      appointments.slice(0, 1),
      linkTarget,
      { named_capture: undefined } as unknown as Record<
        string,
        "not-started" | "completed"
      >,
    )
    expect(entry.status).toBe("none")
    expect(entry.statusLabel).toBeUndefined()
  })
})
