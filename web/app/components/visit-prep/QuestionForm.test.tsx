import { describe, expect, it, vi } from "vitest"

import { type InterviewQuestion, QuestionForm } from "./QuestionForm"
import { fireEvent, render, screen } from "@testing-library/react"

const STORY: InterviewQuestion = {
  question: "What is the main reason for this visit?",
  helper: "In your own words.",
  kind: "story",
}

const CHOICE: InterviewQuestion = {
  question: "Are you taking them as prescribed?",
  helper: "There is no wrong answer.",
  kind: "choice",
  choices: [
    {
      value: "yes",
      label: "Yes, as prescribed",
      hint: "Most days, as written",
    },
    { value: "no", label: "Not really", hint: "Something got in the way" },
  ],
}

function renderForm(question: InterviewQuestion, overrides = {}) {
  const onSubmit = vi.fn()
  const onDraftChange = vi.fn()
  render(
    <QuestionForm
      question={question}
      progress={{ answered: 2, total: 5 }}
      draft=""
      onDraftChange={onDraftChange}
      onSubmit={onSubmit}
      submitting={false}
      {...overrides}
    />,
  )
  return { onSubmit, onDraftChange }
}

describe("QuestionForm", () => {
  it("shows the question and its helper text", () => {
    renderForm(STORY)

    expect(
      screen.getByRole("heading", { name: STORY.question }),
    ).toBeInTheDocument()
    expect(screen.getByText("In your own words.")).toBeInTheDocument()
  })

  it("renders a text field for prose questions", () => {
    renderForm(STORY)

    expect(screen.getByRole("textbox")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument()
  })

  it("renders tap targets instead of a text field for choice questions", () => {
    renderForm(CHOICE)

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /Yes, as prescribed/ }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /Not really/ }),
    ).toBeInTheDocument()
    // Nothing to press to move on -- picking an answer *is* moving on.
    expect(
      screen.queryByRole("button", { name: "Continue" }),
    ).not.toBeInTheDocument()
  })

  it("submits the stored value when a choice is tapped, not its label", () => {
    const { onSubmit } = renderForm(CHOICE)

    fireEvent.click(screen.getByRole("button", { name: /Not really/ }))

    expect(onSubmit).toHaveBeenCalledWith("no")
  })

  it("submits the draft when Continue is pressed", () => {
    const { onSubmit } = renderForm(STORY, { draft: "Blood pressure" })

    fireEvent.click(screen.getByRole("button", { name: "Continue" }))

    expect(onSubmit).toHaveBeenCalledWith("Blood pressure")
  })

  // The API treats an empty answer as a deliberate skip and advances past it.
  // Sending anything else here (a sentinel like "(skipped)") would leak into
  // the brief the physician reads.
  it("submits an empty string when Skip is pressed", () => {
    const { onSubmit } = renderForm(STORY, { draft: "half a thought" })

    fireEvent.click(screen.getByRole("button", { name: "Skip" }))

    expect(onSubmit).toHaveBeenCalledWith("")
  })

  it("offers Skip on choice questions too", () => {
    const { onSubmit } = renderForm(CHOICE)

    fireEvent.click(screen.getByRole("button", { name: "Skip" }))

    expect(onSubmit).toHaveBeenCalledWith("")
  })

  it("counts the question the patient is on, not the one they finished", () => {
    renderForm(STORY)

    expect(screen.getByText("Question 3 of 5")).toBeInTheDocument()
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "40",
    )
  })

  it("never counts past the total when the last question is open", () => {
    renderForm(STORY, { progress: { answered: 5, total: 5 } })

    expect(screen.getByText("Question 5 of 5")).toBeInTheDocument()
  })

  it("omits the counter when there is no progress to report", () => {
    renderForm(STORY, { progress: null })

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument()
    expect(screen.queryByText(/^Question /)).not.toBeInTheDocument()
  })

  it("announces a failure to save", () => {
    renderForm(STORY, { error: "That did not save." })

    expect(screen.getByRole("alert")).toHaveTextContent("That did not save.")
  })

  it("locks the controls while saving", () => {
    renderForm(STORY, { submitting: true })

    expect(screen.getByRole("textbox")).toBeDisabled()
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Skip" })).toBeDisabled()
  })

  it("locks the tap targets while saving", () => {
    renderForm(CHOICE, { submitting: true })

    expect(
      screen.getByRole("button", { name: /Yes, as prescribed/ }),
    ).toBeDisabled()
  })

  it("reports typing back to the caller so a draft survives a failed save", () => {
    const { onDraftChange } = renderForm(STORY)

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "four months of exhaustion" },
    })

    expect(onDraftChange).toHaveBeenCalledWith("four months of exhaustion")
  })

  it("labels the text field for screen readers", () => {
    renderForm(STORY)

    expect(
      screen.getByRole("textbox", { name: STORY.question }),
    ).toBeInTheDocument()
  })
})
