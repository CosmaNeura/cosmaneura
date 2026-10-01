import { useEffect, useRef, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  type PatientConfirmation,
  type PatientContribution,
  type PatientContributionChange,
  type PatientContributionReview as ReviewPayload,
  type VisitPrepPatientClient,
  VisitPrepPatientFailure,
} from "~/lib/visit-prep-patient-api"

import { QuestionForm } from "./QuestionForm"

type Pending =
  | { kind: "change"; payload: PatientContributionChange; key: string }
  | { kind: "review"; payload: ReviewPayload; key: string }

const COPY = {
  unavailable:
    "This link is unavailable. Ask your care team for a new invitation.",
  invalid: "Check your response and try again.",
  conflict: "Your saved answers changed. Reload them before continuing.",
  limited:
    "Too many confirmation attempts. Wait 15 minutes before trying again.",
  temporary:
    "We could not confirm the request. Retry the same save if one is pending.",
} as const

/** Patient wording and recipient evidence live only in this mounted page. */
export function PatientContributionReview({
  client,
  onUnavailable,
  onClose,
  onStatus,
}: {
  client: VisitPrepPatientClient
  onUnavailable: () => void
  onClose: () => void
  onStatus: (complete: boolean, reviewed: boolean) => void
}) {
  const [firstName, setFirstName] = useState("")
  const [role, setRole] = useState<PatientConfirmation["role"]>("patient")
  const [current, setCurrent] = useState<PatientContribution | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<VisitPrepPatientFailure["code"] | null>(
    null,
  )
  const confirmation = useRef<PatientConfirmation | null>(null)
  const pending = useRef<Pending | null>(null)
  const refreshRequired = useRef(false)
  const busyRef = useRef(false)
  const generation = useRef(0)
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    generation.current += 1
    const conceal = () => {
      // Do not leave saved answers visible when returning from another surface.
      // A pending exact save survives in memory, but its wording is concealed.
      generation.current += 1
      busyRef.current = false
      setBusy(false)
      setCurrent(null)
      setEditing(null)
      setDraft("")
      setFirstName("")
      setError(null)
      if (!pending.current) confirmation.current = null
      refreshRequired.current = pending.current !== null
    }
    const visibility = () => {
      if (document.visibilityState !== "visible") conceal()
    }
    window.addEventListener("blur", conceal)
    document.addEventListener("visibilitychange", visibility)
    return () => {
      mounted.current = false
      generation.current += 1
      confirmation.current = null
      pending.current = null
      window.removeEventListener("blur", conceal)
      document.removeEventListener("visibilitychange", visibility)
    }
  }, [])

  async function run(operation: () => Promise<void>) {
    if (busyRef.current) return
    const requestGeneration = generation.current
    busyRef.current = true
    setBusy(true)
    setError(null)
    try {
      await operation()
    } catch (failure) {
      if (!mounted.current || generation.current !== requestGeneration) return
      const code =
        failure instanceof VisitPrepPatientFailure ? failure.code : "temporary"
      setError(code)
      if (code === "unavailable") {
        confirmation.current = null
        pending.current = null
        setCurrent(null)
        setDraft("")
        setFirstName("")
        onUnavailable()
      }
    } finally {
      if (generation.current === requestGeneration) {
        busyRef.current = false
        if (mounted.current) setBusy(false)
      }
    }
  }

  async function load(confirmed: PatientConfirmation) {
    const requestGeneration = generation.current
    const next = await client.readContribution(confirmed)
    if (!mounted.current || generation.current !== requestGeneration) return
    confirmation.current = confirmed
    refreshRequired.current = false
    setCurrent(next)
    onStatus(next.current_answers_complete, next.current_answers_reviewed)
    setFirstName("")
    setEditing(null)
    setDraft("")
  }

  function retrySave() {
    const save = pending.current
    const confirmed = confirmation.current
    if (!save || !confirmed) return
    const requestGeneration = generation.current
    void run(async () => {
      const receipt =
        save.kind === "change"
          ? await client.correctContribution(save.payload, save.key)
          : await client.reviewContribution(save.payload, save.key)
      if (!mounted.current || generation.current !== requestGeneration) return
      onStatus(
        receipt.current_answers_complete,
        receipt.current_answers_reviewed,
      )
      pending.current = null
      refreshRequired.current = true
      setEditing(null)
      setDraft("")
      if (
        save.kind === "review" &&
        receipt.current_answers_reviewed &&
        current &&
        receipt.basis.original_response_count ===
          save.payload.basis.original_response_count &&
        receipt.basis.event_sequence === save.payload.basis.event_sequence + 1
      ) {
        // The value-free receipt confirms precisely the displayed basis.
        setCurrent({
          ...current,
          basis: receipt.basis,
          current_answers_complete: receipt.current_answers_complete,
          current_answers_reviewed: true,
        })
        refreshRequired.current = false
      } else {
        // A parent correction may reveal or hide a topic. Only Core determines
        // the new applicability; do not invent it from optimistic local state.
        setCurrent(null)
        await load(confirmed)
      }
    })
  }

  function saveChange(answer: string | null) {
    if (
      busyRef.current ||
      pending.current ||
      !current ||
      !confirmation.current ||
      !editing
    )
      return
    try {
      pending.current = {
        kind: "change",
        key: crypto.randomUUID(),
        payload: {
          ...confirmation.current,
          basis: current.basis,
          question_key: editing,
          response_kind: answer === null ? "skipped" : "answered",
          answer,
        },
      }
      retrySave()
    } catch {
      setError("temporary")
    }
  }

  function confirmReview() {
    if (
      busyRef.current ||
      pending.current ||
      !current?.current_answers_complete ||
      !confirmation.current
    )
      return
    try {
      pending.current = {
        kind: "review",
        key: crypto.randomUUID(),
        payload: {
          ...confirmation.current,
          basis: current.basis,
        },
      }
      retrySave()
    } catch {
      setError("temporary")
    }
  }

  const topic = current?.topics.find((row) => row.question.key === editing)
  const blocked = busy || pending.current !== null || refreshRequired.current
  function wording(
    kind: string | null,
    answer: string | null,
    row: NonNullable<typeof topic>,
  ) {
    if (kind === "skipped") return "You skipped this topic."
    if (kind === null) return "Not answered yet."
    return (
      row.question.choices.find((choice) => choice.value === answer)?.label ??
      answer
    )
  }

  return (
    <section>
      {!current ? (
        <>
          <h1 className="text-2xl font-semibold">Review your saved answers</h1>
          <p className="mt-3">
            Before displaying saved answers, enter the recipient first name and
            role again. This check is not identity verification.
          </p>
          {!pending.current && !refreshRequired.current && (
            <form
              onSubmit={(event) => {
                event.preventDefault()
                const confirmed = { first_name: firstName, role }
                void run(() => load(confirmed))
              }}
            >
              <label htmlFor="review-recipient-name" className="mt-6 block">
                Recipient first name
              </label>
              <input
                id="review-recipient-name"
                value={firstName}
                maxLength={120}
                autoComplete="off"
                disabled={busy}
                onChange={(event) => setFirstName(event.target.value)}
                className="mt-2 w-full rounded border bg-white p-3 text-base"
              />
              <label htmlFor="review-recipient-role" className="mt-4 block">
                This invitation was sent to
              </label>
              <select
                id="review-recipient-role"
                value={role}
                disabled={busy}
                onChange={(event) =>
                  setRole(event.target.value as PatientConfirmation["role"])
                }
                className="mt-2 w-full rounded border bg-white p-3 text-base"
              >
                <option value="patient">The patient</option>
                <option value="caregiver">
                  A caregiver answering for the patient
                </option>
              </select>
              <Button
                type="submit"
                className="mt-6"
                disabled={busy || !firstName.trim()}
              >
                {busy ? "Checking…" : "Show saved answers"}
              </Button>
            </form>
          )}
        </>
      ) : topic ? (
        <>
          <p className="mb-4 text-sm">
            Saving a change preserves your earlier wording. It does not edit the
            provider’s clinical record.
          </p>
          <QuestionForm
            question={topic.question}
            draft={draft}
            onDraftChange={setDraft}
            onSubmit={saveChange}
            busy={blocked}
          />
          <Button
            variant="outline"
            className="mt-4"
            disabled={blocked}
            onClick={() => {
              setEditing(null)
              setDraft("")
            }}
          >
            Cancel edit
          </Button>
        </>
      ) : (
        <>
          <h1 className="text-2xl font-semibold">Your saved answers</h1>
          <p className="mt-3">
            {current.actor_role === "caregiver"
              ? "Caregiver report for the patient."
              : "Patient report."}{" "}
            You can change or skip a response. Your original wording is
            retained.
          </p>
          <div className="mt-6 grid gap-5">
            {current.topics.map((row) => (
              <article
                key={row.question.key}
                className="min-w-0 rounded-lg border border-[var(--brand-rule)] p-4"
              >
                <h2 className="text-lg font-semibold">{row.question.prompt}</h2>
                <p className="mt-3 break-words whitespace-pre-wrap">
                  {wording(row.response_kind, row.answer, row)}
                </p>
                {row.changed && (
                  <details className="mt-3 text-sm">
                    <summary>Earlier wording retained</summary>
                    <p className="mt-2 break-words whitespace-pre-wrap">
                      {wording(
                        row.initial_response_kind,
                        row.initial_answer,
                        row,
                      )}
                    </p>
                  </details>
                )}
                <Button
                  variant="outline"
                  className="mt-4"
                  aria-label={`${row.response_kind === null ? "Answer" : "Change"}: ${row.question.prompt}`}
                  disabled={blocked}
                  onClick={() => {
                    setEditing(row.question.key)
                    setDraft(row.answer ?? "")
                    setError(null)
                  }}
                >
                  {row.response_kind === null
                    ? "Answer topic"
                    : "Change response"}
                </Button>
              </article>
            ))}
          </div>
          {current.current_answers_reviewed ? (
            <p role="status" className="mt-6">
              You confirmed these saved answers. This does not mean a provider
              has reviewed them.
            </p>
          ) : (
            <>
              <p className="mt-6">
                {current.current_answers_complete
                  ? "Confirm that these answers reflect what you want to share."
                  : "Answer or explicitly skip the remaining topic before confirming your answers."}
              </p>
              <Button
                className="mt-4"
                disabled={blocked || !current.current_answers_complete}
                onClick={confirmReview}
              >
                Confirm my answers
              </Button>
            </>
          )}
        </>
      )}
      {error && (
        <p role="alert" className="mt-6 text-sm text-red-700">
          {COPY[error]}
        </p>
      )}
      {!busy && pending.current && error !== "conflict" && (
        <Button className="mt-4" onClick={retrySave}>
          Retry same save
        </Button>
      )}
      {!busy &&
        confirmation.current &&
        (error === "conflict" ||
          (refreshRequired.current && !pending.current)) && (
          <Button
            className="mt-4"
            onClick={() => {
              pending.current = null
              refreshRequired.current = true
              setCurrent(null)
              void run(() => load(confirmation.current!))
            }}
          >
            Reload saved answers
          </Button>
        )}
      {!busy && pending.current?.kind === "change" && error === "invalid" && (
        <Button
          variant="outline"
          className="mt-4"
          onClick={() => {
            pending.current = null
            refreshRequired.current = false
            setError(null)
          }}
        >
          Edit my response
        </Button>
      )}
      <Button
        variant="ghost"
        className="mt-6"
        disabled={busy || pending.current !== null}
        onClick={onClose}
      >
        Back to preparation
      </Button>
    </section>
  )
}
