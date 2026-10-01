import { describe, expect, it, vi } from "vitest"

import {
  type PatientContribution,
  VisitPrepPatientFailure,
} from "~/lib/visit-prep-patient-api"

import { PatientContributionReview } from "./PatientContributionReview"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

const question = (key: string, position: number) => ({
  key,
  position,
  prompt: key === "story" ? "Tell me your story" : `Synthetic ${key}`,
  helper_text: null,
  kind: "story" as const,
  choices: [],
})
const initial: PatientContribution = {
  basis: { original_response_count: 4, event_sequence: 0 },
  actor_role: "caregiver",
  original_interview_completed: true,
  current_answers_complete: true,
  current_answers_reviewed: false,
  topics: [
    {
      question: question("reason", 1),
      response_kind: "answered",
      answer: "Original invented reason.",
      initial_response_kind: "answered",
      initial_answer: "Original invented reason.",
      changed: false,
      recorded_at: "2099-01-01T12:00:00Z",
    },
    {
      question: question("story", 5),
      response_kind: "answered",
      answer: "Original invented story.",
      initial_response_kind: "answered",
      initial_answer: "Original invented story.",
      changed: false,
      recorded_at: "2099-01-01T12:00:00Z",
    },
  ],
}
function setup() {
  const client = {
    resume: vi.fn(),
    confirm: vi.fn(),
    submit: vi.fn(),
    complete: vi.fn(),
    reportWrongRecipient: vi.fn(),
    dispose: vi.fn(),
    readContribution: vi.fn().mockResolvedValue(initial),
    correctContribution: vi.fn(),
    reviewContribution: vi.fn(),
  }
  const props = {
    client,
    onUnavailable: vi.fn(),
    onClose: vi.fn(),
    onStatus: vi.fn(),
  }
  render(<PatientContributionReview {...props} />)
  return props
}
async function open() {
  fireEvent.change(screen.getByLabelText("Recipient first name"), {
    target: { value: "José" },
  })
  fireEvent.change(screen.getByLabelText("This invitation was sent to"), {
    target: { value: "caregiver" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Show saved answers" }))
  await screen.findByRole("heading", { name: "Your saved answers" })
}
const receipt = (sequence: number, complete = true, reviewed = false) => ({
  disposition: "accepted",
  basis: { original_response_count: 4, event_sequence: sequence },
  current_answers_complete: complete,
  current_answers_reviewed: reviewed,
})

describe("saved patient contribution review", () => {
  it("requires fresh confirmation and confirms the exact displayed basis without clinician claims", async () => {
    const { client, onStatus } = setup()
    expect(client.readContribution).not.toHaveBeenCalled()
    expect(
      screen.queryByText("Original invented story."),
    ).not.toBeInTheDocument()
    await open()
    expect(client.readContribution).toHaveBeenCalledWith({
      first_name: "José",
      role: "caregiver",
    })
    expect(screen.getByText(/Caregiver report/)).toBeInTheDocument()
    expect(
      screen.queryByLabelText("Recipient first name"),
    ).not.toBeInTheDocument()
    client.reviewContribution.mockResolvedValue(receipt(1, true, true))
    fireEvent.click(screen.getByRole("button", { name: "Confirm my answers" }))
    await screen.findByText(/You confirmed these saved answers/)
    expect(client.reviewContribution.mock.calls[0][0]).toEqual({
      first_name: "José",
      role: "caregiver",
      basis: initial.basis,
    })
    expect(client.readContribution).toHaveBeenCalledOnce()
    expect(onStatus).toHaveBeenLastCalledWith(true, true)
    expect(
      screen.getByText(/does not mean a provider has reviewed/),
    ).toBeInTheDocument()
  })

  it("retains original wording, reloads Core applicability and permits a newly relevant skip", async () => {
    const { client } = setup()
    await open()
    const missing = {
      question: question("adherence_barrier", 4),
      response_kind: null,
      answer: null,
      initial_response_kind: null,
      initial_answer: null,
      changed: false,
      recorded_at: null,
    }
    const changed: PatientContribution = {
      ...initial,
      basis: receipt(1).basis,
      current_answers_complete: false,
      topics: [
        initial.topics[0],
        missing,
        {
          ...initial.topics[1],
          answer: "Corrected invented story.",
          changed: true,
        },
      ],
    }
    client.correctContribution
      .mockResolvedValueOnce(receipt(1, false))
      .mockResolvedValueOnce(receipt(2))
    client.readContribution
      .mockResolvedValueOnce(changed)
      .mockResolvedValueOnce({
        ...changed,
        basis: receipt(2).basis,
        current_answers_complete: true,
        topics: [
          changed.topics[0],
          {
            ...missing,
            response_kind: "skipped",
            initial_response_kind: "skipped",
            recorded_at: "2099-01-01T12:01:00Z",
          },
          changed.topics[2],
        ],
      })
    fireEvent.click(
      screen.getByRole("button", { name: "Change: Tell me your story" }),
    )
    expect(screen.getByLabelText("Tell me your story")).toHaveValue(
      "Original invented story.",
    )
    fireEvent.change(screen.getByLabelText("Tell me your story"), {
      target: { value: "Corrected invented story." },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }))
    await screen.findByText("Corrected invented story.")
    expect(screen.getByText("Original invented story.")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Confirm my answers" }),
    ).toBeDisabled()
    fireEvent.click(
      screen.getByRole("button", {
        name: "Answer: Synthetic adherence_barrier",
      }),
    )
    fireEvent.click(screen.getByRole("button", { name: "Skip this topic" }))
    await screen.findByText("You skipped this topic.")
    expect(client.correctContribution.mock.calls[1][0]).toMatchObject({
      basis: receipt(1).basis,
      question_key: "adherence_barrier",
      response_kind: "skipped",
      answer: null,
    })
    expect(
      screen.getByRole("button", { name: "Confirm my answers" }),
    ).toBeEnabled()
  })

  it("keeps uncertain edits frozen and retries the identical payload and UUID", async () => {
    const { client } = setup()
    await open()
    client.correctContribution
      .mockRejectedValueOnce(new VisitPrepPatientFailure("temporary"))
      .mockResolvedValueOnce(receipt(1))
    client.readContribution.mockResolvedValue({
      ...initial,
      basis: receipt(1).basis,
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Change: Tell me your story" }),
    )
    fireEvent.change(screen.getByLabelText("Tell me your story"), {
      target: { value: "Uncertain invented edit." },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }))
    await screen.findByRole("button", { name: "Retry same save" })
    expect(screen.getByLabelText("Tell me your story")).toBeDisabled()
    expect(
      screen.getByRole("button", { name: "Back to preparation" }),
    ).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Retry same save" }))
    await screen.findByRole("heading", { name: "Your saved answers" })
    expect(client.correctContribution.mock.calls[0]).toEqual(
      client.correctContribution.mock.calls[1],
    )
  })

  it("recovers a failed post-save read without submitting the mutation again", async () => {
    const { client } = setup()
    await open()
    client.correctContribution.mockResolvedValue(receipt(1))
    client.readContribution
      .mockRejectedValueOnce(new VisitPrepPatientFailure("temporary"))
      .mockResolvedValueOnce({ ...initial, basis: receipt(1).basis })
    fireEvent.click(
      screen.getByRole("button", { name: "Change: Tell me your story" }),
    )
    fireEvent.click(screen.getByRole("button", { name: "Skip this topic" }))
    await screen.findByRole("button", { name: "Reload saved answers" })
    expect(
      screen.queryByRole("button", { name: "Retry same save" }),
    ).not.toBeInTheDocument()
    fireEvent.click(
      screen.getByRole("button", { name: "Reload saved answers" }),
    )
    await screen.findByRole("heading", { name: "Your saved answers" })
    expect(client.correctContribution).toHaveBeenCalledOnce()
  })

  it("rejects stale edits and requires a current read before a new save", async () => {
    const { client } = setup()
    await open()
    client.correctContribution.mockRejectedValue(
      new VisitPrepPatientFailure("conflict"),
    )
    fireEvent.click(
      screen.getByRole("button", { name: "Change: Tell me your story" }),
    )
    fireEvent.click(screen.getByRole("button", { name: "Skip this topic" }))
    await screen.findByRole("button", { name: "Reload saved answers" })
    expect(
      screen.queryByRole("button", { name: "Retry same save" }),
    ).not.toBeInTheDocument()
    fireEvent.click(
      screen.getByRole("button", { name: "Reload saved answers" }),
    )
    await screen.findByRole("heading", { name: "Your saved answers" })
    expect(client.correctContribution).toHaveBeenCalledOnce()
  })

  it("conceals saved answers on focus loss and ignores an earlier in-flight read", async () => {
    const { client } = setup()
    await open()
    fireEvent(window, new Event("blur"))
    expect(
      screen.queryByText("Original invented story."),
    ).not.toBeInTheDocument()
    expect(client.readContribution).toHaveBeenCalledOnce()
    let resolve!: (value: PatientContribution) => void
    client.readContribution.mockImplementation(
      () =>
        new Promise<PatientContribution>((done) => {
          resolve = done
        }),
    )
    fireEvent.change(screen.getByLabelText("Recipient first name"), {
      target: { value: "José" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Show saved answers" }))
    await waitFor(() =>
      expect(client.readContribution).toHaveBeenCalledTimes(2),
    )
    fireEvent(window, new Event("blur"))
    await act(async () => resolve(initial))
    expect(
      screen.queryByText("Original invented story."),
    ).not.toBeInTheDocument()
  })

  it("conceals an uncertain correction on focus loss but retains its exact retry", async () => {
    const { client } = setup()
    await open()
    let resolve!: (value: ReturnType<typeof receipt>) => void
    client.correctContribution
      .mockImplementationOnce(
        () =>
          new Promise<ReturnType<typeof receipt>>((done) => {
            resolve = done
          }),
      )
      .mockResolvedValueOnce(receipt(1))
    fireEvent.click(
      screen.getByRole("button", { name: "Change: Tell me your story" }),
    )
    fireEvent.change(screen.getByLabelText("Tell me your story"), {
      target: { value: "Hidden uncertain invented draft." },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }))
    await waitFor(() =>
      expect(client.correctContribution).toHaveBeenCalledOnce(),
    )
    fireEvent(window, new Event("blur"))
    expect(
      screen.queryByDisplayValue("Hidden uncertain invented draft."),
    ).not.toBeInTheDocument()
    await act(async () => resolve(receipt(1)))
    expect(
      screen.queryByText("Original invented story."),
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Retry same save" }))
    await screen.findByRole("heading", { name: "Your saved answers" })
    expect(client.correctContribution.mock.calls[0]).toEqual(
      client.correctContribution.mock.calls[1],
    )
  })

  it("lets a rejected validation draft be edited without changing an uncertain request", async () => {
    const { client } = setup()
    await open()
    client.correctContribution.mockRejectedValue(
      new VisitPrepPatientFailure("invalid"),
    )
    fireEvent.click(
      screen.getByRole("button", { name: "Change: Tell me your story" }),
    )
    fireEvent.change(screen.getByLabelText("Tell me your story"), {
      target: { value: "Rejected invented draft." },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }))
    await screen.findByRole("button", { name: "Edit my response" })
    fireEvent.click(screen.getByRole("button", { name: "Edit my response" }))
    expect(screen.getByLabelText("Tell me your story")).toHaveValue(
      "Rejected invented draft.",
    )
    expect(screen.getByLabelText("Tell me your story")).toBeEnabled()
  })

  it("reports rate limiting without automatically retrying confirmation", async () => {
    const { client } = setup()
    client.readContribution.mockRejectedValue(
      new VisitPrepPatientFailure("limited"),
    )
    fireEvent.change(screen.getByLabelText("Recipient first name"), {
      target: { value: "José" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Show saved answers" }))
    await screen.findByRole("alert")
    expect(screen.getByText(/Wait 15 minutes/)).toBeInTheDocument()
    expect(client.readContribution).toHaveBeenCalledOnce()
  })

  it("denies current authority and asks its parent to clear the invitation", async () => {
    const { client, onUnavailable } = setup()
    client.readContribution.mockRejectedValue(
      new VisitPrepPatientFailure("unavailable"),
    )
    fireEvent.change(screen.getByLabelText("Recipient first name"), {
      target: { value: "José" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Show saved answers" }))
    await waitFor(() => expect(onUnavailable).toHaveBeenCalledOnce())
    expect(
      screen.queryByText("Original invented story."),
    ).not.toBeInTheDocument()
    expect(screen.getByLabelText("Recipient first name")).toHaveValue("")
  })
})
