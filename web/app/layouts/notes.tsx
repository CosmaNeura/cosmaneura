import * as React from "react"
import { Outlet, useOutletContext } from "react-router"
import type { ShouldRevalidateFunctionArgs } from "react-router"

import { Route } from "./+types/notes"

import { AppSidebar, SidebarEntry } from "@/components/app-sidebar"
import { PageTransition } from "@/components/page-transition"
import { SkeletonMessageList } from "@/components/skeleton-message-list"
import { SidebarProvider } from "@/components/ui/sidebar"
import { getClient } from "~/configuration/clerk"
import { Appointment, noteList } from "~/configuration/client"

import { invariant } from "@epic-web/invariant"

type ContextType = { onNavigate: (page: string) => void; currentPage: string }

export async function clientLoader(_loader_args: Route.ClientLoaderArgs) {
  const clerkClient = await getClient()
  invariant(clerkClient, "clerk client should always exist or redirect")

  const { data } = await noteList()

  // TODO need idiomatic error handling here
  if (data === undefined) {
    throw new Error("Failed to load data")
  }

  return data
}

// TODO not operational! https://github.com/remix-run/react-router/issues/12740
// https://discord.com/channels/770287896669978684/770287896669978687/1328764904894758954
// export function shouldRevalidate(_arg: ShouldRevalidateFunctionArgs) {
//   return false
// }

export default function NotesLayout({ loaderData }: Route.ComponentProps) {
  const outletContext = useOutletContext<ContextType>()
  const { onNavigate, currentPage } = outletContext
  const { appointments } = loaderData

  function appointmentToSidebarEntry(appointment: Appointment): SidebarEntry {
    return {
      id: appointment.id,
      url: `/notes/${appointment.id}`,
      // title: appointment.patient.name,
      title: "patient name",
      // location: appointment.location,
      location: "Denver, CO",
      subTitle: "type of appointment",
      date: appointment.appointment_date,
      // status: appointment.status,
      status: "none",
      // preview: appointment,
      preview: "something",
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
        <Outlet context={outletContext} />
      </SidebarProvider>
    </PageTransition>
  )
}
