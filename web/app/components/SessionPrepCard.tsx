import { useState } from "react"

import { Button } from "@/components/ui/button"
import { generateSessionPrep } from "~/configuration/client"
import { toast } from "~/hooks/use-toast"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { sessionPrepLatestOptions } from "client/@tanstack/react-query.gen"

const POLL_INTERVAL_MS = 3_000

interface SessionPrepCardProps {
  patientId?: string
  appointmentId?: string
}

// A short, scannable pre-session brief, generated on demand from the
// patient's session history. Brevity is the point: 3-5 talking points and
// open threads, readable in the seconds before a session starts.
export function SessionPrepCard({
  patientId,
  appointmentId,
}: SessionPrepCardProps) {
  const [starting, setStarting] = useState(false)
  const queryClient = useQueryClient()

  const target = {
    ...(patientId ? { patient_id: patientId } : {}),
    ...(appointmentId ? { appointment_id: appointmentId } : {}),
  }

  const queryOptions = sessionPrepLatestOptions({ query: target })

  const { data: prep, isLoading } = useQuery({
    ...queryOptions,
    // a 404 just means no brief has been generated yet
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ? POLL_INTERVAL_MS : false,
  })

  async function handleGenerate() {
    setStarting(true)
    const { data, error } = await generateSessionPrep({ body: target })

    if (error || !data) {
      toast({
        title: "Could not start session prep",
        variant: "destructive",
      })
    } else {
      // seed the cache with the pending row so polling picks it up
      queryClient.setQueryData(queryOptions.queryKey, data)
    }
    setStarting(false)
  }

  const pending = prep?.status === "pending" || starting

  return (
    <section
      data-testid="session-prep-card"
      className="mt-6 rounded-lg border p-4"
    >
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Session prep</h2>
        {prep?.status === "completed" && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleGenerate}
            disabled={pending}
          >
            Regenerate
          </Button>
        )}
      </div>

      {!prep && !isLoading && (
        <div>
          <p className="text-muted-foreground mb-3 text-sm">
            Generate a short brief from this patient&apos;s session history —
            talking points and open threads, readable in under a minute.
          </p>
          <Button onClick={handleGenerate} disabled={pending}>
            {pending ? "Preparing…" : "Prep for session"}
          </Button>
        </div>
      )}

      {prep?.status === "pending" && (
        <p className="text-muted-foreground text-sm italic">Preparing brief…</p>
      )}

      {prep?.status === "failed" && (
        <div>
          <p className="text-destructive mb-3 text-sm">
            Something went wrong generating the brief.
          </p>
          <Button onClick={handleGenerate} disabled={pending} variant="outline">
            Try again
          </Button>
        </div>
      )}

      {prep?.status === "completed" && (
        <div className="space-y-3 text-sm">
          {prep.alerts.length > 0 && (
            <ul className="border-destructive/50 bg-destructive/5 space-y-1 rounded-md border p-2">
              {prep.alerts.map((alert) => (
                <li key={alert} className="text-destructive font-medium">
                  ⚠ {alert}
                </li>
              ))}
            </ul>
          )}

          <ul className="list-disc space-y-1 pl-5">
            {prep.talking_points.map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>

          {prep.open_threads.length > 0 && (
            <div>
              <h3 className="text-muted-foreground mb-1 text-xs font-medium uppercase">
                Open threads from last session
              </h3>
              <ul className="list-disc space-y-1 pl-5">
                {prep.open_threads.map((thread) => (
                  <li key={thread}>{thread}</li>
                ))}
              </ul>
            </div>
          )}

          {prep.personality_note && (
            <p className="text-muted-foreground text-xs">
              Communication style: {prep.personality_note}
            </p>
          )}

          <p className="text-muted-foreground text-xs">
            Built from {prep.visits_in_context}{" "}
            {prep.visits_in_context === 1 ? "prior visit" : "prior visits"}
            {prep.days_since_last_session !== null &&
              prep.days_since_last_session !== undefined &&
              ` · last seen ${prep.days_since_last_session} days ago`}
            . AI-generated — verify against the chart.
          </p>
        </div>
      )}
    </section>
  )
}
