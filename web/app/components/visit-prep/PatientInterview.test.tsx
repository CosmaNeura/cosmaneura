import { StrictMode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  captureVisitPrepCredential,
  forgetVisitPrepCredential,
} from "~/lib/visit-prep-browser"
import {
  type PatientInterviewState,
  VisitPrepPatientFailure,
} from "~/lib/visit-prep-patient-api"

import { PatientInterview } from "./PatientInterview"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

const token = `vp1_${"A".repeat(43)}`
const invited: PatientInterviewState = {
  status: "confirmation_required",
  physician_display_name: null,
  practice_display_name: null,
  scheduled_at: null,
  timezone_name: null,
  submitted_questions: 0,
  maximum_questions: 5,
  current_question: null,
}
function state(
  key: string,
  position: number,
  kind: "story" | "list" | "choice" = "story",
): PatientInterviewState {
  return {
    ...invited,
    status: "in_progress",
    physician_display_name: "Dr. Synthetic",
    practice_display_name: "Synthetic Practice",
    scheduled_at: "2099-01-15T15:00:00Z",
    timezone_name: "UTC",
    submitted_questions: position - 1,
    current_question: {
      key,
      position,
      prompt: key === "story" ? "Tell me your story" : `Synthetic ${key}`,
      helper_text: null,
      kind,
      choices:
        kind === "choice"
          ? [{ value: "some", label: "Some of the time", hint: null }]
          : [],
    },
  }
}
function setup(initial = invited) {
  const client = {
    resume: vi.fn().mockResolvedValue(initial),
    confirm: vi.fn().mockResolvedValue(state("reason", 1)),
    submit: vi.fn(),
    complete: vi.fn(),
    reportWrongRecipient: vi.fn().mockResolvedValue(undefined),
    readContribution: vi.fn(),
    correctContribution: vi.fn(),
    reviewContribution: vi.fn(),
    readClarification: vi.fn(),
    decideClarification: vi.fn(),
    readEmailConsent: vi.fn(),
    decideEmailConsent: vi.fn(),
    dispose: vi.fn(),
  }
  const makeClient = vi.fn(() => client)
  return { client, makeClient }
}
beforeEach(() => {
  window.history.replaceState(null, "", `/visit-prep#access_token=${token}`)
  captureVisitPrepCredential(window)
  vi.stubEnv("VITE_PYTHON_URL", "https://api.example.test")
})
afterEach(() => {
  forgetVisitPrepCredential()
  vi.unstubAllEnvs()
})

describe("patient journey", () => {
  it("opens optional email choices explicitly, skips parent focus reload and clears on support-view denial", async () => {
    const { client, makeClient } = setup(state("story", 5))
    render(<PatientInterview makeClient={makeClient} />)
    await screen.findByRole("heading", { name: "Tell me your story" })
    fireEvent.change(screen.getByLabelText("Tell me your story"), {
      target: { value: "Unsaved synthetic story" },
    })
    expect(client.readEmailConsent).not.toHaveBeenCalled()
    fireEvent.click(
      screen.getByRole("button", { name: "Optional email choices" }),
    )
    await screen.findByRole("heading", { name: "Optional email choices" })
    fireEvent(window, new Event("focus"))
    expect(client.resume).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole("button", { name: "Back to preparation" }))
    await screen.findByRole("heading", { name: "Tell me your story" })
    expect(screen.getByLabelText("Tell me your story")).toHaveValue(
      "Unsaved synthetic story",
    )
    expect(client.decideEmailConsent).not.toHaveBeenCalled()
    fireEvent.click(
      screen.getByRole("button", { name: "Optional email choices" }),
    )
    fireEvent.change(screen.getByLabelText("Recipient first name"), {
      target: { value: "José" },
    })
    await act(async () =>
      window.dispatchEvent(new Event("cosmaneura:view-as-hard-block")),
    )
    expect(
      screen.queryByRole("heading", { name: "Optional email choices" }),
    ).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue("José")).not.toBeInTheDocument()
    expect(client.dispose).toHaveBeenCalledOnce()
  })
  it("mounts review only after an explicit choice and clears saved answers on support-view denial", async () => {
    const { client, makeClient } = setup({
      ...invited,
      status: "completed",
      submitted_questions: 4,
    })
    client.readContribution.mockResolvedValue({
      basis: { original_response_count: 4, event_sequence: 0 },
      actor_role: "patient",
      original_interview_completed: true,
      current_answers_complete: true,
      current_answers_reviewed: false,
      topics: [
        {
          question: state("story", 5).current_question,
          response_kind: "answered",
          answer: "Private saved invented story.",
          initial_response_kind: "answered",
          initial_answer: "Private saved invented story.",
          changed: false,
          recorded_at: "2099-01-01T12:00:00Z",
        },
      ],
    })
    render(<PatientInterview makeClient={makeClient} />)
    await screen.findByRole("heading", { name: "Visit Prep complete" })
    expect(client.readContribution).not.toHaveBeenCalled()
    fireEvent.click(
      screen.getByRole("button", { name: "Review or change saved answers" }),
    )
    fireEvent.change(screen.getByLabelText("Recipient first name"), {
      target: { value: "José" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Show saved answers" }))
    await screen.findByText("Private saved invented story.")
    await act(async () =>
      window.dispatchEvent(new Event("cosmaneura:view-as-hard-block")),
    )
    await screen.findByRole("alert")
    expect(
      screen.queryByText("Private saved invented story."),
    ).not.toBeInTheDocument()
    expect(client.dispose).toHaveBeenCalledOnce()
  })

  it("clears its draft when support-view state changes, ignoring a late response", async () => {
    const { client, makeClient } = setup(state("story", 5))
    let resolve!: (value: PatientInterviewState) => void
    client.submit.mockImplementation(
      () =>
        new Promise<PatientInterviewState>((done) => {
          resolve = done
        }),
    )
    render(<PatientInterview makeClient={makeClient} />)
    await screen.findByRole("heading", { name: "Tell me your story" })
    fireEvent.change(screen.getByLabelText("Tell me your story"), {
      target: { value: "Must disappear" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }))
    await act(async () => {
      window.dispatchEvent(new Event("cosmaneura:view-as-hard-block"))
    })
    await screen.findByRole("alert")
    await act(async () => {
      resolve(state("reason", 1))
    })
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    expect(screen.queryByText("Must disappear")).not.toBeInTheDocument()
    expect(screen.queryByText("Synthetic reason")).not.toBeInTheDocument()
  })

  it("does not discard an unsaved draft when focus rechecks the same topic", async () => {
    const { client, makeClient } = setup(state("story", 5))
    render(<PatientInterview makeClient={makeClient} />)
    await screen.findByRole("heading", { name: "Tell me your story" })
    fireEvent.change(screen.getByLabelText("Tell me your story"), {
      target: { value: "Retain on focus" },
    })
    fireEvent(window, new Event("focus"))
    await waitFor(() => expect(client.resume).toHaveBeenCalledTimes(2))
    expect(screen.getByLabelText("Tell me your story")).toHaveValue(
      "Retain on focus",
    )
  })
  it("completes five physician topics, including unconditional free-form story", async () => {
    const { client, makeClient } = setup()
    client.submit
      .mockResolvedValueOnce(state("medications", 2, "list"))
      .mockResolvedValueOnce(state("adherence", 3, "choice"))
      .mockResolvedValueOnce(state("adherence_barrier", 4))
      .mockResolvedValueOnce(state("story", 5))
      .mockResolvedValueOnce({
        ...state("story", 5),
        status: "ready_to_complete",
        current_question: null,
        submitted_questions: 5,
      })
    client.complete.mockResolvedValue({
      ...invited,
      status: "completed",
      submitted_questions: 5,
    })
    render(<PatientInterview makeClient={makeClient} />)
    await screen.findByRole("heading", { name: "Before we begin" })
    expect(screen.queryByText("Dr. Synthetic")).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Recipient first name"), {
      target: { value: "José" },
    })
    fireEvent.change(screen.getByLabelText("This invitation was sent to"), {
      target: { value: "caregiver" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Begin Visit Prep" }))
    await screen.findByRole("heading", { name: "Synthetic reason" })
    expect(client.confirm).toHaveBeenCalledWith("José", "caregiver")
    for (const [prompt, answer] of [
      ["Synthetic reason", "My concern"],
      ["Synthetic medications", "My medications"],
    ]) {
      fireEvent.change(screen.getByLabelText(prompt), {
        target: { value: answer },
      })
      fireEvent.click(screen.getByRole("button", { name: "Save and continue" }))
      await waitFor(() =>
        expect(
          screen.queryByRole("heading", { name: prompt }),
        ).not.toBeInTheDocument(),
      )
    }
    fireEvent.click(screen.getByRole("button", { name: "Some of the time" }))
    await screen.findByRole("heading", { name: "Synthetic adherence_barrier" })
    fireEvent.click(screen.getByRole("button", { name: "Skip this topic" }))
    await screen.findByRole("heading", { name: "Tell me your story" })
    fireEvent.change(screen.getByLabelText("Tell me your story"), {
      target: { value: "My own original story" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }))
    await screen.findByRole("heading", { name: "Your topics are saved" })
    expect(client.submit.mock.calls[3][0]).toEqual({
      question_key: "adherence_barrier",
      response_kind: "skipped",
      answer: null,
    })
    expect(client.submit.mock.calls[4][0].answer).toBe("My own original story")
    fireEvent.click(screen.getByRole("button", { name: "Finish Visit Prep" }))
    await screen.findByRole("heading", { name: "Visit Prep complete" })
    expect(
      screen.getByText(/does not mean a provider has reviewed/),
    ).toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain(token)
    expect(window.location.hash).toBe("")
  })

  it("keeps an uncertain draft and retries the same exact save", async () => {
    const { client, makeClient } = setup(state("story", 5))
    client.submit
      .mockRejectedValueOnce(new VisitPrepPatientFailure("temporary"))
      .mockResolvedValueOnce({
        ...invited,
        status: "ready_to_complete",
        submitted_questions: 5,
      })
    render(<PatientInterview makeClient={makeClient} />)
    await screen.findByRole("heading", { name: "Tell me your story" })
    fireEvent.change(screen.getByLabelText("Tell me your story"), {
      target: { value: "A draft that must survive" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }))
    await screen.findByRole("button", { name: "Retry same save" })
    expect(screen.getByLabelText("Tell me your story")).toHaveValue(
      "A draft that must survive",
    )
    expect(screen.getByLabelText("Tell me your story")).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Retry same save" }))
    await screen.findByRole("heading", { name: "Your topics are saved" })
    expect(client.submit.mock.calls[0]).toEqual(client.submit.mock.calls[1])
  })

  it("reports misdelivery before confirmation and clears all displayed state", async () => {
    const { client, makeClient } = setup()
    render(<PatientInterview makeClient={makeClient} />)
    await screen.findByRole("heading", { name: "Before we begin" })
    fireEvent.change(screen.getByLabelText("Recipient first name"), {
      target: { value: "Wrong recipient" },
    })
    fireEvent.click(
      screen.getByRole("button", { name: /sent to the wrong person/ }),
    )
    await screen.findByRole("heading", {
      name: "Thank you for letting us know",
    })
    expect(client.reportWrongRecipient).toHaveBeenCalledOnce()
    expect(client.confirm).not.toHaveBeenCalled()
    expect(client.dispose).toHaveBeenCalled()
    expect(document.body.innerHTML).not.toContain("Wrong recipient")
  })

  it("discards answers and identity if the current credential is denied", async () => {
    const { client, makeClient } = setup(state("story", 5))
    client.submit.mockRejectedValue(new VisitPrepPatientFailure("unavailable"))
    render(<PatientInterview makeClient={makeClient} />)
    await screen.findByRole("heading", { name: "Tell me your story" })
    fireEvent.change(screen.getByLabelText("Tell me your story"), {
      target: { value: "Discarded synthetic content" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }))
    await screen.findByRole("alert")
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain("Discarded synthetic content")
    expect(document.body.innerHTML).not.toContain("Dr. Synthetic")
  })

  it("rejects missing tokens before making any request", async () => {
    forgetVisitPrepCredential()
    const { makeClient } = setup()
    render(<PatientInterview makeClient={makeClient} />)
    await screen.findByRole("alert")
    expect(makeClient).not.toHaveBeenCalled()
  })

  it("survives StrictMode without accepting the disposed initialization", async () => {
    const { makeClient, client } = setup(state("story", 5))
    render(
      <StrictMode>
        <PatientInterview makeClient={makeClient} />
      </StrictMode>,
    )
    await screen.findByRole("heading", { name: "Tell me your story" })
    expect(makeClient).toHaveBeenCalledTimes(2)
    expect(client.dispose).toHaveBeenCalledTimes(1)
  })
})
