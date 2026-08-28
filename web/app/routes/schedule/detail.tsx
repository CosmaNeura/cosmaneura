import { useState } from "react"
import { href, useNavigate } from "react-router"
import { useRevalidator } from "react-router"

import { Route } from "./+types/detail"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarTrigger } from "@/components/ui/sidebar"
import { InnerScrollableContent } from "~/components/shared/InnerScrollableContent"
import { authorizeZoomCapture, cancelMeeting } from "~/configuration/client"
import type {
  Appointment,
  Patient,
  ZoomMeetingResponse,
} from "~/configuration/client"
import { toast } from "~/hooks/use-toast"

function formatDateTime(isoString: string): string {
  return new Date(isoString).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function formatDateOfBirth(isoString: string): string {
  const calendarDate = isoString.slice(0, 10)
  return new Date(`${calendarDate}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  })
}

function CaptureAuthorization({
  appointments,
  meeting,
  patients,
}: {
  appointments: Appointment[]
  meeting: ZoomMeetingResponse
  patients: Patient[]
}) {
  const [open, setOpen] = useState(false)
  const [selectedAppointmentId, setSelectedAppointmentId] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const { revalidate } = useRevalidator()
  const patientsById = new Map(patients.map((patient) => [patient.id, patient]))
  const selectableAppointments = appointments.filter((appointment) =>
    patientsById.has(appointment.patient_id),
  )

  function resetForm() {
    setSelectedAppointmentId("")
  }

  function handleOpenChange(nextOpen: boolean) {
    if (submitting) return
    setOpen(nextOpen)
    if (!nextOpen) resetForm()
  }

  if (meeting.capture_authorized) {
    return (
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/30">
        <p className="font-medium text-emerald-900 dark:text-emerald-100">
          Cosmas meeting bot enabled
        </p>
        <p className="mt-1 text-sm text-emerald-800 dark:text-emerald-200">
          Cosmas is authorized to join this Zoom meeting and prepare notes.
        </p>
      </div>
    )
  }

  async function handleAuthorize(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (!selectedAppointmentId) return

    setSubmitting(true)
    try {
      const { data, error } = await authorizeZoomCapture({
        path: { zoom_meeting_id: meeting.zoom_meeting_id },
        body: {
          appointment_id: selectedAppointmentId,
        },
      })

      if (error || !data) {
        toast({
          title: "Could not enable the meeting bot",
          description: "Please verify the selected appointment and try again.",
          variant: "destructive",
        })
        return
      }

      setOpen(false)
      resetForm()
      toast(
        data.state === "armed"
          ? { title: "Cosmas meeting bot enabled" }
          : {
              title: "Meeting bot authorization saved",
              description:
                "The bot will be armed automatically when Zoom starts this meeting.",
            },
      )
      revalidate()
    } catch {
      toast({
        title: "Could not enable the meeting bot",
        description: "Please try again.",
        variant: "destructive",
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="rounded-lg border p-4">
      <p className="font-medium">Enable the Cosmas meeting bot</p>
      <p className="text-muted-foreground mt-1 text-sm">
        Link this Zoom meeting to an existing Cosmas appointment. The patient
        will still be asked for recording consent in Zoom.
      </p>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogTrigger asChild>
          <Button className="mt-4 w-full sm:w-auto">Enable meeting bot</Button>
        </DialogTrigger>
        <DialogContent
          className="max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] overflow-y-auto rounded-lg sm:max-w-md"
          data-ph-no-capture=""
          data-sentry-block=""
        >
          <DialogHeader>
            <DialogTitle>Select the appointment</DialogTitle>
            <DialogDescription>
              Choose the existing Cosmas appointment that belongs to this Zoom
              visit. This does not replace the patient&apos;s in-meeting
              recording consent.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleAuthorize} className="space-y-5">
            {selectableAppointments.length ? (
              <div className="space-y-2">
                <Label htmlFor="capture-appointment">Cosmas appointment</Label>
                <select
                  id="capture-appointment"
                  value={selectedAppointmentId}
                  onChange={(event) =>
                    setSelectedAppointmentId(event.target.value)
                  }
                  required
                  disabled={submitting}
                  className="border-input bg-background focus-visible:ring-ring h-11 w-full rounded-md border px-3 text-base shadow-sm focus-visible:ring-1 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="" disabled>
                    Select an appointment
                  </option>
                  {selectableAppointments.map((appointment) => {
                    const patient = patientsById.get(appointment.patient_id)
                    if (!patient) return null

                    return (
                      <option key={appointment.id} value={appointment.id}>
                        {patient.name} — DOB{" "}
                        {formatDateOfBirth(patient.date_of_birth)} —{" "}
                        {formatDateTime(appointment.appointment_date)}
                      </option>
                    )
                  })}
                </select>
              </div>
            ) : (
              <p className="text-muted-foreground rounded-md border p-3 text-sm">
                No existing Cosmas appointments are available to link.
              </p>
            )}
            <p className="text-muted-foreground text-sm">
              Confirm that the selected appointment is for this Zoom visit
              before enabling the bot.
            </p>
            <DialogFooter className="gap-2 sm:gap-0">
              <Button
                type="button"
                variant="outline"
                onClick={() => handleOpenChange(false)}
                disabled={submitting}
                className="w-full sm:w-auto"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={submitting || !selectedAppointmentId}
                className="w-full sm:w-auto"
              >
                {submitting ? "Enabling..." : "Confirm and enable"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export function MeetingDetail({
  appointments,
  captureEnabled,
  meeting,
  patients,
}: {
  appointments: Appointment[]
  captureEnabled: boolean
  meeting: ZoomMeetingResponse
  patients: Patient[]
}) {
  const [cancelling, setCancelling] = useState(false)
  const { revalidate } = useRevalidator()
  const navigate = useNavigate()

  const isScheduled = meeting.status === "scheduled"
  const isActive =
    meeting.status === "scheduled" || meeting.status === "started"
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
        {isActive && meeting.join_url && (
          <Button
            asChild
            className="bg-[#0E72ED] text-white hover:bg-[#0E72ED]/90"
          >
            <a
              href={meeting.join_url}
              target="_blank"
              rel="noopener noreferrer"
              data-ph-no-capture=""
              data-sentry-block=""
            >
              Join Meeting
            </a>
          </Button>
        )}

        {isScheduled && (
          <Button
            variant="outline"
            onClick={handleCancel}
            disabled={cancelling}
          >
            {cancelling ? "Cancelling..." : "Cancel Meeting"}
          </Button>
        )}
      </div>

      {captureEnabled && isActive && (
        <div className="mt-6">
          <CaptureAuthorization
            meeting={meeting}
            appointments={appointments}
            patients={patients}
          />
        </div>
      )}

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
              Notes will appear here after the meeting ends and Cosma processes
              the recording.
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
  const { noteList, patientList, scheduleList } = await import(
    "~/configuration/client"
  )
  const { data } = await scheduleList()
  const captureEnabled = data?.capture_available ?? false

  const meeting = data?.meetings.find(
    (m) => m.zoom_meeting_id === args.params.zoom_meeting_id,
  )

  if (!meeting) {
    throw new Response("Meeting not found", { status: 404 })
  }

  const needsAppointmentOptions =
    captureEnabled &&
    !meeting.capture_authorized &&
    (meeting.status === "scheduled" || meeting.status === "started")
  if (!needsAppointmentOptions) {
    return {
      appointments: [],
      appointmentsUnavailable: false,
      captureEnabled,
      meeting,
      patients: [],
    }
  }

  const [
    { data: noteData, error: noteError },
    { data: patientData, error: patientError },
  ] = await Promise.all([noteList(), patientList()])

  return {
    appointments: noteData?.appointments ?? [],
    appointmentsUnavailable: Boolean(noteError || patientError),
    captureEnabled,
    meeting,
    patients: patientData ?? [],
  }
}

export default function ScheduleDetailPage({
  loaderData,
}: Route.ComponentProps) {
  const {
    appointments,
    appointmentsUnavailable,
    captureEnabled,
    meeting,
    patients,
  } = loaderData

  return (
    <SidebarInset>
      <header className="bg-background sticky top-0 flex shrink-0 items-center gap-2 border-b p-4">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mr-2 h-4" />
        <h1 className="text-lg font-semibold">{meeting.topic}</h1>
      </header>
      <InnerScrollableContent>
        {appointmentsUnavailable ? (
          <p className="text-muted-foreground mb-4 rounded-md border p-3 text-sm">
            Appointments could not be loaded. Refresh the page and try again.
          </p>
        ) : null}
        <MeetingDetail
          meeting={meeting}
          appointments={appointments}
          captureEnabled={captureEnabled}
          patients={patients}
        />
      </InnerScrollableContent>
    </SidebarInset>
  )
}
