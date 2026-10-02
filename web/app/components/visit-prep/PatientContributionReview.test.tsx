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
function setup(continuedRole: "patient" | "caregiver" | null = null) {
  const client = {
    resume: vi.fn(),
    confirm: vi.fn(),
    submit: vi.fn(),
    complete: vi.fn(),
    reportWrongRecipient: vi.fn(),
    dispose: vi.fn(),
    readContribution: vi.fn().mockResolvedValue(initial),
    correctContribution: vi.fn(),
    explainContribution: vi.fn(),
    reviewContribution: vi.fn(),
    readClarification: vi.fn(),
    generateClarification: vi.fn(),
    decideClarification: vi.fn(),
    readFollowup: vi.fn(),
    answerFollowup: vi.fn(),
    readEmailConsent: vi.fn(),
    decideEmailConsent: vi.fn(),
    readSMSConsent: vi.fn(),
    decideSMSConsent: vi.fn(),
  }
  const props = {
    client,
    onUnavailable: vi.fn(),
    onClose: vi.fn(),
    onStatus: vi.fn(),
  }
  render(<PatientContributionReview {...props} continuedRole={continuedRole} />)
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
  it("requests a saved suggestion without changing the answer until accepted", async () => {
    vi.stubEnv("VITE_VISIT_PREP_PATIENT_AI_ENABLED", "true")
    try {
      const { client } = setup("caregiver")
      await screen.findByRole("heading", { name: "Your saved answers" })
      const proposalId = "visit_prep_clarification_01j00000000000000000000000"
      client.generateClarification.mockResolvedValue({
        proposal_id: proposalId,
        basis: initial.basis,
        disposition: "accepted",
      })
      client.readClarification.mockResolvedValue({
        status: "available",
        proposal_id: proposalId,
        basis: initial.basis,
        actor_role: "caregiver",
        question_key: "story",
        current_words: initial.topics[1].answer,
        organized_words: "invented story.",
        turns: [],
      })
      fireEvent.click(
        screen.getByRole("button", {
          name: "Help me clarify: Tell me your story",
        }),
      )
      await screen.findByRole("heading", { name: "Review proposed wording" })
      expect(client.generateClarification).toHaveBeenCalledWith(
        {
          role: "caregiver",
          question_key: "story",
          basis: initial.basis,
          source_turn_id: null,
        },
        expect.any(String),
      )
      expect(client.correctContribution).not.toHaveBeenCalled()
      expect(client.decideClarification).not.toHaveBeenCalled()
      expect(
        screen.getByRole("button", { name: "Accept proposed wording" }),
      ).toBeEnabled()
    } finally {
      vi.unstubAllEnvs()
    }
  })
  it("saves and withdraws an explanation separately without changing the selected code", async () => {
    const { client } = setup("caregiver")
    const choice = {
      ...initial.topics[0],
      question: {
        ...question("adherence", 3),
        kind: "choice" as const,
        choices: [{ value: "yes", label: "Yes, as prescribed" }],
      },
      answer: "yes",
      initial_answer: "yes",
    }
    const withChoice = { ...initial, topics: [choice, initial.topics[1]] }
    client.readContribution.mockResolvedValue(withChoice)
    await screen.findByRole("heading", { name: "Your saved answers" })
    fireEvent(window, new Event("blur"))
    fireEvent.click(screen.getByRole("button", { name: "Show saved answers" }))
    await screen.findByRole("button", { name: "Explain: Synthetic adherence" })
    fireEvent.click(
      screen.getByRole("button", { name: "Explain: Synthetic adherence" }),
    )
    fireEvent.change(
      screen.getByLabelText("What would you like your provider to understand?"),
      { target: { value: "My invented explanation." } },
    )
    client.explainContribution.mockResolvedValue(receipt(1))
    client.readContribution.mockResolvedValue({
      ...withChoice,
      basis: receipt(1).basis,
      topics: [
        {
          ...choice,
          explanation_words: "My invented explanation.",
          explanation_recorded_at: "2099-01-01T12:01:00Z",
        },
        initial.topics[1],
      ],
    })
    fireEvent.click(screen.getByRole("button", { name: "Save my explanation" }))
    await screen.findByText("My invented explanation.")
    expect(client.explainContribution).toHaveBeenCalledWith(
      {
        role: "caregiver",
        basis: initial.basis,
        question_key: "adherence",
        patient_words: "My invented explanation.",
      },
      expect.any(String),
    )
    expect(client.correctContribution).not.toHaveBeenCalled()
    expect(
      screen.queryByLabelText("Recipient first name"),
    ).not.toBeInTheDocument()
    fireEvent.click(
      screen.getByRole("button", { name: "Explain: Synthetic adherence" }),
    )
    client.explainContribution.mockResolvedValue(receipt(2))
    client.readContribution.mockResolvedValue({
      ...withChoice,
      basis: receipt(2).basis,
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Withdraw my explanation" }),
    )
    await screen.findByRole("heading", { name: "Your saved answers" })
    expect(client.explainContribution.mock.calls[1][0].patient_words).toBeNull()
    expect(screen.getByText("Yes, as prescribed")).toBeInTheDocument()
    expect(
      screen.queryByText("My invented explanation."),
    ).not.toBeInTheDocument()
  })
  it("keeps the exact explanation save and UUID for a lost-ack retry", async () => {
    const { client } = setup()
    const choice = {
      ...initial.topics[0],
      question: {
        ...question("adherence", 3),
        kind: "choice" as const,
        choices: [{ value: "yes", label: "Yes" }],
      },
      answer: "yes",
      initial_answer: "yes",
    }
    client.readContribution.mockResolvedValue({
      ...initial,
      topics: [choice, initial.topics[1]],
    })
    await open()
    fireEvent.click(
      screen.getByRole("button", { name: "Explain: Synthetic adherence" }),
    )
    fireEvent.change(
      screen.getByLabelText("What would you like your provider to understand?"),
      { target: { value: "Invented retry words." } },
    )
    client.explainContribution
      .mockRejectedValueOnce(new VisitPrepPatientFailure("temporary"))
      .mockResolvedValue(receipt(1))
    fireEvent.click(screen.getByRole("button", { name: "Save my explanation" }))
    await screen.findByRole("alert")
    fireEvent.click(screen.getByRole("button", { name: "Retry same save" }))
    await screen.findByRole("heading", { name: "Your saved answers" })
    expect(client.explainContribution.mock.calls[1]).toEqual(
      client.explainContribution.mock.calls[0],
    )
  })
  it("uses remembered confirmation without asking for names again, including after concealment", async () => {
    const { client } = setup("caregiver")
    await screen.findByRole("heading", { name: "Your saved answers" })
    expect(client.readContribution).toHaveBeenCalledWith({ role: "caregiver" })
    expect(
      screen.queryByLabelText("Recipient first name"),
    ).not.toBeInTheDocument()
    fireEvent(window, new Event("blur"))
    expect(
      screen.queryByText("Original invented story."),
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Show saved answers" }))
    await screen.findByRole("heading", { name: "Your saved answers" })
    expect(client.readContribution).toHaveBeenCalledTimes(2)
  })
  const followup = {
    contract: "visit_prep.patient_followup.v1",
    status: "prompt",
    question_key: "story",
    actor_role: "caregiver",
    basis: initial.basis,
    proposal_id: "visit_prep_clarification_00000000000000000000000001",
    current_words: "Original invented story.",
    pending_prompt:
      "Is there anything you would like to make clearer about this answer?",
    turns: [],
  }
  const answeredFollowup = {
    ...followup,
    status: "answered",
    turns: [
      {
        prompt: followup.pending_prompt,
        patient_words: "Invented follow-up words.",
      },
    ],
  }
  async function showFollowup(client: ReturnType<typeof setup>["client"]) {
    client.readFollowup.mockResolvedValue(followup)
    fireEvent.click(
      screen.getByRole("button", {
        name: "Check follow-up: Tell me your story",
      }),
    )
    await screen.findByRole("heading", { name: "Clarify your answer" })
  }
  it("acknowledges exact follow-up words without extra reads or changing answers", async () => {
    const { client } = setup()
    await open()
    await showFollowup(client)
    expect(client.answerFollowup).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText(followup.pending_prompt), {
      target: { value: " Invented follow-up words. " },
    })
    client.answerFollowup.mockResolvedValue({
      proposal_id: followup.proposal_id,
      turn_id: "visit_prep_clarification_turn_00000000000000000000000001",
      disposition: "accepted",
      turn_sequence: 1,
      basis: initial.basis,
    })
    client.readFollowup.mockResolvedValue(answeredFollowup)
    fireEvent.click(
      screen.getByRole("button", { name: "Save follow-up answer" }),
    )
    await screen.findByText(/Your follow-up answer is saved/)
    expect(client.answerFollowup.mock.calls[0][0]).toEqual({
      first_name: "José",
      role: "caregiver",
      proposal_id: followup.proposal_id,
      patient_words: "Invented follow-up words.",
    })
    expect(client.correctContribution).not.toHaveBeenCalled()
    expect(client.decideClarification).not.toHaveBeenCalled()
    expect(client.readContribution).toHaveBeenCalledOnce()
    expect(client.readFollowup).toHaveBeenCalledOnce()
    expect(
      screen.queryByLabelText(followup.pending_prompt),
    ).not.toBeInTheDocument()
  })
  it("retries identical follow-up words after an uncertain save", async () => {
    const { client } = setup()
    await open()
    await showFollowup(client)
    fireEvent.change(screen.getByLabelText(followup.pending_prompt), {
      target: { value: "Invented follow-up words." },
    })
    client.answerFollowup
      .mockRejectedValueOnce(new VisitPrepPatientFailure("temporary"))
      .mockResolvedValue({ turn_sequence: 1, basis: initial.basis })
    fireEvent.click(
      screen.getByRole("button", { name: "Save follow-up answer" }),
    )
    await screen.findByRole("button", { name: "Retry same save" })
    expect(screen.getByLabelText(followup.pending_prompt)).toBeDisabled()
    client.readFollowup.mockResolvedValue(answeredFollowup)
    fireEvent.click(screen.getByRole("button", { name: "Retry same save" }))
    await screen.findByText(/Your follow-up answer is saved/)
    expect(client.answerFollowup.mock.calls[0]).toEqual(
      client.answerFollowup.mock.calls[1],
    )
  })
  it("can recover a saved follow-up without resubmitting after a lost receipt", async () => {
    const { client } = setup()
    await open()
    await showFollowup(client)
    fireEvent.change(screen.getByLabelText(followup.pending_prompt), {
      target: { value: "Invented follow-up words." },
    })
    client.answerFollowup.mockRejectedValue(
      new VisitPrepPatientFailure("temporary"),
    )
    fireEvent.click(
      screen.getByRole("button", { name: "Save follow-up answer" }),
    )
    await screen.findByRole("button", { name: "Check whether follow-up saved" })
    client.readFollowup.mockResolvedValue(answeredFollowup)
    fireEvent.click(
      screen.getByRole("button", { name: "Check whether follow-up saved" }),
    )
    await screen.findByText(/Your follow-up answer is saved/)
    expect(client.answerFollowup).toHaveBeenCalledOnce()
    expect(
      screen.queryByRole("button", { name: "Retry same save" }),
    ).not.toBeInTheDocument()
  })
  it("conceals pending follow-up words and discards late acknowledgment", async () => {
    const { client } = setup()
    await open()
    await showFollowup(client)
    let resolve!: (value: object) => void
    client.answerFollowup.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r
        }),
    )
    fireEvent.change(screen.getByLabelText(followup.pending_prompt), {
      target: { value: "Invented follow-up words." },
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Save follow-up answer" }),
    )
    fireEvent(window, new Event("blur"))
    await act(async () => resolve({ turn_sequence: 1, basis: initial.basis }))
    expect(
      screen.queryByText("Invented follow-up words."),
    ).not.toBeInTheDocument()
    client.readFollowup.mockResolvedValue(answeredFollowup)
    fireEvent.click(
      screen.getByRole("button", { name: "Check whether follow-up saved" }),
    )
    await screen.findByText(/Your follow-up answer is saved/)
    expect(client.answerFollowup).toHaveBeenCalledOnce()
  })
  it("rejects a follow-up source changed before presentation", async () => {
    const { client } = setup()
    await open()
    client.readFollowup.mockResolvedValue({
      ...followup,
      current_words: "Different invented answer.",
    })
    fireEvent.click(
      screen.getByRole("button", {
        name: "Check follow-up: Tell me your story",
      }),
    )
    await screen.findByRole("button", { name: "Reload saved answers" })
    expect(
      screen.queryByText("Different invented answer."),
    ).not.toBeInTheDocument()
    expect(client.answerFollowup).not.toHaveBeenCalled()
  })
  it("leaves manual editing usable when a follow-up is unavailable", async () => {
    const { client } = setup()
    await open()
    client.readFollowup.mockResolvedValue({
      ...followup,
      status: "not_available",
      proposal_id: null,
      current_words: null,
      pending_prompt: null,
    })
    fireEvent.click(
      screen.getByRole("button", {
        name: "Check follow-up: Tell me your story",
      }),
    )
    await screen.findByText(/No current follow-up is available/)
    expect(
      screen.getByRole("button", { name: "Change: Tell me your story" }),
    ).toBeEnabled()
    expect(client.answerFollowup).not.toHaveBeenCalled()
  })
  it("does not accept a mismatched turn receipt", async () => {
    const { client } = setup()
    await open()
    await showFollowup(client)
    client.answerFollowup.mockResolvedValue({
      turn_sequence: 2,
      basis: initial.basis,
    })
    fireEvent.change(screen.getByLabelText(followup.pending_prompt), {
      target: { value: "Invented follow-up words." },
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Save follow-up answer" }),
    )
    await screen.findByRole("button", { name: "Retry same save" })
    expect(client.readFollowup).toHaveBeenCalledOnce()
    expect(
      screen.queryByText(/Your follow-up answer is saved/),
    ).not.toBeInTheDocument()
  })
  it("captures a second saved prompt without inventing a third or accepting wording", async () => {
    const { client } = setup()
    await open()
    client.readFollowup.mockResolvedValue({
      ...followup,
      turns: answeredFollowup.turns,
      proposal_id: "visit_prep_clarification_00000000000000000000000002",
      pending_prompt: "What would you most like to discuss at this visit?",
    })
    fireEvent.click(
      screen.getByRole("button", {
        name: "Check follow-up: Tell me your story",
      }),
    )
    await screen.findByRole("heading", { name: "Clarify your answer" })
    fireEvent.change(
      screen.getByLabelText(
        "What would you most like to discuss at this visit?",
      ),
      { target: { value: "Invented visit priority." } },
    )
    client.answerFollowup.mockResolvedValue({
      turn_sequence: 2,
      basis: initial.basis,
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Save follow-up answer" }),
    )
    await screen.findByText(/Your follow-up answer is saved/)
    expect(screen.getByText("Invented follow-up words.")).toBeVisible()
    expect(screen.getByText("Invented visit priority.")).toBeVisible()
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    expect(client.readFollowup).toHaveBeenCalledOnce()
    expect(client.correctContribution).not.toHaveBeenCalled()
    expect(client.decideClarification).not.toHaveBeenCalled()
  })
  const proposal = {
    contract: "visit_prep.patient_clarification.v1",
    status: "available",
    question_key: "story",
    actor_role: "caregiver",
    basis: initial.basis,
    proposal_id: "visit_prep_clarification_00000000000000000000000001",
    current_words: "Original invented story.",
    organized_words: "Original invented story.\nSynthetic additional detail.",
    turns: [
      {
        prompt:
          "Is there anything you would like to make clearer about this answer?",
        patient_words: "Synthetic additional detail.",
      },
    ],
  }
  async function showProposal(client: ReturnType<typeof setup>["client"]) {
    client.readClarification.mockResolvedValue(proposal)
    fireEvent.click(
      screen.getByRole("button", {
        name: "Review wording proposal: Tell me your story",
      }),
    )
    await screen.findByRole("heading", { name: "Review proposed wording" })
  }
  it.each([
    ["confirm", "Accept proposed wording"],
    ["edit", "Save my edited wording"],
    ["keep_original", "Keep my current answer"],
    ["dismiss", "Dismiss proposal"],
  ])(
    "requires explicit %s and refreshes authoritative answers",
    async (action, label) => {
      const { client } = setup()
      await open()
      await showProposal(client)
      expect(client.decideClarification).not.toHaveBeenCalled()
      expect(client.readClarification).toHaveBeenCalledWith(
        { first_name: "José", role: "caregiver" },
        "story",
      )
      expect(screen.getByText(/not medical advice/)).toBeInTheDocument()
      if (action === "edit") {
        fireEvent.click(
          screen.getByRole("button", { name: "Edit proposed wording" }),
        )
        fireEvent.change(screen.getByLabelText("Your wording to share"), {
          target: { value: " Synthetic patient edit. " },
        })
      }
      client.decideClarification.mockResolvedValue(
        receipt(["confirm", "edit"].includes(action) ? 1 : 0),
      )
      fireEvent.click(screen.getByRole("button", { name: label }))
      await screen.findByRole("heading", { name: "Your saved answers" })
      expect(client.decideClarification.mock.calls[0][0]).toEqual({
        first_name: "José",
        role: "caregiver",
        proposal_id: proposal.proposal_id,
        action,
        edited_words: action === "edit" ? "Synthetic patient edit." : null,
      })
      expect(client.readContribution).toHaveBeenCalledTimes(2)
      expect(client.correctContribution).not.toHaveBeenCalled()
    },
  )
  it("keeps manual editing available when no proposal exists", async () => {
    const { client } = setup()
    await open()
    client.readClarification.mockResolvedValue({
      ...proposal,
      status: "not_available",
      proposal_id: null,
      current_words: null,
      organized_words: null,
      turns: [],
    })
    fireEvent.click(
      screen.getByRole("button", {
        name: "Review wording proposal: Tell me your story",
      }),
    )
    await screen.findByText(/No current wording proposal/)
    expect(
      screen.getByRole("button", { name: "Change: Tell me your story" }),
    ).toBeEnabled()
    expect(client.decideClarification).not.toHaveBeenCalled()
  })
  it("conceals an uncertain proposal and retries precisely the same choice", async () => {
    const { client } = setup()
    await open()
    await showProposal(client)
    let resolve!: (value: ReturnType<typeof receipt>) => void
    client.decideClarification
      .mockImplementationOnce(
        () =>
          new Promise((r) => {
            resolve = r
          }),
      )
      .mockResolvedValue(receipt(1))
    fireEvent.click(
      screen.getByRole("button", { name: "Accept proposed wording" }),
    )
    await waitFor(() =>
      expect(client.decideClarification).toHaveBeenCalledOnce(),
    )
    fireEvent(window, new Event("blur"))
    expect(
      screen.queryByText(/Synthetic additional detail/),
    ).not.toBeInTheDocument()
    await act(async () => resolve(receipt(1)))
    expect(
      screen.queryByRole("heading", { name: "Your saved answers" }),
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Retry same save" }))
    await screen.findByRole("heading", { name: "Your saved answers" })
    expect(client.decideClarification.mock.calls[0]).toEqual(
      client.decideClarification.mock.calls[1],
    )
  })
  it("rejects stale wording before presentation and requires a reload", async () => {
    const { client } = setup()
    await open()
    client.readClarification.mockResolvedValue({
      ...proposal,
      current_words: "Different synthetic words.",
    })
    fireEvent.click(
      screen.getByRole("button", {
        name: "Review wording proposal: Tell me your story",
      }),
    )
    await screen.findByRole("button", { name: "Reload saved answers" })
    expect(
      screen.queryByText(/Different synthetic words/),
    ).not.toBeInTheDocument()
    expect(client.decideClarification).not.toHaveBeenCalled()
  })
  it("discards late proposal reads after concealment", async () => {
    const { client } = setup()
    await open()
    let resolve!: (value: typeof proposal) => void
    client.readClarification.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r
        }),
    )
    fireEvent.click(
      screen.getByRole("button", {
        name: "Review wording proposal: Tell me your story",
      }),
    )
    fireEvent(window, new Event("blur"))
    await act(async () => resolve(proposal))
    expect(
      screen.queryByText(/Synthetic additional detail/),
    ).not.toBeInTheDocument()
    expect(screen.getByLabelText("Recipient first name")).toHaveValue("")
  })
  it("does not acknowledge a mismatched decision basis", async () => {
    const { client } = setup()
    await open()
    await showProposal(client)
    client.decideClarification
      .mockResolvedValueOnce(receipt(9))
      .mockResolvedValueOnce(receipt(1))
    fireEvent.click(
      screen.getByRole("button", { name: "Accept proposed wording" }),
    )
    await screen.findByRole("button", { name: "Retry same save" })
    expect(client.readContribution).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole("button", { name: "Retry same save" }))
    await screen.findByRole("heading", { name: "Your saved answers" })
    expect(client.decideClarification.mock.calls[0]).toEqual(
      client.decideClarification.mock.calls[1],
    )
  })
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
