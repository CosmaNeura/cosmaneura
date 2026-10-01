import { useEffect, useRef, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  VIEW_AS_HARD_BLOCK_EVENT,
  subscribeToViewAsStateChanges,
} from "~/lib/view-as-binding"
import {
  forgetVisitPrepCredential,
  readVisitPrepCredential,
} from "~/lib/visit-prep-browser"
import {
  type PatientInterviewState,
  type PatientResponse,
  type VisitPrepPatientClient,
  VisitPrepPatientFailure,
  createVisitPrepPatientClient,
} from "~/lib/visit-prep-patient-api"

import { InterviewFrame } from "./InterviewFrame"
import { PatientContributionReview } from "./PatientContributionReview"
import { QuestionForm } from "./QuestionForm"

const FAILURE_COPY = {
  unavailable:
    "This link is unavailable. Ask your care team for a new invitation.",
  invalid: "Check your response and try again.",
  conflict:
    "Your preparation changed. Reload its current progress before continuing.",
  limited: "Too many attempts. Wait 15 minutes before trying again.",
  temporary:
    "We could not confirm the save. Your draft is still here; retry the same save.",
} as const

type PendingAnswer = { payload: PatientResponse; key: string }

/** Deterministic interview and saved-answer review; bounded AI follows next. */
export function PatientInterview({
  makeClient = createVisitPrepPatientClient,
}: {
  makeClient?: typeof createVisitPrepPatientClient
}) {
  const client = useRef<VisitPrepPatientClient | null>(null)
  const pendingAnswer = useRef<PendingAnswer | null>(null)
  const busyRef = useRef(false)
  const mounted = useRef(false)
  const generation = useRef(0)
  const lastQuestionKey = useRef<string | undefined>(undefined)
  const [state, setState] = useState<PatientInterviewState | null>(null)
  const [busy, setBusy] = useState(true)
  const [draft, setDraft] = useState("")
  const [firstName, setFirstName] = useState("")
  const [role, setRole] = useState<"patient" | "caregiver">("patient")
  const [error, setError] = useState<VisitPrepPatientFailure["code"] | null>(
    null,
  )
  const [reported, setReported] = useState(false)
  const [reviewing, setReviewing] = useState(false)
  const [contributionComplete, setContributionComplete] = useState<
    boolean | null
  >(null)

  function discardPatientState() {
    generation.current += 1
    busyRef.current = false
    client.current?.dispose()
    client.current = null
    pendingAnswer.current = null
    forgetVisitPrepCredential()
    setState(null)
    setDraft("")
    setFirstName("")
    setReviewing(false)
    setContributionComplete(null)
    setError("unavailable")
    setBusy(false)
  }

  async function run(
    operation: () => Promise<PatientInterviewState | void>,
  ): Promise<void> {
    if (busyRef.current || !client.current) return
    const requestGeneration = generation.current
    busyRef.current = true
    setBusy(true)
    setError(null)
    try {
      const next = await operation()
      if (mounted.current && generation.current === requestGeneration && next) {
        lastQuestionKey.current = next.current_question?.key
        setState(next)
        setFirstName("")
      }
    } catch (failure) {
      if (!mounted.current || generation.current !== requestGeneration) return
      const code =
        failure instanceof VisitPrepPatientFailure ? failure.code : "temporary"
      setError(code)
      if (code === "unavailable") {
        discardPatientState()
      }
    } finally {
      if (generation.current === requestGeneration) {
        busyRef.current = false
        if (mounted.current) setBusy(false)
      }
    }
  }

  useEffect(() => {
    mounted.current = true
    generation.current += 1
    busyRef.current = false
    const token = readVisitPrepCredential()
    if (!token) {
      setError("unavailable")
      setBusy(false)
      return () => {
        mounted.current = false
      }
    }
    try {
      client.current = makeClient(import.meta.env.VITE_PYTHON_URL ?? "", token)
      void run(() => client.current!.resume())
    } catch {
      setError("temporary")
      setBusy(false)
    }
    const forget = discardPatientState
    window.addEventListener("pagehide", forget)
    window.addEventListener(VIEW_AS_HARD_BLOCK_EVENT, forget)
    const unsubscribe = subscribeToViewAsStateChanges(forget)
    const recheck = () => {
      if (
        document.visibilityState !== "visible" ||
        !client.current ||
        busyRef.current ||
        pendingAnswer.current
      )
        return
      const requestGeneration = generation.current
      void run(async () => {
        const next = await client.current!.resume()
        if (
          mounted.current &&
          generation.current === requestGeneration &&
          next.current_question?.key !== lastQuestionKey.current
        )
          setDraft("")
        return next
      })
    }
    window.addEventListener("focus", recheck)
    document.addEventListener("visibilitychange", recheck)
    return () => {
      mounted.current = false
      generation.current += 1
      const closedGeneration = generation.current
      busyRef.current = false
      client.current?.dispose()
      client.current = null
      pendingAnswer.current = null
      window.removeEventListener("pagehide", forget)
      window.removeEventListener(VIEW_AS_HARD_BLOCK_EVENT, forget)
      window.removeEventListener("focus", recheck)
      document.removeEventListener("visibilitychange", recheck)
      unsubscribe()
      // Preserve React StrictMode's immediate setup/cleanup/setup cycle, but
      // discard the module credential on actual route departure.
      queueMicrotask(() => {
        if (!mounted.current && generation.current === closedGeneration)
          forgetVisitPrepCredential()
      })
    }
  }, [makeClient])

  function submit(answer: string | null) {
    if (busyRef.current || !state?.current_question || pendingAnswer.current)
      return
    try {
      pendingAnswer.current = {
        payload: {
          question_key: state.current_question.key,
          response_kind: answer === null ? "skipped" : "answered",
          answer,
        },
        key: crypto.randomUUID(),
      }
    } catch {
      setError("temporary")
      return
    }
    retryAnswer()
  }

  function retryAnswer() {
    const pending = pendingAnswer.current
    if (!pending) return
    const requestGeneration = generation.current
    void run(async () => {
      const next = await client.current!.submit(pending.payload, pending.key)
      if (mounted.current && generation.current === requestGeneration) {
        pendingAnswer.current = null
        setDraft("")
      }
      return next
    })
  }

  function reloadProgress() {
    const requestGeneration = generation.current
    void run(async () => {
      const next = await client.current!.resume()
      if (mounted.current && generation.current === requestGeneration) {
        pendingAnswer.current = null
        if (next.current_question?.key !== state?.current_question?.key)
          setDraft("")
      }
      return next
    })
  }

  async function report() {
    const requestGeneration = generation.current
    await run(async () => {
      await client.current!.reportWrongRecipient()
      if (mounted.current && generation.current === requestGeneration) {
        client.current?.dispose()
        client.current = null
        pendingAnswer.current = null
        forgetVisitPrepCredential()
        setState(null)
        setDraft("")
        setFirstName("")
        setReviewing(false)
        setContributionComplete(null)
        setReported(true)
      }
    })
  }

  return (
    <InterviewFrame>
      <p className="mb-6 text-sm text-[var(--brand-muted-text)]">
        Share what matters before your visit. This is not monitored in real
        time. For urgent help, contact your care team or local emergency
        services.
      </p>
      {reported ? (
        <section role="status">
          <h1 className="text-2xl font-semibold">
            Thank you for letting us know
          </h1>
          <p className="mt-3">
            This invitation has been stopped. Do not forward or use the link.
          </p>
        </section>
      ) : reviewing && client.current ? (
        <PatientContributionReview
          client={client.current}
          onUnavailable={discardPatientState}
          onClose={() => setReviewing(false)}
          onStatus={(complete) => setContributionComplete(complete)}
        />
      ) : state?.status === "confirmation_required" ? (
        <form
          onSubmit={(event) => {
            event.preventDefault()
            void run(() => client.current!.confirm(firstName, role))
          }}
        >
          <h1 className="text-2xl font-semibold">Before we begin</h1>
          <p className="mt-3">
            Enter the first name and role of the person this invitation was sent
            to. This check is not identity verification.
          </p>
          <label htmlFor="recipient-name" className="mt-6 block">
            Recipient first name
          </label>
          <input
            id="recipient-name"
            value={firstName}
            maxLength={120}
            autoComplete="off"
            disabled={busy}
            onChange={(event) => setFirstName(event.target.value)}
            className="mt-2 w-full rounded border bg-white p-3 text-base"
          />
          <label htmlFor="recipient-role" className="mt-4 block">
            This invitation was sent to
          </label>
          <select
            id="recipient-role"
            value={role}
            disabled={busy}
            onChange={(event) =>
              setRole(event.target.value as "patient" | "caregiver")
            }
            className="mt-2 w-full rounded border bg-white p-3 text-base"
          >
            <option value="patient">The patient</option>
            <option value="caregiver">
              A caregiver answering for the patient
            </option>
          </select>
          <Button
            className="mt-6"
            type="submit"
            disabled={busy || !firstName.trim()}
          >
            {busy ? "Checking…" : "Begin Visit Prep"}
          </Button>
        </form>
      ) : state?.status === "in_progress" && state.current_question ? (
        <>
          <p className="mb-6 text-sm">
            For {state.physician_display_name} · {state.practice_display_name}
          </p>
          <QuestionForm
            question={state.current_question}
            draft={draft}
            onDraftChange={setDraft}
            onSubmit={submit}
            busy={busy || pendingAnswer.current !== null}
          />
        </>
      ) : state?.status === "ready_to_complete" ? (
        <section>
          <h1 className="text-2xl font-semibold">Your topics are saved</h1>
          <p className="mt-3">
            You answered or skipped {state.submitted_questions} topics. Finish
            to mark this preparation complete.
          </p>
          <Button
            className="mt-6 mr-3"
            variant="outline"
            disabled={busy}
            onClick={() => setReviewing(true)}
          >
            Review or change saved answers
          </Button>
          <Button
            className="mt-6"
            disabled={busy || contributionComplete === false}
            onClick={() => void run(() => client.current!.complete())}
          >
            {busy ? "Finishing…" : "Finish Visit Prep"}
          </Button>
        </section>
      ) : state?.status === "completed" ? (
        <section role="status">
          <h1 className="text-2xl font-semibold">
            {contributionComplete === false
              ? "An updated topic needs a response"
              : "Visit Prep complete"}
          </h1>
          <p className="mt-3">
            Your responses have been saved for your care team. This does not
            mean a provider has reviewed them.
          </p>
          {contributionComplete === false && (
            <p className="mt-3">
              A change made another topic relevant. Answer or skip it in your
              saved-answer review.
            </p>
          )}
          <Button
            className="mt-6"
            variant="outline"
            disabled={busy}
            onClick={() => setReviewing(true)}
          >
            Review or change saved answers
          </Button>
        </section>
      ) : busy ? (
        <p role="status">Loading your preparation…</p>
      ) : null}
      {!reviewing && error && (
        <p role="alert" className="mt-6 text-sm text-red-700">
          {FAILURE_COPY[error]}
        </p>
      )}
      {!reviewing && error && client.current && !busy && (
        <div className="mt-4 flex flex-wrap gap-3">
          {pendingAnswer.current && error !== "conflict" && (
            <Button onClick={retryAnswer}>Retry same save</Button>
          )}
          {(error === "conflict" || !state) && (
            <Button onClick={reloadProgress}>Reload current progress</Button>
          )}
          {pendingAnswer.current && error === "invalid" && (
            <Button
              variant="outline"
              onClick={() => {
                pendingAnswer.current = null
                setError(null)
              }}
            >
              Edit my response
            </Button>
          )}
        </div>
      )}
      {!reported && !reviewing && client.current && (
        <Button
          variant="ghost"
          className="mt-8 self-start"
          disabled={busy}
          onClick={() => void report()}
        >
          This invitation was sent to the wrong person
        </Button>
      )}
      <p className="mt-6 text-xs text-[var(--brand-muted-text)]">
        Saved progress can be resumed from your original invitation. Unsaved
        text is kept only on this page.
      </p>
    </InterviewFrame>
  )
}
