import { Outlet, href } from "react-router"

import { Route } from "./+types/schedule"

import { AppSidebar } from "@/components/app-sidebar"
import { SidebarProvider } from "@/components/ui/sidebar"
import { scheduleList } from "~/configuration/client"
import type { ZoomMeetingResponse } from "~/configuration/client"

import { invariant } from "@epic-web/invariant"

export async function clientLoader(_args: Route.ClientLoaderArgs) {
  const { data } = await scheduleList()
  invariant(data, "schedule data failed to load")
  return data
}

function meetingsToSidebarEntries(meetings: ZoomMeetingResponse[]) {
  return meetings.map((meeting) => ({
    id: meeting.zoom_meeting_id,
    url: href("/schedule/:zoom_meeting_id", {
      zoom_meeting_id: meeting.zoom_meeting_id,
    }),
    title: meeting.topic,
    location: "",
    date: new Date(meeting.start_time).toLocaleDateString(),
    status:
      meeting.status === "ended"
        ? ("completed" as const)
        : ("in-progress" as const),
    preview: `${meeting.duration} min`,
  }))
}

export default function ScheduleLayout({ loaderData }: Route.ComponentProps) {
  const { meetings, zoom_connected } = loaderData

  return (
    <SidebarProvider
      style={
        {
          "--sidebar-width": "300px",
        } as React.CSSProperties
      }
    >
      <AppSidebar
        isLoading={!meetings}
        header="Schedule"
        entries={zoom_connected ? meetingsToSidebarEntries(meetings) : []}
      />
      <Outlet />
    </SidebarProvider>
  )
}
