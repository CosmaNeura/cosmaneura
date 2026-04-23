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

function meetingSidebarStatus(
  meeting: ZoomMeetingResponse,
): "completed" | "in-progress" | "not-started" {
  // ZoomMeeting.status from the backend is the canonical signal once
  // meeting.ended fires. Before that, fall back to the scheduled time
  // window so a meeting that ran and ended silently (missed webhook,
  // early exit) doesn't keep showing as "in-progress" past its end.
  if (meeting.status === "ended") return "completed"

  const now = Date.now()
  const startMs = new Date(meeting.start_time).getTime()
  const endMs = startMs + meeting.duration * 60_000

  if (now >= endMs) return "completed"
  if (now >= startMs) return "in-progress"
  return "not-started"
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
    status: meetingSidebarStatus(meeting),
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
