import { useEffect, useRef, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  type PatientClarificationChoice,
  type PatientClarificationRequest,
  type PatientClarificationView,
  type PatientConfirmation,
  type PatientContribution,
  type PatientContributionChange,
  type PatientExplanation,
  type PatientFollowupAnswer,
  type PatientFollowupView,
  type PatientContributionReview as ReviewPayload,
  type VisitPrepPatientClient,
  VisitPrepPatientFailure,
} from "~/lib/visit-prep-patient-api"

import { QuestionForm } from "./QuestionForm"

type Pending =
  | { kind: "generate"; payload: PatientClarificationRequest; key: string }
  | { kind: "change"; payload: PatientContributionChange; key: string }
  | { kind: "review"; payload: ReviewPayload; key: string }
  | { kind: "explanation"; payload: PatientExplanation; key: string }
  | {
      kind: "clarification"
      payload: PatientClarificationChoice
      key: string
      basis: PatientContribution["basis"]
    }
  | {
      kind: "followup"
      payload: PatientFollowupAnswer
      key: string
      view: PatientFollowupView
    }

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
  continuedRole = null,
}: {
  client: VisitPrepPatientClient
  onUnavailable: () => void
  onClose: () => void
  onStatus: (complete: boolean, reviewed: boolean) => void
  continuedRole?: PatientConfirmation["role"] | null
}) {
  const [firstName, setFirstName] = useState("")
  const [role, setRole] = useState<PatientConfirmation["role"]>("patient")
  const [current, setCurrent] = useState<PatientContribution | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [explaining, setExplaining] = useState<string | null>(null)
  const [draft, setDraft] = useState("")
  const [proposal, setProposal] = useState<PatientClarificationView | null>(
    null,
  )
  const [editingProposal, setEditingProposal] = useState(false)
  const [followup, setFollowup] = useState<PatientFollowupView | null>(null)
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
    busyRef.current = false
    const conceal = () => {
      // Do not leave saved answers visible when returning from another surface.
      // A pending exact save survives in memory, but its wording is concealed.
      generation.current += 1
      busyRef.current = false
      setBusy(false)
      setCurrent(null)
      setEditing(null)
      setExplaining(null)
      setDraft("")
      setProposal(null)
      setFollowup(null)
      setEditingProposal(false)
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

  useEffect(() => {
    if (continuedRole) void run(() => load({ role: continuedRole }))
  }, [continuedRole])

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
      if (code === "conflict") {
        refreshRequired.current = true
        setCurrent(null)
        setProposal(null)
        setFollowup(null)
        setEditingProposal(false)
        setEditing(null)
        setExplaining(null)
        setDraft("")
      }
      if (code === "unavailable") {
        confirmation.current = null
        pending.current = null
        setCurrent(null)
        setDraft("")
        setProposal(null)
        setFollowup(null)
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
    setExplaining(null)
    setDraft("")
    setProposal(null)
    setFollowup(null)
    setEditingProposal(false)
  }

  function retrySave() {
    const save = pending.current
    const confirmed = confirmation.current
    if (!save || !confirmed) return
    const requestGeneration = generation.current
    void run(async () => {
      if (save.kind === "generate") {
        const receipt = await client.generateClarification(
          save.payload,
          save.key,
        )
        if (!mounted.current || generation.current !== requestGeneration) return
        if (
          receipt.basis.original_response_count !==
            save.payload.basis.original_response_count ||
          receipt.basis.event_sequence !== save.payload.basis.event_sequence
        )
          throw new VisitPrepPatientFailure("conflict")
        pending.current = null
        const view = await client.readClarification(
          confirmed,
          save.payload.question_key,
        )
        if (!mounted.current || generation.current !== requestGeneration) return
        if (
          view.basis.original_response_count !==
            receipt.basis.original_response_count ||
          view.basis.event_sequence !== receipt.basis.event_sequence
        )
          throw new VisitPrepPatientFailure("conflict")
        if (view.status === "available") {
          if (view.proposal_id !== receipt.proposal_id)
            throw new VisitPrepPatientFailure("conflict")
          setFollowup(null)
          setProposal(view)
        } else {
          await loadFollowup(confirmed, save.payload.question_key)
          if (!mounted.current || generation.current !== requestGeneration)
            return
          setProposal(view)
        }
        return
      }
      if (save.kind === "followup") {
        const receipt = await client.answerFollowup(save.payload, save.key)
        if (!mounted.current || generation.current !== requestGeneration) return
        if (
          receipt.basis.original_response_count !==
            save.view.basis.original_response_count ||
          receipt.basis.event_sequence !== save.view.basis.event_sequence ||
          receipt.turn_sequence !== save.view.turns.length + 1
        )
          throw new VisitPrepPatientFailure("temporary")
        pending.current = null
        setDraft("")
        if (current) {
          // The bound receipt acknowledges precisely these explicit words.
          // No answer, choice, contribution basis or applicability changes.
          setFollowup({
            ...save.view,
            status: "answered",
            source_turn_id: receipt.turn_id,
            turns: [
              ...save.view.turns,
              {
                prompt: save.view.pending_prompt!,
                patient_words: save.payload.patient_words,
              },
            ],
          })
          refreshRequired.current = false
        } else {
          refreshRequired.current = true
          await loadFollowup(confirmed, save.view.question_key)
        }
        return
      }
      const receipt =
        save.kind === "change"
          ? await client.correctContribution(save.payload, save.key)
          : save.kind === "explanation"
            ? await client.explainContribution(save.payload, save.key)
            : save.kind === "clarification"
              ? await client.decideClarification(save.payload, save.key)
              : await client.reviewContribution(save.payload, save.key)
      if (!mounted.current || generation.current !== requestGeneration) return
      if (
        save.kind === "clarification" &&
        (receipt.basis.original_response_count !==
          save.basis.original_response_count ||
          receipt.basis.event_sequence !==
            save.basis.event_sequence +
              Number(["confirm", "edit"].includes(save.payload.action)))
      )
        throw new VisitPrepPatientFailure("temporary")
      onStatus(
        receipt.current_answers_complete,
        receipt.current_answers_reviewed,
      )
      pending.current = null
      refreshRequired.current = true
      setEditing(null)
      setExplaining(null)
      setDraft("")
      setProposal(null)
      setEditingProposal(false)
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

  function saveExplanation(withdraw = false) {
    if (
      busyRef.current ||
      pending.current ||
      !current ||
      !confirmation.current ||
      !explaining
    )
      return
    const words = draft.normalize("NFC").replace(/\r\n?/g, "\n").trim()
    if (
      !withdraw &&
      (!words ||
        words.length > 2000 ||
        /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}]/u.test(words.replace(/\n/g, "")))
    ) {
      setError("invalid")
      return
    }
    try {
      pending.current = {
        kind: "explanation",
        key: crypto.randomUUID(),
        payload: {
          ...confirmation.current,
          basis: current.basis,
          question_key: explaining,
          patient_words: withdraw ? null : words,
        },
      }
      retrySave()
    } catch {
      setError("temporary")
    }
  }

  function readProposal(questionKey: string) {
    const confirmed = confirmation.current
    const snapshot = current
    if (!confirmed || !snapshot || pending.current || busyRef.current) return
    const requestGeneration = generation.current
    setProposal(null)
    setFollowup(null)
    setEditingProposal(false)
    setDraft("")
    void run(async () => {
      const view = await client.readClarification(confirmed, questionKey)
      if (!mounted.current || generation.current !== requestGeneration) return
      if (
        view.basis.original_response_count !==
          snapshot.basis.original_response_count ||
        view.basis.event_sequence !== snapshot.basis.event_sequence ||
        (view.status === "available" &&
          view.current_words !==
            snapshot.topics.find((t) => t.question.key === questionKey)?.answer)
      )
        throw new VisitPrepPatientFailure("conflict")
      setProposal(view)
    })
  }

  function requestHelp(questionKey: string, sourceTurnId?: string | null) {
    if (!confirmation.current || !current || pending.current || busyRef.current)
      return
    try {
      pending.current = {
        kind: "generate",
        key: crypto.randomUUID(),
        payload: {
          ...confirmation.current,
          question_key: questionKey,
          basis: current.basis,
          source_turn_id: sourceTurnId ?? null,
        },
      }
      setDraft("")
      retrySave()
    } catch {
      setError("temporary")
    }
  }

  async function loadFollowup(
    confirmed: PatientConfirmation,
    questionKey: string,
    existing?: PatientContribution,
  ) {
    const requestGeneration = generation.current
    const snapshot = existing ?? (await client.readContribution(confirmed))
    if (!mounted.current || generation.current !== requestGeneration) return
    const view = await client.readFollowup(confirmed, questionKey)
    if (!mounted.current || generation.current !== requestGeneration) return
    if (
      view.basis.original_response_count !==
        snapshot.basis.original_response_count ||
      view.basis.event_sequence !== snapshot.basis.event_sequence ||
      (view.status !== "not_available" &&
        view.current_words !==
          snapshot.topics.find((t) => t.question.key === questionKey)?.answer)
    )
      throw new VisitPrepPatientFailure("conflict")
    setCurrent(snapshot)
    refreshRequired.current = false
    onStatus(
      snapshot.current_answers_complete,
      snapshot.current_answers_reviewed,
    )
    setFollowup(view)
    setDraft("")
  }

  function readFollowup(questionKey: string) {
    const confirmed = confirmation.current
    if (!confirmed || !current || pending.current || busyRef.current) return
    setProposal(null)
    setFollowup(null)
    setEditing(null)
    setDraft("")
    void run(() => loadFollowup(confirmed, questionKey, current))
  }

  function answerFollowup() {
    if (
      busyRef.current ||
      pending.current ||
      !confirmation.current ||
      followup?.status !== "prompt" ||
      !followup.proposal_id
    )
      return
    try {
      const words = draft.normalize("NFC").replace(/\r\n?/g, "\n").trim()
      if (
        !words ||
        words.length > 2000 ||
        /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}]/u.test(words.replace(/\n/g, ""))
      ) {
        setError("invalid")
        return
      }
      pending.current = {
        kind: "followup",
        key: crypto.randomUUID(),
        view: followup,
        payload: {
          ...confirmation.current,
          proposal_id: followup.proposal_id,
          patient_words: words,
        },
      }
      retrySave()
    } catch {
      setError("temporary")
    }
  }

  function chooseProposal(action: PatientClarificationChoice["action"]) {
    if (
      busyRef.current ||
      pending.current ||
      !confirmation.current ||
      !proposal?.proposal_id
    )
      return
    try {
      const words =
        action === "edit"
          ? draft.normalize("NFC").replace(/\r\n?/g, "\n").trim()
          : null
      if (
        words !== null &&
        (!words ||
          words.length > 2000 ||
          /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}]/u.test(words.replace(/\n/g, "")))
      ) {
        setError("invalid")
        return
      }
      pending.current = {
        kind: "clarification",
        key: crypto.randomUUID(),
        basis: proposal.basis,
        payload: {
          ...confirmation.current,
          proposal_id: proposal.proposal_id,
          action,
          edited_words: words,
        },
      }
      retrySave()
    } catch {
      setError("temporary")
    }
  }

  const topic = current?.topics.find((row) => row.question.key === editing)
  const explanationTopic = current?.topics.find(
    (row) => row.question.key === explaining,
  )
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
            {continuedRole ? (
              "Your initial confirmation is remembered for this invitation."
            ) : (
              <>
                Before displaying saved answers, enter the recipient first name
                and role again. This check is not identity verification.
              </>
            )}
          </p>
          {continuedRole && !pending.current && !refreshRequired.current ? (
            <Button
              className="mt-6"
              disabled={busy}
              onClick={() => void run(() => load({ role: continuedRole }))}
            >
              {busy ? "Loading…" : "Show saved answers"}
            </Button>
          ) : (
            !continuedRole &&
            !pending.current &&
            !refreshRequired.current && (
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
            )
          )}
        </>
      ) : followup && followup.status !== "not_available" ? (
        <>
          <h1 className="text-2xl font-semibold">Clarify your answer</h1>
          <p className="mt-3">
            This optional follow-up helps organize what you want to share. It is
            not medical advice or a provider review. Saving it retains your
            words separately; it does not replace your current answer.
          </p>
          <h2 className="mt-6 font-semibold">Current answer</h2>
          <p className="mt-2 break-words whitespace-pre-wrap">
            {current.topics
              .find((row) => row.question.key === followup.question_key)
              ?.question.choices.find(
                (choice) => choice.value === followup.current_words,
              )?.label ?? followup.current_words}
          </p>
          {followup.turns.map((turn, index) => (
            <div key={index} className="mt-4">
              <p className="font-semibold">{turn.prompt}</p>
              <p className="mt-2 break-words whitespace-pre-wrap">
                {turn.patient_words}
              </p>
            </div>
          ))}
          {followup.status === "prompt" ? (
            <>
              <label
                htmlFor="followup-answer"
                className="mt-6 block font-semibold"
              >
                {followup.pending_prompt}
              </label>
              <textarea
                id="followup-answer"
                value={draft}
                maxLength={2000}
                disabled={blocked}
                autoComplete="off"
                onChange={(event) => setDraft(event.target.value)}
                className="mt-2 min-h-32 w-full rounded border bg-white p-3 text-base"
              />
              <Button
                className="mt-4"
                disabled={blocked || !draft.trim()}
                onClick={answerFollowup}
              >
                Save follow-up answer
              </Button>
            </>
          ) : (
            <p role="status" className="mt-6">
              Your follow-up answer is saved. No new wording has been accepted.
              You can check for another saved follow-up or review a wording
              proposal.
            </p>
          )}
          <div className="mt-4 flex flex-wrap gap-3">
            <Button
              variant="outline"
              disabled={blocked}
              onClick={() => readFollowup(followup.question_key)}
            >
              Check saved follow-up
            </Button>
            {import.meta.env.VITE_VISIT_PREP_PATIENT_AI_ENABLED === "true" &&
              followup.status === "answered" &&
              followup.source_turn_id && (
                <Button
                  disabled={blocked}
                  onClick={() =>
                    requestHelp(followup.question_key, followup.source_turn_id)
                  }
                >
                  Organize my answer and follow-up
                </Button>
              )}
            <Button
              variant="outline"
              disabled={blocked}
              onClick={() => {
                setFollowup(null)
                setDraft("")
              }}
            >
              Back to saved answers
            </Button>
          </div>
        </>
      ) : proposal?.status === "available" ? (
        <>
          <h1 className="text-2xl font-semibold">Review proposed wording</h1>
          <p className="mt-3">
            This organizes words you supplied, not medical advice or a provider
            review. Accept it only if it reflects what you want to share. Your
            original wording is retained.
          </p>
          {current.topics.find(
            (row) => row.question.key === proposal.question_key,
          )?.question.kind === "choice" && (
            <p className="mt-3">
              This proposal is an explanation alongside your selected answer.
              Accepting or editing it will not change your selected option.
            </p>
          )}
          <h2 className="mt-6 font-semibold">Current answer</h2>
          <p className="mt-2 break-words whitespace-pre-wrap">
            {current.topics
              .find((row) => row.question.key === proposal.question_key)
              ?.question.choices.find(
                (choice) => choice.value === proposal.current_words,
              )?.label ?? proposal.current_words}
          </p>
          {proposal.turns.map((turn, index) => (
            <div key={index} className="mt-4">
              <p className="font-semibold">{turn.prompt}</p>
              <p className="mt-2 break-words whitespace-pre-wrap">
                {turn.patient_words}
              </p>
            </div>
          ))}
          {current.topics.find(
            (row) => row.question.key === proposal.question_key,
          )?.explanation_words && (
            <div className="mt-4">
              <h2 className="font-semibold">Your current explanation</h2>
              <p className="mt-2 break-words whitespace-pre-wrap">
                {
                  current.topics.find(
                    (row) => row.question.key === proposal.question_key,
                  )?.explanation_words
                }
              </p>
            </div>
          )}
          <h2 className="mt-6 font-semibold">Proposed wording</h2>
          <p className="mt-2 break-words whitespace-pre-wrap">
            {proposal.organized_words}
          </p>
          {editingProposal && (
            <>
              <label htmlFor="clarification-edit" className="mt-4 block">
                Your wording to share
              </label>
              <textarea
                id="clarification-edit"
                value={draft}
                maxLength={2000}
                disabled={blocked}
                autoComplete="off"
                onChange={(e) => setDraft(e.target.value)}
                className="mt-2 min-h-32 w-full rounded border bg-white p-3 text-base"
              />
            </>
          )}
          <div className="mt-6 flex flex-wrap gap-3">
            <Button
              disabled={blocked || (editingProposal && !draft.trim())}
              onClick={() =>
                chooseProposal(editingProposal ? "edit" : "confirm")
              }
            >
              {editingProposal
                ? "Save my edited wording"
                : "Accept proposed wording"}
            </Button>
            <Button
              variant="outline"
              disabled={blocked}
              onClick={() => {
                setEditingProposal(true)
                setDraft(proposal.organized_words ?? "")
              }}
            >
              Edit proposed wording
            </Button>
            <Button
              variant="outline"
              disabled={blocked}
              onClick={() => chooseProposal("keep_original")}
            >
              {current.topics.find(
                (row) => row.question.key === proposal.question_key,
              )?.question.kind === "choice"
                ? "Keep my current explanation"
                : "Keep my current answer"}
            </Button>
            <Button
              variant="ghost"
              disabled={blocked}
              onClick={() => chooseProposal("dismiss")}
            >
              Dismiss proposal
            </Button>
          </div>
        </>
      ) : explanationTopic ? (
        <>
          <h1 className="text-2xl font-semibold">
            Explain your selected answer
          </h1>
          <h2 className="mt-4 font-semibold">
            {explanationTopic.question.prompt}
          </h2>
          <p className="mt-2">
            {wording(
              explanationTopic.response_kind,
              explanationTopic.answer,
              explanationTopic,
            )}
          </p>
          <p className="mt-4">
            Your explanation is saved separately for this selected answer. It
            does not replace your choice. Changing the answer will require a new
            explanation.
          </p>
          <label
            htmlFor="choice-explanation"
            className="mt-6 block font-semibold"
          >
            What would you like your provider to understand?
          </label>
          <textarea
            id="choice-explanation"
            value={draft}
            maxLength={2000}
            disabled={blocked}
            autoComplete="off"
            className="mt-2 min-h-36 w-full rounded border bg-white p-3 text-base"
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="mt-4 flex flex-wrap gap-3">
            <Button
              disabled={blocked || !draft.trim()}
              onClick={() => saveExplanation()}
            >
              Save my explanation
            </Button>
            {explanationTopic.explanation_words && (
              <Button
                variant="outline"
                disabled={blocked}
                onClick={() => saveExplanation(true)}
              >
                Withdraw my explanation
              </Button>
            )}
            <Button
              variant="ghost"
              disabled={blocked}
              onClick={() => {
                setExplaining(null)
                setDraft("")
              }}
            >
              Cancel explanation
            </Button>
          </div>
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
            {proposal?.status === "not_available" && (
              <p role="status">
                No current wording proposal is available for this topic. You can
                still change your answer yourself.
              </p>
            )}
            {followup?.status === "not_available" && (
              <p role="status">
                No current follow-up is available. You can still change your
                answer or review a saved wording proposal.
              </p>
            )}
            {current.topics.map((row) => (
              <article
                key={row.question.key}
                className="min-w-0 rounded-lg border border-[var(--brand-rule)] p-4"
              >
                <h2 className="text-lg font-semibold">{row.question.prompt}</h2>
                <p className="mt-3 break-words whitespace-pre-wrap">
                  {wording(row.response_kind, row.answer, row)}
                </p>
                {row.explanation_words && (
                  <div className="mt-3 text-sm">
                    <h3 className="font-semibold">
                      Your explanation for this selected answer
                    </h3>
                    <p className="mt-2 break-words whitespace-pre-wrap">
                      {row.explanation_words}
                    </p>
                  </div>
                )}
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
                    setExplaining(null)
                    setFollowup(null)
                    setProposal(null)
                    setDraft(row.answer ?? "")
                    setError(null)
                  }}
                >
                  {row.response_kind === null
                    ? "Answer topic"
                    : "Change response"}
                </Button>
                {row.response_kind === "answered" &&
                  row.question.kind === "choice" && (
                    <Button
                      variant="outline"
                      className="mt-4"
                      disabled={blocked}
                      aria-label={`Explain: ${row.question.prompt}`}
                      onClick={() => {
                        setEditing(null)
                        setExplaining(row.question.key)
                        setFollowup(null)
                        setProposal(null)
                        setDraft(row.explanation_words ?? "")
                        setError(null)
                      }}
                    >
                      {row.explanation_words
                        ? "Edit explanation"
                        : "Explain my answer"}
                    </Button>
                  )}
                {row.response_kind === "answered" && (
                  <Button
                    variant="ghost"
                    className="mt-4"
                    disabled={blocked}
                    aria-label={`Check follow-up: ${row.question.prompt}`}
                    onClick={() => readFollowup(row.question.key)}
                  >
                    Check follow-up
                  </Button>
                )}
                {import.meta.env.VITE_VISIT_PREP_PATIENT_AI_ENABLED ===
                  "true" &&
                  row.response_kind === "answered" && (
                    <Button
                      variant="outline"
                      className="mt-4"
                      disabled={blocked}
                      aria-label={`Help me clarify: ${row.question.prompt}`}
                      onClick={() => requestHelp(row.question.key)}
                    >
                      Help me clarify this answer
                    </Button>
                  )}
                {row.response_kind === "answered" && (
                  <Button
                    variant="ghost"
                    className="mt-4"
                    disabled={blocked}
                    aria-label={`Review wording proposal: ${row.question.prompt}`}
                    onClick={() => readProposal(row.question.key)}
                  >
                    Review wording proposal
                  </Button>
                )}
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
      {!busy && pending.current?.kind === "generate" && (
        <Button
          variant="outline"
          className="mt-4"
          onClick={() => {
            pending.current = null
            setProposal(null)
            setFollowup(null)
            setError(null)
            const confirmed = confirmation.current
            if (confirmed) void run(() => load(confirmed))
          }}
        >
          Continue with my saved answers
        </Button>
      )}
      {!busy && pending.current?.kind === "followup" && (
        <Button
          variant="outline"
          className="mt-4"
          onClick={() => {
            const save = pending.current
            const confirmed = confirmation.current
            if (save?.kind !== "followup" || !confirmed) return
            pending.current = null
            refreshRequired.current = true
            setCurrent(null)
            setFollowup(null)
            setDraft("")
            void run(async () => {
              await loadFollowup(confirmed, save.view.question_key)
            })
          }}
        >
          Check whether follow-up saved
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
      {!busy &&
        (pending.current?.kind === "change" ||
          pending.current?.kind === "explanation") &&
        error === "invalid" && (
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
