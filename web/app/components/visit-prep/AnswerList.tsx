import type { IntakeSchemaQuestion } from "~/configuration/client"

export type PrepAnswer = {
  question: string
  answer: string
  skipped: boolean
}

/**
 * Whether the patient has dealt with a question, skips included.
 *
 * Mirrors `IntakeSchemaQuestion.is_answered` on the backend, deliberately
 * including the same either-field tolerance: a skip stores an empty string, and
 * truthiness on the answer alone would hide it.
 */
function isAnswered(question: IntakeSchemaQuestion): boolean {
  return (
    (question.answer ?? null) !== null ||
    (question.answer_timestamp ?? null) !== null
  )
}

/**
 * Everything the patient has responded to, in the order they were asked.
 *
 * Questions they have not reached yet are left out entirely -- an unasked
 * question is not a blank answer, and showing it as one reads like the patient
 * declined.
 */
export function answeredQuestions(
  questions: IntakeSchemaQuestion[],
): PrepAnswer[] {
  return [...questions]
    .filter(isAnswered)
    .sort((left, right) => left.order - right.order)
    .map((question) => {
      const answer = (question.answer ?? "").trim()
      return {
        question: question.question,
        answer,
        skipped: answer.length === 0,
      }
    })
}

export type PrepStatus = "not-started" | "in-progress" | "completed"

/**
 * Which of the three states a prep is in.
 *
 * The middle one is the point. A prep the patient started and walked away from
 * used to collapse into "not-started" with an empty body, even though every
 * answer they had typed was already on the record.
 */
export function prepStatusFor(
  answers: PrepAnswer[],
  hasBrief: boolean,
): PrepStatus {
  if (hasBrief) return "completed"
  return answers.length > 0 ? "in-progress" : "not-started"
}

/**
 * What the patient has said so far.
 *
 * Rendered whether or not the brief exists. Before it does, this is the only
 * thing on the page -- a prep someone abandoned halfway through still holds
 * whatever they typed, and that used to render as an empty page.
 */
export function AnswerList({ answers }: { answers: PrepAnswer[] }) {
  if (answers.length === 0) return null

  return (
    <dl className="space-y-5">
      {answers.map((answer) => (
        <div key={answer.question}>
          <dt className="text-muted-foreground text-xs tracking-wide uppercase">
            {answer.question}
          </dt>
          <dd className="mt-1 text-sm">
            {answer.skipped ? (
              <span className="text-muted-foreground italic">Skipped</span>
            ) : (
              <span className="whitespace-pre-wrap">{answer.answer}</span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  )
}
