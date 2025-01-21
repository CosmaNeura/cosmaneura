import { Outlet } from "react-router"

import { Route } from "./+types/notes"

import { AppSidebar, SidebarEntry } from "@/components/app-sidebar"
import { PageTransition } from "@/components/page-transition"
import { SkeletonMessageList } from "@/components/skeleton-message-list"
import { SidebarProvider } from "@/components/ui/sidebar"
import { getClient } from "~/configuration/clerk"
import { Appointment, noteList } from "~/configuration/client"

import { standardDateTimeFormat } from "../lib/date"
import { invariant } from "@epic-web/invariant"

export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  // TODO need idiomatic error handling here
  const { data } = await noteList()
  invariant(data, "data failed to load")

  return data
}

// import type { ShouldRevalidateFunctionArgs } from "react-router"
// TODO not operational! https://github.com/remix-run/react-router/issues/12740
// https://discord.com/channels/770287896669978684/770287896669978687/1328764904894758954
// export function shouldRevalidate(_arg: ShouldRevalidateFunctionArgs) {
//   return false
// }

export default function NotesLayout({ loaderData }: Route.ComponentProps) {
  const { appointments } = loaderData

  function appointmentToSidebarEntry(appointment: Appointment): SidebarEntry {
    return {
      id: appointment.id,
      url: `/notes/${appointment.id}`,
      // title: appointment.patient.name,
      title: appointment.patient_name,
      // location: appointment.location,
      // TODO wire up type of appointment
      // subTitle: appointment.appointment_name,
      date: standardDateTimeFormat(appointment.appointment_date),
      // status: appointment.status,
      status: appointment.appointment_name ? "completed" : "in-progress",
      preview: appointment.appointment_name,
    }
  }

  return (
    <PageTransition>
      <SidebarProvider
        style={
          {
            "--sidebar-width": "350px",
          } as React.CSSProperties
        }
      >
        <AppSidebar
          isLoading={!appointments}
          header={"Notes"}
          entries={appointments.map(appointmentToSidebarEntry)}
        />
        <Outlet />
      </SidebarProvider>
    </PageTransition>
  )
}
