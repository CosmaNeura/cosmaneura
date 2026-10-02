import { beforeEach, describe, expect, it, vi } from "vitest"

import { VisitPrepPackages } from "./VisitPrepPackages"
import { act, fireEvent, render, screen } from "@testing-library/react"

const api = vi.hoisted(() => ({
  list: vi.fn(),
  package: vi.fn(),
  request: vi.fn(),
  email: vi.fn(),
  subscribe: vi.fn(),
}))
vi.mock("~/configuration/client", () => ({
  visitPrepList: (...args: unknown[]) => api.list(...args),
  visitPrepSynthesis: (...args: unknown[]) => api.package(...args),
  visitPrepSynthesisRequest: (...args: unknown[]) => api.request(...args),
  visitPrepEmailStatus: (...args: unknown[]) => api.email(...args),
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
const sources = {
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
const value = {
  contract: "visit_prep.patient_synthesis.v1",
  sources,
  status: "missing",
  build_status: "not_requested",
  retry_at: null,
  brief: null,
  artifact_id: null,
  version: null,
  recorded_at: null,
  generator_version: "patient_sources_v1",
  prompt_version: "patient_report_only_v1",
  model_profile: "synthetic_model",
}
function ready() {
  const item = {
    text: "Recipient reports a corrected story.",
    evidence: [
      {
        source_ref: "visit_prep_contribution_correction",
        quote: "corrected synthetic story",
      },
    ],
  }
  return {
    ...value,
    status: "ready",
    build_status: "ready",
    version: 1,
    artifact_id: "visit_prep_synthesis_synthetic",
    recorded_at: "2099-01-01T11:01:00Z",
    brief: {
      headline: item,
      summary: [item],
      visit_priorities: [],
      medication_and_adherence: [],
      barriers: [],
      uncertainties: [],
    },
  }
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
  it("never mixes a contribution with another preparation's invitation panel", async () => {
    const other = { ...prep, id: "visit_prep_other" }
    api.list.mockResolvedValue({
      data: { preparations: [prep, other], next_before_id: null },
    })
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    await open()
    fireEvent.click(
      screen.getByRole("button", {
        name: `Email invitation status ${other.id}`,
      }),
    )
    expect(
      screen.queryByRole("heading", { name: "Patient contribution" }),
    ).toBeNull()
    expect(
      screen.getByRole("heading", { name: "Invitation email status" }),
    ).toBeVisible()
    fireEvent.click(
      screen.getByRole("button", { name: `Open contribution ${prep.id}` }),
    )
    await screen.findByRole("heading", { name: "Patient contribution" })
    expect(
      screen.queryByRole("heading", { name: "Invitation email status" }),
    ).toBeNull()
    expect(api.email).not.toHaveBeenCalled()
  })
  it("opens status independently of patient answers, including an unopened interview", async () => {
    api.list.mockResolvedValue({
      data: {
        preparations: [{ ...prep, lifecycle_state: "invited" }],
        next_before_id: null,
      },
    })
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    fireEvent.click(screen.getByRole("button", { name: "Load preparations" }))
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Email invitation status ${prep.id}`,
      }),
    )
    expect(
      screen.getByRole("heading", { name: "Invitation email status" }),
    ).toBeVisible()
    expect(api.package).not.toHaveBeenCalled()
    expect(api.email).not.toHaveBeenCalled()
    expect(
      screen.getByRole("button", { name: `Open contribution ${prep.id}` }),
    ).toBeDisabled()
  })
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
    expect(screen.getAllByText(/Caregiver report/)[1]).toHaveTextContent(
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
      data: {
        ...value,
        sources: { ...sources, package_id: "visit_prep_wrong" },
      },
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
  it("renders a current cited brief as interpretation alongside actual words, without generation", async () => {
    api.package.mockResolvedValue({ data: ready() })
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    await open()
    expect(
      screen.getByRole("heading", { name: "Pre-visit brief" }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/AI interpretation of patient-reported/),
    ).toBeInTheDocument()
    expect(
      screen.getAllByText("Recipient reports a corrected story."),
    ).toHaveLength(2)
    expect(screen.getAllByText("corrected synthetic story")).toHaveLength(2)
    expect(
      screen.getByText("My corrected synthetic story."),
    ).toBeInTheDocument()
    expect(
      screen.getByText("visit_prep_synthesis_synthetic"),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Generate synthesis" }),
    ).not.toBeInTheDocument()
    expect(api.request).not.toHaveBeenCalled()
  })
  it("queues only an explicit exact-source request, clears old content and re-reads atomically", async () => {
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    await open()
    const pending = deferred<unknown>()
    api.request.mockReturnValue(pending.promise)
    api.package.mockResolvedValue({
      data: { ...value, build_status: "pending" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Generate synthesis" }))
    expect(api.request).toHaveBeenCalledWith({
      path: { prep_id: prep.id },
      body: { expected_source_version: sources.source_version },
      cache: "no-store",
    })
    expect(
      screen.queryByText("My corrected synthetic story."),
    ).not.toBeInTheDocument()
    await act(async () =>
      pending.resolve({
        data: {
          package_id: prep.id,
          source_version: sources.source_version,
          state: "pending",
        },
      }),
    )
    await screen.findByText(/Synthesis queued/)
    expect(api.package).toHaveBeenCalledTimes(2)
    expect(api.request).toHaveBeenCalledOnce()
    expect(
      screen.queryByRole("button", { name: "Generate synthesis" }),
    ).not.toBeInTheDocument()
  })
  it("never retries an uncertain POST automatically and requires a fresh read", async () => {
    api.request.mockRejectedValue(new Error("Synthetic acknowledgment lost"))
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    await open()
    fireEvent.click(screen.getByRole("button", { name: "Generate synthesis" }))
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "status is unknown",
    )
    expect(
      screen.queryByText("My corrected synthetic story."),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Generate synthesis" }),
    ).not.toBeInTheDocument()
    api.package.mockResolvedValue({
      data: { ...value, build_status: "running" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Reload contribution" }))
    await screen.findByText(/Synthesis is being generated/)
    expect(api.request).toHaveBeenCalledOnce()
  })
  it("keeps terminal cooldown read-only until Core's retry time", async () => {
    api.package.mockResolvedValue({
      data: {
        ...value,
        build_status: "failed",
        retry_at: "2099-01-01T12:00:00Z",
      },
    })
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    await open()
    expect(screen.getByText(/Synthesis is unavailable/)).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Request synthesis again" }),
    ).toBeDisabled()
    expect(
      screen.getByRole("button", { name: "Reload contribution" }),
    ).toBeEnabled()
    expect(
      screen.getByText("My corrected synthetic story."),
    ).toBeInTheDocument()
    expect(api.request).not.toHaveBeenCalled()
  })
  it("discards a late synthesis request after support denial without reading its result", async () => {
    const pending = deferred<unknown>()
    api.request.mockReturnValue(pending.promise)
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    await open()
    fireEvent.click(screen.getByRole("button", { name: "Generate synthesis" }))
    fireEvent(window, new Event("cosmaneura:view-as-hard-block"))
    await act(async () =>
      pending.resolve({
        data: {
          package_id: prep.id,
          source_version: sources.source_version,
          state: "ready",
        },
      }),
    )
    expect(api.package).toHaveBeenCalledOnce()
    expect(
      screen.queryByRole("heading", { name: "Pre-visit brief" }),
    ).not.toBeInTheDocument()
  })
  it("discards the post-request atomic read when the account changes", async () => {
    const pending = deferred<unknown>()
    api.request.mockResolvedValue({
      data: {
        package_id: prep.id,
        source_version: sources.source_version,
        state: "ready",
      },
    })
    const view = render(
      <VisitPrepPackages access={access} scopeKey="user_synthetic" />,
    )
    await open()
    api.package.mockReturnValue(pending.promise)
    fireEvent.click(screen.getByRole("button", { name: "Generate synthesis" }))
    await act(async () => {})
    expect(api.package).toHaveBeenCalledTimes(2)
    view.rerender(<VisitPrepPackages access={access} scopeKey="other_user" />)
    await act(async () => pending.resolve({ data: ready() }))
    expect(
      screen.queryByText("Recipient reports a corrected story."),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByText("My corrected synthetic story."),
    ).not.toBeInTheDocument()
    expect(api.request).toHaveBeenCalledOnce()
  })
  it("never displays the previous brief after a correction makes its version stale", async () => {
    api.package.mockResolvedValueOnce({ data: ready() }).mockResolvedValueOnce({
      data: {
        ...value,
        status: "stale",
        sources: { ...sources, source_version: "b".repeat(64) },
      },
    })
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    await open()
    fireEvent.click(screen.getByRole("button", { name: "Reload contribution" }))
    await screen.findByText(/The contribution changed/)
    expect(
      screen.queryByText("Recipient reports a corrected story."),
    ).not.toBeInTheDocument()
    expect(screen.getByText("b".repeat(64))).toBeInTheDocument()
    expect(api.request).not.toHaveBeenCalled()
  })
  it.each(["explanation", "choice"])(
    "grounds an explanation in its own source, not the %s source",
    async (source) => {
      const body = ready()
      body.sources = {
        ...sources,
        topics: sources.topics.map((topic) =>
          topic.question_key === "adherence"
            ? {
                ...topic,
                explanation_words: "My separately reported synthetic context.",
                explanation_source_id: "visit_prep_contribution_explanation",
                explanation_recorded_at: "2099-01-01T11:00:30Z",
              }
            : topic,
        ),
      }
      const item = {
        text: "Recipient reports context for the selected answer.",
        evidence: [
          {
            source_ref:
              source === "explanation"
                ? "visit_prep_contribution_explanation"
                : "visit_prep_response_adherence",
            quote: "separately reported synthetic context",
          },
        ],
      }
      body.brief.headline = item
      body.brief.summary = [item]
      api.package.mockResolvedValue({ data: body })
      render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
      fireEvent.click(screen.getByRole("button", { name: "Load preparations" }))
      fireEvent.click(
        await screen.findByRole("button", {
          name: `Open contribution ${prep.id}`,
        }),
      )
      if (source === "explanation") {
        await screen.findByRole("heading", { name: "Pre-visit brief" })
        expect(
          screen.getByText("Recipient explanation for this selected answer"),
        ).toBeInTheDocument()
        expect(screen.getByText("Some of the time")).toBeInTheDocument()
        expect(screen.getAllByText(item.text).length).toBeGreaterThanOrEqual(1)
      } else {
        await screen.findByRole("alert")
        expect(screen.queryByText(item.text)).not.toBeInTheDocument()
        expect(
          screen.queryByText("My separately reported synthetic context."),
        ).not.toBeInTheDocument()
      }
    },
  )
  it.each(["unknown_source", "old_quote", "stale_output", "wrong_contract"])(
    "rejects %s synthesis instead of rendering a mismatched interpretation",
    async (failure) => {
      const body = ready()
      if (failure === "unknown_source")
        body.brief.headline.evidence[0].source_ref = "old_source"
      if (failure === "old_quote")
        body.brief.headline.evidence[0].quote = "original synthetic story"
      if (failure === "stale_output") body.status = "stale"
      if (failure === "wrong_contract") body.contract = "wrong_contract"
      api.package.mockResolvedValue({ data: body })
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
      expect(
        screen.queryByText("Recipient reports a corrected story."),
      ).not.toBeInTheDocument()
    },
  )
  it("shows an honest empty brief without filling in skipped answers", async () => {
    const body = {
      ...ready(),
      sources: {
        ...sources,
        topics: sources.topics.map((topic) => ({
          ...topic,
          response_kind: "skipped",
          answer: null,
          initial_response_kind: "skipped",
          initial_answer: null,
        })),
      },
      brief: {
        headline: null,
        summary: [],
        visit_priorities: [],
        medication_and_adherence: [],
        barriers: [],
        uncertainties: [],
      },
    }
    api.package.mockResolvedValue({ data: body })
    render(<VisitPrepPackages access={access} scopeKey="user_synthetic" />)
    await open()
    expect(
      screen.getByText(/No answers were supplied for synthesis/),
    ).toBeInTheDocument()
    expect(api.request).not.toHaveBeenCalled()
  })
})
