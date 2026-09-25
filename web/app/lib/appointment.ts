import { SidebarEntry } from "@/components/app-sidebar"
import { Appointment } from "~/configuration/client"

import { appointmentDateFormat } from "../lib/date"
import { invariant } from "@epic-web/invariant"

export function appointmentsToSidebarEntries(
  appointments: Appointment[],
  linkTarget: (id: string) => string,
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
      // status: appointment.status,
      status: appointment.appointment_name ? "completed" : "in-progress",
      preview: appointment.appointment_name,
    } as SidebarEntry
  })
}
