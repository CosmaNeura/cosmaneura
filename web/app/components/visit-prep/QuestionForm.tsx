import { useEffect, useId, useRef } from "react"

import { Button } from "@/components/ui/button"
import { cn } from "~/lib/utils"

export type QuestionChoice = {
  value: string
  label: string
  hint?: string | null
}

export type InterviewQuestion = {
  question: string
  helper?: string | null
  kind: "story" | "list" | "choice"
  choices?: QuestionChoice[] | null
}

export type InterviewProgress = {
  answered: number
  total: number
}

/**
 * Keep in step with `MAX_ANSWER_CHARS` in app/routes/chat.py.
 *
 * The server is what enforces this; stopping input here just means a patient
 * never writes past the limit and then loses the submission to a 422.
 */
const MAX_ANSWER_CHARS = 8000

/**
 * One question, one screen.
 *
 * Replaces the assistant-ui chat thread. The endpoint behind it always served
 * exactly one question at a time -- the chat bubbles were presentation, and
 * presenting a physician's five questions as a chatbot is what made the old
 * intake feel like homework (CosmaNeura#135).
 */
export function QuestionForm({
  question,
  progress,
  draft,
  onDraftChange,
  onSubmit,
  submitting,
  error,
}: {
  question: InterviewQuestion
  progress?: InterviewProgress | null
  draft: string
  onDraftChange: (value: string) => void
  /** An empty string is a deliberate skip, which the API stores as such. */
  onSubmit: (answer: string) => void
  submitting: boolean
  error?: string | null
}) {
  const fieldId = useId()
  const helperId = `${fieldId}-helper`
  const textarea = useRef<HTMLTextAreaElement>(null)

  // Move focus to the field whenever the question changes so a patient working
  // through prose questions never has to tap twice. Choice questions are left
  // alone: raising the keyboard over the tap targets would be actively worse.
  useEffect(() => {
    if (question.kind !== "choice") textarea.current?.focus()
  }, [question.question, question.kind])

  const showCounter = Boolean(progress && progress.total > 0)
  const step = progress ? Math.min(progress.answered + 1, progress.total) : 0
  const percent =
    progress && progress.total > 0
      ? Math.round((progress.answered / progress.total) * 100)
      : 0

  return (
    <div>
      {showCounter && progress ? (
        <>
          <p className="text-sm text-[var(--brand-muted-text)] tabular-nums">
            Question {step} of {progress.total}
          </p>
          <div
            className="mt-3 h-1 overflow-hidden rounded-full bg-[var(--brand-rule)]"
            role="progressbar"
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Progress"
          >
            <div
              className="h-full rounded-full bg-[var(--brand-gold)] transition-[width] duration-300"
              style={{ width: `${percent}%` }}
            />
          </div>
        </>
      ) : null}

      <h1 className="mt-8 text-2xl leading-snug font-semibold md:text-3xl">
        {question.question}
      </h1>
      {question.helper ? (
        <p id={helperId} className="mt-3 text-[var(--brand-muted-text)]">
          {question.helper}
        </p>
      ) : null}

      {question.kind === "choice" && question.choices?.length ? (
        <div className="mt-8 grid gap-3">
          {question.choices.map((choice) => (
            <button
              key={choice.value}
              type="button"
              disabled={submitting}
              onClick={() => onSubmit(choice.value)}
              className={cn(
                "min-h-14 rounded-lg border border-[var(--brand-rule)] bg-white px-4 py-4 text-left",
                "transition-colors hover:border-[var(--brand-navy-mid)]",
                "focus-visible:ring-ring/50 focus-visible:ring-[3px] focus-visible:outline-none",
                "disabled:pointer-events-none disabled:opacity-50",
              )}
            >
              <span className="block font-medium">{choice.label}</span>
              {choice.hint ? (
                <span className="mt-1 block text-sm text-[var(--brand-muted-text)]">
                  {choice.hint}
                </span>
              ) : null}
            </button>
          ))}
        </div>
      ) : (
        <>
          <label htmlFor={fieldId} className="sr-only">
            {question.question}
          </label>
          <textarea
            id={fieldId}
            ref={textarea}
            aria-describedby={question.helper ? helperId : undefined}
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            rows={7}
            maxLength={MAX_ANSWER_CHARS}
            disabled={submitting}
            placeholder="Write the way you would say it out loud."
            className={cn(
              "mt-8 w-full resize-y rounded-lg border border-[var(--brand-rule)] bg-white px-4 py-4",
              "text-base leading-relaxed",
              "focus-visible:border-[var(--brand-gold)] focus-visible:outline-none",
              "disabled:opacity-60",
            )}
          />
        </>
      )}

      {error ? (
        <p role="alert" className="text-destructive mt-4 text-sm">
          {error}
        </p>
      ) : null}

      <div className="mt-6 flex items-center justify-between gap-3">
        <Button
          variant="ghost"
          disabled={submitting}
          onClick={() => onSubmit("")}
        >
          Skip
        </Button>
        {question.kind === "choice" ? (
          <span className="text-sm text-[var(--brand-muted-text)]">
            Choose an answer
          </span>
        ) : (
          <Button
            size="lg"
            className="min-w-32"
            disabled={submitting}
            onClick={() => onSubmit(draft)}
          >
            {submitting ? "Saving…" : "Continue"}
          </Button>
        )}
      </div>
    </div>
  )
}
