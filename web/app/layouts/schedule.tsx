import { Outlet, href } from "react-router"

import { Route } from "./+types/schedule"

import { AppSidebar, type SidebarEntry } from "@/components/app-sidebar"
import type { StatusBadgeOptions } from "@/components/shared/StatusBadge"
import { SidebarProvider } from "@/components/ui/sidebar"
import { ScheduleLoadBoundary } from "~/components/ScheduleLoadBoundary"
import type { ZoomMeetingResponse } from "~/configuration/client"
import { calendarDayOffset, meetingTimeLabel } from "~/lib/date"
import { loadSchedule } from "~/lib/schedule-load"

export async function clientLoader({ request }: Route.ClientLoaderArgs) {
  return loadSchedule(request)
}

export function ErrorBoundary({ error }: { error: unknown }) {
  return <ScheduleLoadBoundary error={error} list />
}

const MEETING_STATUS_LABELS: Record<StatusBadgeOptions, string> = {
  completed: "Ended",
  "in-progress": "In progress",
  "not-started": "Upcoming",
}

function meetingSidebarStatus(
  meeting: ZoomMeetingResponse,
  now: number,
): StatusBadgeOptions {
  // ZoomMeeting.status from the backend is the canonical signal once
  // meeting.ended fires. Before that, fall back to the scheduled time
  // window so a meeting that ran and ended silently (missed webhook,
  // early exit) doesn't keep showing as "in-progress" past its end.
  if (meeting.status === "ended") return "completed"

  const startMs = new Date(meeting.start_time).getTime()
  const endMs = startMs + meeting.duration * 60_000

  if (now >= endMs) return "completed"
  if (now >= startMs) return "in-progress"
  return "not-started"
}

const GROUP_ORDER = ["Today", "Upcoming", "Past"] as const

// Upcoming meetings first, soonest at the top; past meetings after them,
// most recent first.
export function meetingsToSidebarEntries(
  meetings: ZoomMeetingResponse[],
  now: Date = new Date(),
): SidebarEntry[] {
  return meetings
    .map((meeting) => {
      const start = new Date(meeting.start_time)
      const status = meetingSidebarStatus(meeting, now.getTime())
      const group: (typeof GROUP_ORDER)[number] =
        status === "completed"
          ? "Past"
          : calendarDayOffset(start, now) <= 0
            ? "Today"
            : "Upcoming"
      return { meeting, start, status, group }
    })
    .sort((a, b) => {
      const byGroup =
        GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group)
      if (byGroup !== 0) return byGroup
      const byTime = a.start.getTime() - b.start.getTime()
      return a.group === "Past" ? -byTime : byTime
    })
    .map(({ meeting, start, status, group }) => ({
      id: meeting.zoom_meeting_id,
      url: href("/schedule/:zoom_meeting_id", {
        zoom_meeting_id: meeting.zoom_meeting_id,
      }),
      title: meeting.topic,
      location: "",
      date: meetingTimeLabel(start, now),
      isoDate: start.toISOString(),
      status,
      statusLabel: MEETING_STATUS_LABELS[status],
      preview: `${meeting.duration} min`,
      group,
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
        searchPlaceholder="Search meetings…"
        emptyState={
          zoom_connected
            ? {
                title: "No upcoming meetings",
                description:
                  "Meetings scheduled in your connected Zoom account will appear here.",
              }
            : {
                title: "Connect Zoom to see your meetings",
                description:
                  "Once Zoom is connected, your scheduled meetings will appear here.",
              }
        }
        entries={zoom_connected ? meetingsToSidebarEntries(meetings) : []}
      />
      <Outlet />
    </SidebarProvider>
  )
}
