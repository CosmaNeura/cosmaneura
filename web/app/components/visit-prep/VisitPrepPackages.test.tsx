import { beforeEach, describe, expect, it, vi } from "vitest"

import { VisitPrepPackages } from "./VisitPrepPackages"
import { act, fireEvent, render, screen } from "@testing-library/react"

const api = vi.hoisted(() => ({
  list: vi.fn(),
  package: vi.fn(),
  subscribe: vi.fn(),
}))
vi.mock("~/configuration/client", () => ({
  visitPrepList: (...args: unknown[]) => api.list(...args),
  visitPrepPackage: (...args: unknown[]) => api.package(...args),
}))
vi.mock("~/lib/view-as-binding", () => ({
  VIEW_AS_HARD_BLOCK_EVENT: "cosmaneura:view-as-hard-block",
  subscribeToViewAsStateChanges: (...args: unknown[]) => api.subscribe(...args),
}))
const access = {
  eligible: true,
  can_manage: true,
  can_send_email: false,
  can_send_sms: false,
}
const prep = {
  id: "visit_prep_synthetic",
  scheduled_at: "2099-01-01T12:00:00Z",
  timezone_name: "UTC",
  lifecycle_state: "completed",
  revoked_at: null,
  patient_id: null,
  question_set_version_id: "visit_prep_question_set_version_synthetic",
}
const value = {
  contract: "visit_prep.patient_sources.v1",
  package_id: prep.id,
  source_version: "a".repeat(64),
  preparation: prep,
  actor_role: "caregiver",
  basis: { original_response_count: 4, event_sequence: 2 },
  current_answers_complete: false,
  current_answers_reviewed: false,
  original_interview_completed: true,
  topics: [
    {
      question_key: "story",
      prompt: "Tell me your story",
      kind: "story",
      choices: [],
      response_kind: "answered",
      answer: "My corrected synthetic story.",
      initial_response_kind: "answered",
      initial_answer: "My original synthetic story.",
      changed: true,
      original_response_id: "visit_prep_response_original",
      current_revision_id: "visit_prep_contribution_correction",
      additional_answer_source_id: null,
      recorded_at: "2099-01-01T11:00:00Z",
    },
    {
      question_key: "adherence",
      prompt: "Adherence report",
      kind: "choice",
      choices: [{ value: "some", label: "Some of the time" }],
      response_kind: "answered",
      answer: "some",
      initial_response_kind: "answered",
      initial_answer: "some",
      changed: false,
      original_response_id: "visit_prep_response_adherence",
      current_revision_id: null,
      additional_answer_source_id: null,
      recorded_at: "2099-01-01T11:00:00Z",
    },
    {
      question_key: "adherence_barrier",
      prompt: "Relevant barrier",
      kind: "story",
      choices: [],
      response_kind: null,
      answer: null,
      initial_response_kind: null,
      initial_answer: null,
      changed: false,
      original_response_id: null,
      current_revision_id: null,
      additional_answer_source_id: null,
      recorded_at: null,
    },
    {
      question_key: "medications",
      prompt: "Medication report",
      kind: "list",
      choices: [],
      response_kind: "skipped",
      answer: null,
      initial_response_kind: "skipped",
      initial_answer: null,
      changed: false,
      original_response_id: "visit_prep_response_skip",
      current_revision_id: null,
      additional_answer_source_id: null,
      recorded_at: "2099-01-01T11:00:00Z",
    },
  ],
}
async function open() {
  fireEvent.click(screen.getByRole("button", { name: "Load preparations" }))
  fireEvent.click(
    await screen.findByRole("button", { name: `Open contribution ${prep.id}` }),
  )
  await screen.findByRole("heading", { name: "Patient contribution" })
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((complete) => {
    resolve = complete
  })
  return { promise, resolve }
}
beforeEach(() => {
  vi.resetAllMocks()
  api.subscribe.mockReturnValue(() => undefined)
  api.list.mockResolvedValue({
    data: { preparations: [prep], next_before_id: null },
  })
  api.package.mockResolvedValue({ data: value })
})
describe("provider current-answer package", () => {
  it("reads only on demand, displays actual words and honest missing/revision labels", async () => {
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    expect(api.list).not.toHaveBeenCalled()
    expect(api.package).not.toHaveBeenCalled()
    await open()
    expect(api.list).toHaveBeenCalledWith({
      query: { limit: 20 },
      cache: "no-store",
    })
    expect(api.package).toHaveBeenCalledWith({
      path: { prep_id: prep.id },
      cache: "no-store",
    })
    expect(
      screen.getByText("My corrected synthetic story."),
    ).toBeInTheDocument()
    expect(screen.getByText("My original synthetic story.")).toBeInTheDocument()
    expect(screen.getByText("Some of the time")).toBeInTheDocument()
    expect(screen.getByText("Not answered")).toBeInTheDocument()
    expect(screen.getByText("Skipped — not supplied")).toBeInTheDocument()
    expect(screen.getByText(/Caregiver report/)).toHaveTextContent(
      "Incomplete contribution",
    )
    expect(
      screen.getByText(/AI synthesis has not been generated/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Not linked to a patient chart/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/original interview was submitted/),
    ).toBeInTheDocument()
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument()
    expect(
      screen.getByText("visit_prep_contribution_correction"),
    ).toBeInTheDocument()
  })
  it("removes old content before a reload and never retains it after an access failure", async () => {
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    await open()
    const pending = deferred<unknown>()
    api.package.mockReturnValue(pending.promise)
    fireEvent.click(screen.getByRole("button", { name: "Reload contribution" }))
    expect(
      screen.queryByText("My corrected synthetic story."),
    ).not.toBeInTheDocument()
    await act(async () =>
      pending.resolve({
        error: { detail: "Not found" },
        response: { status: 404 },
      }),
    )
    expect(screen.getByRole("alert")).toHaveTextContent(
      "unavailable or has changed",
    )
    expect(
      screen.queryByText("My corrected synthetic story."),
    ).not.toBeInTheDocument()
  })
  it("discards late package replies after browser concealment and does not auto-read on focus", async () => {
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    const pending = deferred<unknown>()
    api.package.mockReturnValue(pending.promise)
    fireEvent.click(screen.getByRole("button", { name: "Load preparations" }))
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Open contribution ${prep.id}`,
      }),
    )
    fireEvent(window, new Event("blur"))
    await act(async () => pending.resolve({ data: value }))
    fireEvent(window, new Event("focus"))
    expect(
      screen.queryByText("My corrected synthetic story."),
    ).not.toBeInTheDocument()
    expect(api.package).toHaveBeenCalledOnce()
  })
  it("clears visible answers and denies reload after support-context hard block", async () => {
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    await open()
    fireEvent(window, new Event("cosmaneura:view-as-hard-block"))
    expect(
      screen.queryByText("My corrected synthetic story."),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Load preparations" }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByText(/Patient contributions are unavailable/),
    ).toBeInTheDocument()
  })
  it("hides data immediately when the account or current eligibility changes", async () => {
    const view = render(
      <VisitPrepPackages access={access} scopeKey="user_synthetic" />,
    )
    await open()
    view.rerender(<VisitPrepPackages access={access} scopeKey="user_other" />)
    expect(
      screen.queryByText("My corrected synthetic story."),
    ).not.toBeInTheDocument()
    view.rerender(
      <VisitPrepPackages
        access={{ ...access, can_manage: false }}
        scopeKey="user_other"
      />,
    )
    expect(
      screen.queryByRole("button", { name: "Load preparations" }),
    ).not.toBeInTheDocument()
  })
  it("does not read unconfirmed, revoked or quarantined contributions", async () => {
    api.list.mockResolvedValue({
      data: {
        preparations: [
          { ...prep, lifecycle_state: "invited" },
          { ...prep, id: "revoked", revoked_at: "2099-01-01" },
          { ...prep, id: "quarantined", lifecycle_state: "misdirected" },
        ],
        next_before_id: null,
      },
    })
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    fireEvent.click(screen.getByRole("button", { name: "Load preparations" }))
    const buttons = await screen.findAllByRole("button", {
      name: /Open contribution/,
    })
    expect(buttons).toHaveLength(3)
    buttons.forEach((button) => expect(button).toBeDisabled())
    expect(api.package).not.toHaveBeenCalled()
  })
  it("uses Core's bounded next-page cursor and does not fetch any answers", async () => {
    api.list
      .mockResolvedValueOnce({
        data: { preparations: [prep], next_before_id: prep.id },
      })
      .mockResolvedValueOnce({
        data: { preparations: [], next_before_id: null },
      })
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    fireEvent.click(screen.getByRole("button", { name: "Load preparations" }))
    fireEvent.click(
      await screen.findByRole("button", { name: "Next preparations" }),
    )
    await screen.findByText("No preparations found.")
    expect(api.list).toHaveBeenLastCalledWith({
      query: { limit: 20, before_id: prep.id },
      cache: "no-store",
    })
    expect(api.package).not.toHaveBeenCalled()
  })
  it("rejects a response for another preparation instead of displaying its answers", async () => {
    api.package.mockResolvedValue({
      data: { ...value, package_id: "visit_prep_wrong" },
    })
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    fireEvent.click(screen.getByRole("button", { name: "Load preparations" }))
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Open contribution ${prep.id}`,
      }),
    )
    await screen.findByRole("alert")
    expect(
      screen.queryByText("My corrected synthetic story."),
    ).not.toBeInTheDocument()
  })
})
