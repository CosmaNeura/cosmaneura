import { useEffect, useId, useRef } from "react"

import { Button } from "~/components/ui/button"
import type { PatientInterviewQuestion } from "~/lib/visit-prep-patient-api"

/** One physician topic per screen; adapted from 0d92405fbe1b, not legacy Intake. */
export function QuestionForm({
  question,
  draft,
  onDraftChange,
  onSubmit,
  busy,
}: {
  question: PatientInterviewQuestion
  draft: string
  onDraftChange: (value: string) => void
  onSubmit: (answer: string | null) => void
  busy: boolean
}) {
  const fieldId = useId()
  const textarea = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (question.kind !== "choice") textarea.current?.focus()
  }, [question.key, question.kind])

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        if (!busy && draft.trim()) onSubmit(draft)
      }}
    >
      <p className="text-sm text-[var(--brand-muted-text)]">
        Topic {question.position} of 5
      </p>
      <h1 className="mt-6 text-2xl leading-snug font-semibold md:text-3xl">
        {question.prompt}
      </h1>
      {question.helper_text && (
        <p
          id={`${fieldId}-helper`}
          className="mt-3 text-[var(--brand-muted-text)]"
        >
          {question.helper_text}
        </p>
      )}
      {question.kind === "choice" ? (
        <div className="mt-6 grid gap-3">
          {question.choices.map((choice) => (
            <button
              key={choice.value}
              type="button"
              disabled={busy}
              onClick={() => onSubmit(choice.value)}
              className="min-h-14 rounded-lg border border-[var(--brand-rule)] bg-white px-4 py-4 text-left focus-visible:ring-2 disabled:opacity-50"
            >
              <span className="block font-medium">{choice.label}</span>
              {choice.hint && (
                <span className="mt-1 block text-sm text-[var(--brand-muted-text)]">
                  {choice.hint}
                </span>
              )}
            </button>
          ))}
        </div>
      ) : (
        <>
          <label htmlFor={fieldId} className="sr-only">
            {question.prompt}
          </label>
          <textarea
            id={fieldId}
            ref={textarea}
            rows={7}
            value={draft}
            disabled={busy}
            maxLength={2_000}
            aria-describedby={
              question.helper_text ? `${fieldId}-helper` : undefined
            }
            autoComplete="off"
            name="visit-prep-answer"
            onChange={(event) => onDraftChange(event.target.value)}
            placeholder="Write the way you would say it out loud."
            className="mt-6 w-full resize-y rounded-lg border border-[var(--brand-rule)] bg-white px-4 py-4 text-base leading-relaxed focus-visible:ring-2 disabled:opacity-50"
          />
          <p className="mt-2 text-right text-xs text-[var(--brand-muted-text)]">
            {draft.length} / 2,000 characters
          </p>
        </>
      )}
      <div className="mt-6 flex justify-between gap-3">
        <Button
          type="button"
          variant="ghost"
          disabled={busy}
          onClick={() => onSubmit(null)}
        >
          Skip this topic
        </Button>
        {question.kind !== "choice" && (
          <Button type="submit" disabled={busy || !draft.trim()}>
            {busy ? "Saving…" : "Save and continue"}
          </Button>
        )}
      </div>
    </form>
  )
}
