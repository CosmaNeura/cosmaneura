import type { SidebarEntry } from "@/components/app-sidebar"
import type { Appointment, NotetakingData } from "~/configuration/client"

import { appointmentDateFormat } from "../lib/date"
import { invariant } from "@epic-web/invariant"

export type NoteStatuses = NonNullable<NotetakingData["note_statuses"]>

export function appointmentsToSidebarEntries(
  appointments: Appointment[],
  linkTarget: (id: string) => string,
  noteStatuses: NoteStatuses = {},
): SidebarEntry[] {
  return appointments.map((appointment) => {
    invariant(appointment.id, "appointment.id is required")
    return {
      id: appointment.id,
      url: linkTarget(appointment.id),
      title: appointment.patient_name,
      // location: appointment.location,
      // TODO wire up type of appointment
      // subTitle: appointment.appointment_name,
      date: appointmentDateFormat(appointment.appointment_date),
      // An older backend may omit status; a visit title says nothing about notes.
      status: noteStatuses[appointment.id] ?? "none",
      preview: appointment.appointment_name,
    } as SidebarEntry
  })
}
