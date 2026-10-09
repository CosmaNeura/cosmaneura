import { Outlet, href } from "react-router"

import { Route } from "./+types/intake"

import { AppSidebar, SidebarEntry } from "@/components/app-sidebar"
import { StatusBadgeOptions } from "@/components/shared/StatusBadge"
import { SidebarProvider } from "@/components/ui/sidebar"
import { Appointment, IntakeStatus, intakeList } from "~/configuration/client"
import { standardDateTimeFormat } from "~/lib/date"
import { useTerminology } from "~/utils/terminology"

import { invariant } from "@epic-web/invariant"

export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  const { data } = await intakeList()
  invariant(data, "data failed to load")

  return data
}

export function appointmentsToSidebarEntries(
  appointments: Appointment[],
): SidebarEntry[] {
  const STATUS_MAP: Record<IntakeStatus, StatusBadgeOptions> = {
    pending: "not-started",
    processing: "in-progress",
    complete: "completed",
    // this should never occur, but makes TS happy
    none: "not-started",
  }

  return appointments.map((appointment) => {
    invariant(appointment.id, "appointment.id is required")
    return {
      id: appointment.id,
      url: href("/intake/:id", { id: appointment.id }),
      title: appointment.patient_name,
      // location: appointment.location,
      // TODO wire up type of appointment
      // subTitle: appointment.appointment_name,
      date: standardDateTimeFormat(appointment.appointment_date),
      // status: appointment.status,
      status: STATUS_MAP[appointment.intake_status],
      preview: appointment.appointment_name,
    } as SidebarEntry
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
        entries={appointmentsToSidebarEntries(appointments)}
      />
      <Outlet />
    </SidebarProvider>
  )
}
