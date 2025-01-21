import { Outlet } from "react-router"

import { Route } from "./+types/intake"

import { AppSidebar, SidebarEntry } from "@/components/app-sidebar"
import { PageTransition } from "@/components/page-transition"
import { SkeletonMessageList } from "@/components/skeleton-message-list"
import { SidebarProvider } from "@/components/ui/sidebar"
import { getClient } from "~/configuration/clerk"
import { intakeList } from "~/configuration/client"

import { standardDateTimeFormat } from "../lib/date"
import { invariant } from "@epic-web/invariant"

export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  // TODO need idiomatic error handling here
  const { data } = await intakeList()
  invariant(data, "data failed to load")

  return data
}

export default function Layout({ loaderData }: Route.ComponentProps) {
  const { appointments } = loaderData

  function appointmentToSidebarEntry(appointment: Appointment): SidebarEntry {
    return {
      id: appointment.id,
      url: `/notes/${appointment.id}`,
      // title: appointment.patient.name,
      title: appointment.patient_name,
      // location: appointment.location,
      // subTitle: "type of appointment",
      date: standardDateTimeFormat(appointment.appointment_date),
      // status: appointment.status,
      status: "none",
      // preview: appointment,
      // preview: "something",
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
