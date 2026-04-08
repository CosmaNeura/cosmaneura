import { href, useNavigate } from "react-router"
import { useState } from "react"
import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { Button } from "@/components/ui/button"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { cancelMeeting } from "~/configuration/client"
import { toast } from "~/hooks/use-toast"
import { Route } from "./+types/detail"
import { useRevalidator } from "react-router"
import type { ZoomMeetingResponse } from "~/configuration/client"

function formatDateTime(isoString: string): string {
  return new Date(isoString).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function MeetingDetail({ meeting }: { meeting: ZoomMeetingResponse }) {
  const [cancelling, setCancelling] = useState(false)
  const { revalidate } = useRevalidator()
  const navigate = useNavigate()

  const isScheduled = meeting.status === "scheduled"
  const isActive = meeting.status === "scheduled" || meeting.status === "started"
  const isEnded = meeting.status === "ended"

  async function handleCancel() {
    setCancelling(true)
    const { error } = await cancelMeeting({
      path: { zoom_meeting_id: meeting.zoom_meeting_id },
    })

    if (error) {
      toast({ title: "Failed to cancel meeting", variant: "destructive" })
      setCancelling(false)
      return
    }

    toast({ title: "Meeting cancelled" })
    revalidate()
    navigate(href("/schedule"))
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="mb-1 text-2xl font-semibold">{meeting.topic}</h1>
        <p className="text-muted-foreground text-sm">
          {formatDateTime(meeting.start_time)} · {meeting.duration} min
        </p>
      </div>

      <div className="flex gap-3">
        {isActive && (
          <Button
            asChild
            className="bg-[#0E72ED] text-white hover:bg-[#0E72ED]/90"
          >
            <a href={meeting.join_url} target="_blank" rel="noopener noreferrer">
              Join Meeting
            </a>
          </Button>
        )}

        {isScheduled && (
          <Button variant="outline" onClick={handleCancel} disabled={cancelling}>
            {cancelling ? "Cancelling..." : "Cancel Meeting"}
          </Button>
        )}
      </div>

      {isEnded && (
        <div className="mt-6 border-t pt-6">
          {meeting.appointment_id ? (
            <div>
              <p className="text-muted-foreground mb-2 text-sm">
                This meeting has ended. AI notes are available.
              </p>
              <Button variant="outline" asChild>
                <a href={href("/notes/:id", { id: meeting.appointment_id })}>
                  View notes →
                </a>
              </Button>
            </div>
          ) : (
            <p className="text-muted-foreground text-sm italic">
              Notes will appear here after the meeting ends and Cosma processes the recording.
            </p>
          )}
        </div>
      )}

      {!isEnded && (
        <div className="mt-6 border-t pt-6">
          <p className="text-muted-foreground text-sm italic">
            Notes will appear here after the meeting ends.
          </p>
        </div>
      )}
    </div>
  )
}

export async function clientLoader(args: Route.ClientLoaderArgs) {
  const { scheduleList } = await import("~/configuration/client")
  const { data } = await scheduleList()

  const meeting = data?.meetings.find(
    (m) => m.zoom_meeting_id === args.params.zoom_meeting_id
  )

  if (!meeting) {
    throw new Response("Meeting not found", { status: 404 })
  }

  return { meeting }
}

export default function ScheduleDetailPage({ loaderData }: Route.ComponentProps) {
  const { meeting } = loaderData

  return (
    <SidebarInset>
      <header className="bg-background sticky top-0 flex shrink-0 items-center gap-2 border-b p-4">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mr-2 h-4" />
        <h1 className="text-lg font-semibold">{meeting.topic}</h1>
      </header>
      <InnerScrollableContent>
        <MeetingDetail meeting={meeting} />
      </InnerScrollableContent>
    </SidebarInset>
  )
}
