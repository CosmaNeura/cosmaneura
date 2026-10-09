import { Outlet, href } from "react-router"

import { Route } from "./+types/intake"

import { AppSidebar, SidebarEntry } from "@/components/app-sidebar"
import { SidebarProvider } from "@/components/ui/sidebar"
import { Appointment, intakeList } from "~/configuration/client"
import { parseAppointmentDate, relativeDayLabel } from "~/lib/date"
import { intakeStatusBadge } from "~/lib/intake-status"
import { type LabelSet, useTerminology } from "~/utils/terminology"

import { invariant } from "@epic-web/invariant"

export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  const { data } = await intakeList()
  invariant(data, "data failed to load")

  return data
}

export function appointmentsToSidebarEntries(
  appointments: Appointment[],
  t: Pick<LabelSet, "patient">,
  now: Date = new Date(),
): SidebarEntry[] {
  return appointments.map((appointment) => {
    invariant(appointment.id, "appointment.id is required")
    const appointmentDate = parseAppointmentDate(appointment.appointment_date)
    const badge = intakeStatusBadge(appointment.intake_status, t)
    return {
      id: appointment.id,
      url: href("/intake/:id", { id: appointment.id }),
      title: appointment.patient_name,
      location: "",
      date: relativeDayLabel(appointmentDate, now),
      isoDate: appointmentDate.toISOString(),
      status: badge.status,
      statusLabel: badge.label,
      preview: appointment.appointment_name ?? "",
    }
  })
}

export default function Layout({ loaderData }: Route.ComponentProps) {
  const t = useTerminology()
  const { appointments } = loaderData

  return (
    <SidebarProvider
      style={
        {
          "--sidebar-width": "350px",
        } as React.CSSProperties
      }
    >
      <AppSidebar
        isLoading={!appointments}
        header={`${t.patientTitle} Intake`}
        emptyState={{
          title: "No intakes yet",
          description: `When a ${t.patient} is sent an intake link, their intake will appear here.`,
        }}
        searchPlaceholder={`Search ${t.patientPlural}…`}
        entries={appointmentsToSidebarEntries(appointments, t)}
      />
      <Outlet />
    </SidebarProvider>
  )
}
