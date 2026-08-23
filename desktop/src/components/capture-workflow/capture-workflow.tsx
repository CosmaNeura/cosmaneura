import { useEffect, useMemo, useState } from "react"

import { NON_PHI_PREVIEW_ENABLED } from "../../constants"
import {
  getDesktopSnapshot,
  listenForDesktopSnapshot,
  openProviderPlatform,
  startCapture,
  stopCapture,
} from "../../lib/desktop-commands"
import {
  canStartCapture,
  canStopCapture,
  idleDesktopSnapshot,
  type DesktopSnapshot,
} from "../../lib/desktop-state"
import {
  authorizedPreviewSnapshot,
  syntheticAppointments,
} from "../../mock"

type PendingAction = "open" | "start" | "stop" | null

function StatusBadge({ snapshot }: { snapshot: DesktopSnapshot }) {
  const tone = snapshot.localCaptureActive
    ? "bg-rose-100 text-rose-800"
    : snapshot.phase === "authorized"
      ? "bg-emerald-100 text-emerald-800"
      : snapshot.phase === "error"
        ? "bg-amber-100 text-amber-900"
        : "bg-slate-100 text-slate-700"

  return (
    <span className={`rounded-full px-3 py-1 text-xs font-semibold ${tone}`}>
      {snapshot.localCaptureActive ? "Recording locally" : snapshot.phase.replaceAll("_", " ")}
    </span>
  )
}

function WorkflowStep({
  complete,
  number,
  title,
  detail,
}: {
  complete: boolean
  number: number
  title: string
  detail: string
}) {
  return (
    <li className="flex gap-3 rounded-xl border border-slate-200 bg-white p-4">
      <span
        aria-hidden="true"
        className={`flex size-8 shrink-0 items-center justify-center rounded-full text-sm font-bold ${
          complete
            ? "bg-emerald-600 text-white"
            : "bg-slate-100 text-slate-600"
        }`}
      >
        {complete ? "✓" : number}
      </span>
      <div>
        <p className="font-semibold text-slate-950">{title}</p>
        <p className="mt-1 text-sm leading-5 text-slate-600">{detail}</p>
      </div>
    </li>
  )
}

export function CaptureWorkflow() {
  const [snapshot, setSnapshot] = useState<DesktopSnapshot>(idleDesktopSnapshot)
  const [localConsentConfirmed, setLocalConsentConfirmed] = useState(false)
  const [pendingAction, setPendingAction] = useState<PendingAction>(null)
  const [selectedSyntheticId, setSelectedSyntheticId] = useState("")

  useEffect(() => {
    let active = true
    let unlisten: (() => void) | undefined

    void getDesktopSnapshot()
      .then((next) => {
        if (active) setSnapshot(next)
      })
      .catch(() => {
        if (active) setSnapshot(idleDesktopSnapshot)
      })

    void listenForDesktopSnapshot((next) => {
      if (active) setSnapshot(next)
    }).then((nextUnlisten) => {
      if (active) unlisten = nextUnlisten
      else nextUnlisten()
    })

    return () => {
      active = false
      unlisten?.()
    }
  }, [])

  const displayedSnapshot = useMemo(() => {
    if (NON_PHI_PREVIEW_ENABLED && selectedSyntheticId) {
      return authorizedPreviewSnapshot
    }
    return snapshot
  }, [selectedSyntheticId, snapshot])

  async function runAction(
    action: Exclude<PendingAction, null>,
    command: () => Promise<DesktopSnapshot>,
  ) {
    setPendingAction(action)
    try {
      setSnapshot(await command())
    } finally {
      setPendingAction(null)
    }
  }

  const appointmentReady =
    displayedSnapshot.appointmentSelection === "confirmed"
  const consentReady = displayedSnapshot.consentStatus === "granted"

  return (
    <main className="mx-auto min-h-screen max-w-3xl px-5 py-8 sm:px-8 sm:py-12">
      <header className="mb-8 flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-semibold tracking-wide text-sky-700 uppercase">
            Cosmas desktop companion
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-slate-950">
            Virtual visit capture
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">
            Select the visit and record consent in the provider platform. This
            companion receives only a server-authorized, short-lived capture
            grant.
          </p>
        </div>
        <StatusBadge snapshot={displayedSnapshot} />
      </header>

      <section aria-labelledby="workflow-heading">
        <h2 id="workflow-heading" className="sr-only">
          Capture workflow
        </h2>
        <ol className="space-y-3">
          <WorkflowStep
            number={1}
            complete={appointmentReady}
            title="Select the appointment"
            detail={
              appointmentReady
                ? "The platform confirmed one appointment without exposing patient details here."
                : "Open Cosmas and choose the virtual appointment you are about to begin."
            }
          />
          <WorkflowStep
            number={2}
            complete={consentReady}
            title="Confirm all-party consent"
            detail={
              consentReady
                ? "The server verified the current consent evidence and capture lease."
                : "Record how and when every participant consented in Cosmas."
            }
          />
          <WorkflowStep
            number={3}
            complete={displayedSnapshot.localCaptureActive}
            title="Capture microphone and system audio"
            detail="Local audio control remains available even if the platform cannot be reached."
          />
        </ol>
      </section>

      {NON_PHI_PREVIEW_ENABLED ? (
        <section className="mt-6 rounded-xl border border-dashed border-sky-300 bg-sky-50 p-4">
          <label
            className="block text-sm font-semibold text-sky-950"
            htmlFor="synthetic-appointment"
          >
            Non-PHI preview appointment
          </label>
          <select
            id="synthetic-appointment"
            className="mt-2 w-full rounded-lg border border-sky-200 bg-white px-3 py-2 text-sm text-slate-900"
            value={selectedSyntheticId}
            onChange={(event) => setSelectedSyntheticId(event.target.value)}
          >
            <option value="">Choose a synthetic fixture</option>
            {syntheticAppointments.map((appointment) => (
              <option key={appointment.id} value={appointment.id}>
                {appointment.label}
              </option>
            ))}
          </select>
          <p className="mt-2 text-xs text-sky-800">
            Preview mode never starts audio or calls a provider.
          </p>
        </section>
      ) : null}

      <section className="mt-6 rounded-xl bg-slate-950 p-5 text-white shadow-lg">
        <p aria-live="polite" className="text-sm text-slate-200">
          {displayedSnapshot.statusMessage}
        </p>

        {appointmentReady && consentReady ? (
          <label className="mt-5 flex items-start gap-3 text-sm leading-5">
            <input
              checked={localConsentConfirmed}
              className="mt-1 size-4 rounded border-slate-400"
              type="checkbox"
              onChange={(event) =>
                setLocalConsentConfirmed(event.target.checked)
              }
            />
            <span>
              I confirm that the selected visit is correct and every
              participant has consented to recording.
            </span>
          </label>
        ) : null}

        <div className="mt-5 flex flex-wrap gap-3">
          {!appointmentReady ? (
            <button
              className="rounded-lg bg-sky-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-sky-400 disabled:opacity-50"
              disabled={pendingAction !== null}
              type="button"
              onClick={() =>
                void runAction("open", openProviderPlatform)
              }
            >
              {pendingAction === "open" ? "Opening…" : "Open Cosmas"}
            </button>
          ) : null}

          {!displayedSnapshot.localCaptureActive ? (
            <button
              className="rounded-lg bg-rose-500 px-4 py-2 text-sm font-semibold text-white hover:bg-rose-400 disabled:cursor-not-allowed disabled:opacity-40"
              disabled={
                !canStartCapture(displayedSnapshot, localConsentConfirmed) ||
                pendingAction !== null
              }
              type="button"
              onClick={() => void runAction("start", startCapture)}
            >
              {pendingAction === "start" ? "Starting…" : "Start capture"}
            </button>
          ) : (
            <button
              className="rounded-lg bg-white px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-slate-100 disabled:opacity-50"
              disabled={!canStopCapture(displayedSnapshot) || pendingAction !== null}
              type="button"
              onClick={() => void runAction("stop", stopCapture)}
            >
              {pendingAction === "stop" ? "Stopping locally…" : "Stop capture"}
            </button>
          )}
        </div>

        {!displayedSnapshot.providerEnabled ? (
          <p className="mt-4 text-xs leading-5 text-amber-200">
            The capture provider is disabled. Patient recording cannot start in
            this build.
          </p>
        ) : null}
      </section>

      <footer className="mt-6 text-xs leading-5 text-slate-500">
        No patient names, visit details, tokens, or provider capabilities are
        displayed or written to the WebView.
      </footer>
    </main>
  )
}
