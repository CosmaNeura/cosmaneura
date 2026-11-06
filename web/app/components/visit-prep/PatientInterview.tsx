import { useEffect, useMemo, useState } from "react"
import { Button } from "~/components/ui/button"
import {
  VisitPrepPatientFailure, createVisitPrepPatientClient, forgetVisitPrepCredential,
  readVisitPrepCredential, type InterviewState,
} from "~/lib/visit-prep-api"
import { requireEnv } from "~/utils/environment"

export function PatientInterview() {
  const token = useMemo(() => readVisitPrepCredential(), [])
  const [state, setState] = useState<InterviewState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState("")
  const [role, setRole] = useState<"patient" | "caregiver">("patient")
  const [answer, setAnswer] = useState("")
  const client = useMemo(() => {
    if (!token) return null
    try { return createVisitPrepPatientClient(requireEnv("VITE_PYTHON_URL"), token) }
    catch { return null }
  }, [token])

  useEffect(() => {
    if (!client) { setError("This Visit Prep link is unavailable."); return }
    let cancelled = false
    client.resume().then(
      (next) => !cancelled && setState(next),
      () => !cancelled && setError("This Visit Prep link is unavailable."),
    )
    return () => { cancelled = true; client.dispose(); forgetVisitPrepCredential() }
  }, [client])

  async function run(action: () => Promise<InterviewState>) {
    setError(null)
    try { setState(await action()); setAnswer("") }
    catch (err) {
      setError(err instanceof VisitPrepPatientFailure ? "That step could not be completed." : "Something went wrong.")
    }
  }

  const q = state?.current_question
  const submit = (kind: "answered" | "skipped", value: string | null) =>
    client && q && run(() =>
      client.submit({ question_key: q.key, response_kind: kind, answer: value }, crypto.randomUUID()),
    )

  return (
    <main className="mx-auto min-h-dvh max-w-[390px] space-y-4 p-4" data-testid="visit-prep-patient" data-phone-max={390}>
      <h1 className="text-xl font-semibold">Visit Prep</h1>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {!state && !error && <p className="text-sm">Loading…</p>}
      {state?.status === "confirmation_required" && (
        <section className="space-y-3" aria-labelledby="vp-confirm">
          <h2 id="vp-confirm" className="text-lg font-medium">Before we begin</h2>
          <label className="block text-sm">First name
            <input className="mt-1 w-full rounded border px-3 py-2" value={name} onChange={(e) => setName(e.target.value)} autoComplete="given-name" />
          </label>
          <label className="block text-sm">I am answering as
            <select className="mt-1 w-full rounded border px-3 py-2" value={role} onChange={(e) => setRole(e.target.value as "patient" | "caregiver")}>
              <option value="patient">Patient</option>
              <option value="caregiver">Caregiver</option>
            </select>
          </label>
          <Button disabled={!client || !name.trim()} onClick={() => client && run(() => client.confirm(name.trim(), role))}>
            Confirm and continue
          </Button>
        </section>
      )}
      {state?.status === "in_progress" && q && (
        <section className="space-y-3" aria-labelledby="vp-question">
          <p className="text-sm text-muted-foreground">{state.submitted_questions + 1} of {state.maximum_questions}</p>
          <h2 id="vp-question" className="text-lg font-medium break-words">{q.prompt}</h2>
          {q.helper_text && <p className="text-sm text-muted-foreground">{q.helper_text}</p>}
          {q.kind === "choice" ? (
            <div className="space-y-2">
              {q.choices.map((c) => (
                <Button key={c.value} variant={answer === c.value ? "default" : "outline"} className="w-full justify-start" onClick={() => setAnswer(c.value)}>
                  {c.label}
                </Button>
              ))}
            </div>
          ) : (
            <textarea className="min-h-28 w-full rounded border px-3 py-2" value={answer} onChange={(e) => setAnswer(e.target.value)} />
          )}
          <div className="flex gap-2">
            <Button disabled={!client || !answer.trim()} onClick={() => submit("answered", answer.trim())}>Save answer</Button>
            {q.key !== "story" && (
              <Button variant="outline" disabled={!client} onClick={() => submit("skipped", null)}>Skip</Button>
            )}
          </div>
        </section>
      )}
      {state?.status === "ready_to_complete" && (
        <section className="space-y-3">
          <h2 className="text-lg font-medium">Ready to submit</h2>
          <Button disabled={!client} onClick={() => client && run(() => client.complete())}>Confirm my answers</Button>
        </section>
      )}
      {state?.status === "completed" && (
        <section className="space-y-2">
          <h2 className="text-lg font-medium">Thank you</h2>
          <p className="text-sm">Your Visit Prep answers were submitted.</p>
        </section>
      )}
    </main>
  )
}
