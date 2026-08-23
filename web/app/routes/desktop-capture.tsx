import { useRef, useState } from "react"

import type { Route } from "./+types/desktop-capture"

import { Button } from "~/components/ui/button"
import type { Appointment } from "~/configuration/client"
import { createDesktopCaptureLaunch, noteList } from "~/configuration/client"
import {
  buildDesktopLaunchUrl,
  parseParticipantRegions,
} from "~/lib/desktop-capture-handoff"

import { invariant } from "@epic-web/invariant"

type ConsentMethod = "pre_visit_form" | "verbal_attestation" | "phone_ivr"
type LaunchPhase = "idle" | "creating" | "opening" | "error"

export async function clientLoader(_: Route.ClientLoaderArgs) {
  const { data } = await noteList()
  invariant(data, "appointments failed to load")
  return data
}

export function DesktopCaptureForm({
  appointments: loadedAppointments,
}: {
  appointments: Appointment[]
}) {
  const appointments = loadedAppointments.filter(
    (appointment) => !appointment.cancelled_at,
  )
  const [appointmentId, setAppointmentId] = useState(appointments[0]?.id ?? "")
  const [regions, setRegions] = useState("")
  const [method, setMethod] = useState<ConsentMethod>("verbal_attestation")
  const [allPartiesConsented, setAllPartiesConsented] = useState(false)
  const [phase, setPhase] = useState<LaunchPhase>("idle")
  const [launchUrl, setLaunchUrl] = useState<string | null>(null)
  const idempotencyKey = useRef(`desktop-launch-${crypto.randomUUID()}`)

  async function createLaunch() {
    const participantRegions = parseParticipantRegions(regions)
    if (!appointmentId || !participantRegions || !allPartiesConsented) {
      setPhase("error")
      return
    }
    setPhase("creating")
    try {
      const { data, error } = await createDesktopCaptureLaunch({
        path: { appointment_id: appointmentId },
        headers: { "Idempotency-Key": idempotencyKey.current },
        body: {
          modality: "virtual",
          participant_regions: participantRegions,
          method,
          all_parties_consented: true,
          granted_at: new Date().toISOString(),
        },
      })
      const nextLaunchUrl =
        !error && data ? buildDesktopLaunchUrl(data.launch_ticket) : null
      if (!nextLaunchUrl) {
        setPhase("error")
        return
      }
      setLaunchUrl(nextLaunchUrl)
      setPhase("opening")
      window.location.assign(nextLaunchUrl)
    } catch {
      setPhase("error")
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-2xl px-6 py-10">
      <header>
        <p className="text-primary text-sm font-semibold tracking-wide uppercase">
          Cosmas Capture
        </p>
        <h1 className="mt-2 text-3xl font-semibold">Prepare a virtual visit</h1>
        <p className="text-muted-foreground mt-3 text-sm leading-6">
          Choose the canonical encounter and document the consent you obtained.
          This works independently of the scheduling system that created the
          appointment.
        </p>
      </header>

      <section className="mt-8 space-y-5 rounded-2xl border bg-white p-6 shadow-sm">
        <label className="block text-sm font-medium">
          Appointment
          <select
            className="mt-2 w-full rounded-md border bg-white px-3 py-2"
            value={appointmentId}
            onChange={(event) => setAppointmentId(event.target.value)}
          >
            {appointments.length === 0 ? (
              <option value="">No eligible appointments</option>
            ) : null}
            {appointments.map((appointment) => (
              <option key={appointment.id} value={appointment.id}>
                {appointment.patient_name} ·{" "}
                {new Date(appointment.appointment_date).toLocaleString()}
              </option>
            ))}
          </select>
        </label>

        <label className="block text-sm font-medium">
          Participant states or regions
          <input
            className="mt-2 w-full rounded-md border px-3 py-2"
            placeholder="NY, NJ"
            value={regions}
            onChange={(event) => setRegions(event.target.value)}
          />
          <span className="text-muted-foreground mt-1 block text-xs">
            Enter each US postal abbreviation, separated by commas.
          </span>
        </label>

        <label className="block text-sm font-medium">
          Consent method
          <select
            className="mt-2 w-full rounded-md border bg-white px-3 py-2"
            value={method}
            onChange={(event) => setMethod(event.target.value as ConsentMethod)}
          >
            <option value="verbal_attestation">Verbal attestation</option>
            <option value="pre_visit_form">Pre-visit form</option>
            <option value="phone_ivr">Phone or IVR confirmation</option>
          </select>
        </label>

        <label className="flex items-start gap-3 rounded-lg border p-4 text-sm leading-5">
          <input
            checked={allPartiesConsented}
            className="mt-1 size-4"
            type="checkbox"
            onChange={(event) => setAllPartiesConsented(event.target.checked)}
          />
          <span>
            I attest that every participant consented to recording and that the
            states or regions above are accurate.
          </span>
        </label>

        {phase === "error" ? (
          <p className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950">
            Cosmas could not prepare this capture. Check the appointment,
            regions, and consent, then try again.
          </p>
        ) : null}

        <div className="flex flex-wrap gap-3">
          <Button
            disabled={
              !appointmentId || !allPartiesConsented || phase === "creating"
            }
            type="button"
            onClick={() =>
              launchUrl
                ? window.location.assign(launchUrl)
                : void createLaunch()
            }
          >
            {phase === "creating"
              ? "Preparing…"
              : phase === "opening"
                ? "Open Cosmas Capture again"
                : "Verify consent and open desktop app"}
          </Button>
          {phase === "opening" && launchUrl ? (
            <Button
              variant="outline"
              type="button"
              onClick={() => window.location.assign(launchUrl)}
            >
              Retry opening the app
            </Button>
          ) : null}
        </div>
      </section>
    </main>
  )
}

export default function DesktopCapturePage({
  loaderData,
}: Route.ComponentProps) {
  return <DesktopCaptureForm appointments={loaderData.appointments} />
}
