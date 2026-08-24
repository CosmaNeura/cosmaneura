import { describe, expect, it } from "vitest"

import type { IntakeSchemaQuestion } from "~/configuration/client"

import { AnswerList, answeredQuestions, prepStatusFor } from "./AnswerList"
import { render, screen } from "@testing-library/react"

function question(
  overrides: Partial<IntakeSchemaQuestion> & {
    order: number
    question: string
  },
): IntakeSchemaQuestion {
  return { required_details: "", ...overrides }
}

describe("answeredQuestions", () => {
  it("keeps only what the patient has responded to", () => {
    const answers = answeredQuestions([
      question({
        order: 1,
        question: "Why?",
        answer: "Exhausted",
        answer_timestamp: 1,
      }),
      question({ order: 2, question: "Meds?" }),
    ])

    expect(answers).toEqual([
      { question: "Why?", answer: "Exhausted", skipped: false },
    ])
  })

  it("returns them in the order they were asked, not stored", () => {
    const answers = answeredQuestions([
      question({
        order: 3,
        question: "Third",
        answer: "c",
        answer_timestamp: 1,
      }),
      question({
        order: 1,
        question: "First",
        answer: "a",
        answer_timestamp: 1,
      }),
      question({
        order: 2,
        question: "Second",
        answer: "b",
        answer_timestamp: 1,
      }),
    ])

    expect(answers.map((a) => a.question)).toEqual(["First", "Second", "Third"])
  })

  it("does not mutate the array it was given", () => {
    const questions = [
      question({
        order: 2,
        question: "Second",
        answer: "b",
        answer_timestamp: 1,
      }),
      question({
        order: 1,
        question: "First",
        answer: "a",
        answer_timestamp: 1,
      }),
    ]

    answeredQuestions(questions)

    expect(questions.map((q) => q.order)).toEqual([2, 1])
  })

  // A skip stores an empty string. Truthiness on the answer alone would drop it,
  // and the physician would never know the patient was asked.
  it("treats an empty answer as a skip rather than as unanswered", () => {
    const answers = answeredQuestions([
      question({ order: 1, question: "Why?", answer: "", answer_timestamp: 1 }),
    ])

    expect(answers).toEqual([{ question: "Why?", answer: "", skipped: true }])
  })

  it("treats a whitespace-only answer as a skip", () => {
    const answers = answeredQuestions([
      question({
        order: 1,
        question: "Why?",
        answer: "   ",
        answer_timestamp: 1,
      }),
    ])

    expect(answers[0].skipped).toBe(true)
  })

  // Mirrors the backend's either-field tolerance for older rows and backfills.
  it("counts an answer with no timestamp", () => {
    const answers = answeredQuestions([
      question({ order: 1, question: "Why?", answer: "Exhausted" }),
    ])

    expect(answers).toHaveLength(1)
  })

  it("counts a timestamp with no answer as a skip", () => {
    const answers = answeredQuestions([
      question({ order: 1, question: "Why?", answer_timestamp: 1 }),
    ])

    expect(answers[0].skipped).toBe(true)
  })

  it("is empty for a prep nobody has opened", () => {
    expect(
      answeredQuestions([
        question({ order: 1, question: "Why?" }),
        question({ order: 2, question: "Meds?" }),
      ]),
    ).toEqual([])
  })
})

describe("AnswerList", () => {
  it("shows each question with what the patient said", () => {
    render(
      <AnswerList
        answers={[
          {
            question: "Why are you here?",
            answer: "Exhausted",
            skipped: false,
          },
        ]}
      />,
    )

    expect(screen.getByText("Why are you here?")).toBeInTheDocument()
    expect(screen.getByText("Exhausted")).toBeInTheDocument()
  })

  it("marks a skipped question as skipped rather than showing nothing", () => {
    render(
      <AnswerList
        answers={[{ question: "Why are you here?", answer: "", skipped: true }]}
      />,
    )

    expect(screen.getByText("Why are you here?")).toBeInTheDocument()
    expect(screen.getByText("Skipped")).toBeInTheDocument()
  })

  it("preserves the line breaks a patient typed", () => {
    render(
      <AnswerList
        answers={[{ question: "Meds?", answer: "One\nTwo", skipped: false }]}
      />,
    )

    // getByText collapses whitespace by default, which would hide the very
    // thing under test.
    expect(
      screen.getByText("One\nTwo", { collapseWhitespace: false }),
    ).toHaveClass("whitespace-pre-wrap")
  })

  it("renders nothing at all when there is nothing to show", () => {
    const { container } = render(<AnswerList answers={[]} />)

    expect(container).toBeEmptyDOMElement()
  })
})

describe("prepStatusFor", () => {
  const answered = [{ question: "Why?", answer: "Exhausted", skipped: false }]
  const skipped = [{ question: "Why?", answer: "", skipped: true }]

  it("is not-started when the patient has answered nothing", () => {
    expect(prepStatusFor([], false)).toBe("not-started")
  })

  // The state that did not exist before: the page used to render these as
  // not-started, with an empty body, discarding answers it already had.
  it("is in-progress once anything has been answered", () => {
    expect(prepStatusFor(answered, false)).toBe("in-progress")
  })

  it("is in-progress even when the only response was a skip", () => {
    expect(prepStatusFor(skipped, false)).toBe("in-progress")
  })

  it("is completed once the brief exists", () => {
    expect(prepStatusFor(answered, true)).toBe("completed")
  })

  it("prefers completed over in-progress", () => {
    expect(prepStatusFor(skipped, true)).toBe("completed")
  })
})
