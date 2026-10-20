import type { SidebarEntry } from "@/components/app-sidebar"
import type { StatusBadgeOptions } from "@/components/shared/StatusBadge"
import type { Appointment, NotetakingData } from "~/configuration/client"

import { parseAppointmentDate, relativeDayLabel } from "../lib/date"
import { invariant } from "@epic-web/invariant"

export type NoteStatuses = NonNullable<NotetakingData["note_statuses"]>

export const NOTE_STATUS_LABELS: Record<StatusBadgeOptions, string> = {
  completed: "Note ready",
  "in-progress": "Writing note",
  "not-started": "No note yet",
}

export function appointmentsToSidebarEntries(
  appointments: Appointment[],
  linkTarget: (id: string) => string,
  noteStatuses: NoteStatuses = {},
  now: Date = new Date(),
): SidebarEntry[] {
  return appointments.map((appointment) => {
    invariant(appointment.id, "appointment.id is required")
    const appointmentDate = parseAppointmentDate(appointment.appointment_date)
    // An older backend may omit status; a visit title says nothing about notes.
    const knownStatuses: Partial<NoteStatuses> = noteStatuses
    const status = knownStatuses[appointment.id] ?? "none"
    return {
      id: appointment.id,
      url: linkTarget(appointment.id),
      title: appointment.patient_name,
      location: "",
      date: relativeDayLabel(appointmentDate, now),
      isoDate: appointmentDate.toISOString(),
      status,
      statusLabel: status === "none" ? undefined : NOTE_STATUS_LABELS[status],
      preview: appointment.appointment_name ?? "",
    }
  })
}
