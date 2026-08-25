import { beforeEach, describe, expect, it, vi } from "vitest"

import { VisitPrepQuestionSetSettings } from "./QuestionSetSettings"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

const api = vi.hoisted(() => ({
  list: vi.fn(),
  replace: vi.fn(),
  successor: vi.fn(),
  publish: vi.fn(),
  makeDefault: vi.fn(),
  toast: vi.fn(),
}))

vi.mock("~/configuration/client", () => ({
  visitPrepQuestionSetList: (...args: unknown[]) => api.list(...args),
  visitPrepQuestionSetReplace: (...args: unknown[]) => api.replace(...args),
  visitPrepQuestionSetSuccessor: (...args: unknown[]) => api.successor(...args),
  visitPrepQuestionSetPublish: (...args: unknown[]) => api.publish(...args),
  visitPrepQuestionSetDefault: (...args: unknown[]) => api.makeDefault(...args),
}))

vi.mock("~/hooks/use-toast", () => ({
  toast: (...args: unknown[]) => api.toast(...args),
}))

const UPDATED_AT = "2026-08-25T16:00:00Z"

function version({
  id = "visit_prep_question_set_version_01",
  versionNumber = 1,
  published = true,
  isDefault = true,
  updatedAt = UPDATED_AT,
  firstPrompt = "What is the main reason for this visit?",
}: {
  id?: string
  versionNumber?: number
  published?: boolean
  isDefault?: boolean
  updatedAt?: string | null
  firstPrompt?: string
} = {}) {
  return {
    id,
    question_set_id: "visit_prep_question_set_01",
    version_number: versionNumber,
    name: "Visit Prep default questions",
    supersedes_version_id:
      versionNumber === 1 ? null : "visit_prep_question_set_version_01",
    published_at: published ? "2026-08-25T15:00:00Z" : null,
    is_default: isDefault,
    created_at: "2026-08-25T15:00:00Z",
    updated_at: updatedAt,
    questions: [
      {
        id: `${id}_question_1`,
        question_set_version_id: id,
        key: "reason",
        position: 1,
        prompt: firstPrompt,
        helper_text: "In your own words.",
        kind: "story" as const,
        choices: [],
        show_if: null,
        created_at: UPDATED_AT,
        updated_at: UPDATED_AT,
      },
      {
        id: `${id}_question_2`,
        question_set_version_id: id,
        key: "medications",
        position: 2,
        prompt: "What medications are you prescribed right now?",
        helper_text: "Include the dose if you know it.",
        kind: "list" as const,
        choices: [],
        show_if: null,
        created_at: UPDATED_AT,
        updated_at: UPDATED_AT,
      },
      {
        id: `${id}_question_3`,
        question_set_version_id: id,
        key: "adherence",
        position: 3,
        prompt: "Are you taking them as prescribed?",
        helper_text: "There is no wrong answer.",
        kind: "choice" as const,
        choices: [
          { value: "yes", label: "Yes, as prescribed", hint: "Most days" },
          { value: "some", label: "Some of them", hint: "A few get skipped" },
          {
            value: "no",
            label: "Not really",
            hint: "Something got in the way",
          },
          { value: "none", label: "I don't take any", hint: null },
        ],
        show_if: null,
        created_at: UPDATED_AT,
        updated_at: UPDATED_AT,
      },
      {
        id: `${id}_question_4`,
        question_set_version_id: id,
        key: "adherence_barrier",
        position: 4,
        prompt: "If not — what got in the way?",
        helper_text: "All of that is useful.",
        kind: "story" as const,
        choices: [],
        show_if: {
          question_id: `${id}_question_3`,
          question_key: "adherence",
          values: ["some", "no"],
        },
        created_at: UPDATED_AT,
        updated_at: UPDATED_AT,
      },
      {
        id: `${id}_question_5`,
        question_set_version_id: id,
        key: "story",
        position: 5,
        prompt: "Tell me your health story — whatever feels relevant.",
        helper_text: "What belongs in this visit?",
        kind: "story" as const,
        choices: [],
        show_if: null,
        created_at: UPDATED_AT,
        updated_at: UPDATED_AT,
      },
    ],
  }
}

function questionSet(versions = [version()]) {
  return {
    id: "visit_prep_question_set_01",
    created_at: "2026-08-25T15:00:00Z",
    updated_at: UPDATED_AT,
    versions,
  }
}

function mutationResult(
  targetVersion: ReturnType<typeof version>,
  versions = [targetVersion],
) {
  return {
    question_set: questionSet(versions),
    version: targetVersion,
    disposition: "updated" as const,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset())
  api.list.mockResolvedValue({ data: { question_sets: [questionSet()] } })
  vi.spyOn(window, "confirm").mockReturnValue(true)
})

describe("VisitPrepQuestionSetSettings", () => {
  it("does not reveal or request the feature for an ineligible physician", () => {
    render(
      <VisitPrepQuestionSetSettings
        access={{ eligible: false, can_manage: false }}
      />,
    )

    expect(screen.queryByText("Visit Prep questions")).not.toBeInTheDocument()
    expect(api.list).not.toHaveBeenCalled()
  })

  it("renders support view as read-only and masks authored content", async () => {
    render(
      <VisitPrepQuestionSetSettings
        access={{ eligible: true, can_manage: false }}
      />,
    )

    expect(
      await screen.findByText("Read-only while using support view."),
    ).toBeInTheDocument()
    expect(screen.getByLabelText("Question set name")).toHaveAttribute(
      "readonly",
    )
    expect(
      screen.queryByRole("button", { name: "Edit as new version" }),
    ).not.toBeInTheDocument()

    const section = screen.getByRole("region", {
      name: "Visit Prep questions",
    })
    expect(section).toHaveAttribute("data-sentry-mask")
    expect(section).toHaveAttribute("data-ph-no-capture")
  })

  it("creates and saves a successor without changing stable structure", async () => {
    const published = version()
    const draft = version({
      id: "visit_prep_question_set_version_02",
      versionNumber: 2,
      published: false,
      isDefault: false,
      updatedAt: "2026-08-25T17:00:00Z",
    })
    api.successor.mockResolvedValue({
      data: mutationResult(draft, [published, draft]),
    })
    const saved = version({
      id: draft.id,
      versionNumber: 2,
      published: false,
      isDefault: false,
      updatedAt: "2026-08-25T17:05:00Z",
      firstPrompt: "What matters most for today's visit?",
    })
    api.replace.mockResolvedValue({
      data: mutationResult(saved, [published, saved]),
    })

    render(
      <VisitPrepQuestionSetSettings
        access={{ eligible: true, can_manage: true }}
      />,
    )

    fireEvent.click(
      await screen.findByRole("button", { name: "Edit as new version" }),
    )
    await waitFor(() =>
      expect(api.successor).toHaveBeenCalledWith({
        path: { version_id: published.id },
      }),
    )

    const prompt = await screen.findByLabelText("Question 1 text")
    fireEvent.change(prompt, {
      target: { value: "What matters most for today's visit?" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }))

    await waitFor(() => expect(api.replace).toHaveBeenCalledTimes(1))
    const request = api.replace.mock.calls[0][0]
    expect(request.path).toEqual({ version_id: draft.id })
    expect(request.body.expected_updated_at).toBe("2026-08-25T17:00:00Z")
    expect(request.body.questions).toHaveLength(5)
    expect(request.body.questions[0]).toMatchObject({
      key: "reason",
      kind: "story",
      prompt: "What matters most for today's visit?",
    })
    expect(request.body.questions[3].show_if).toEqual({
      question_key: "adherence",
      values: ["some", "no"],
    })
  })

  it("locks authored fields while a draft save is in flight", async () => {
    const draft = version({ published: false, isDefault: false })
    const pending = deferred<{
      data: ReturnType<typeof mutationResult>
    }>()
    api.list.mockResolvedValue({
      data: { question_sets: [questionSet([draft])] },
    })
    api.replace.mockReturnValue(pending.promise)

    render(
      <VisitPrepQuestionSetSettings
        access={{ eligible: true, can_manage: true }}
      />,
    )

    const prompt = await screen.findByLabelText("Question 1 text")
    fireEvent.change(prompt, { target: { value: "Submitted wording" } })
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }))

    await waitFor(() => expect(api.replace).toHaveBeenCalledTimes(1))
    expect(
      screen.getByRole("region", { name: "Visit Prep questions" }),
    ).toHaveAttribute("aria-busy", "true")
    screen
      .getAllByRole("textbox")
      .forEach((field) => expect(field).toHaveAttribute("readonly"))

    await act(async () => {
      pending.resolve({
        data: mutationResult(
          version({
            published: false,
            isDefault: false,
            firstPrompt: "Submitted wording",
          }),
        ),
      })
      await pending.promise
    })

    await waitFor(() => expect(prompt).not.toHaveAttribute("readonly"))
    expect(prompt).toHaveValue("Submitted wording")
  })

  it("publishes a saved draft and selects it as the default atomically", async () => {
    const draft = version({
      id: "visit_prep_question_set_version_02",
      versionNumber: 2,
      published: false,
      isDefault: false,
    })
    api.list.mockResolvedValue({
      data: { question_sets: [questionSet([draft])] },
    })
    const published = {
      ...draft,
      published_at: "2026-08-25T18:00:00Z",
      is_default: true,
    }
    api.publish.mockResolvedValue({
      data: mutationResult(published, [published]),
    })

    render(
      <VisitPrepQuestionSetSettings
        access={{ eligible: true, can_manage: true }}
      />,
    )

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Publish and make default",
      }),
    )

    await waitFor(() =>
      expect(api.publish).toHaveBeenCalledWith({
        path: { version_id: draft.id },
        body: { make_default: true },
      }),
    )
    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringContaining("Published versions cannot be changed"),
    )
    expect(await screen.findAllByText("Published · Default")).toHaveLength(2)
  })

  it("changes the default only through the published-version endpoint", async () => {
    const published = version({ isDefault: false })
    api.list.mockResolvedValue({
      data: { question_sets: [questionSet([published])] },
    })
    api.makeDefault.mockResolvedValue({
      data: mutationResult({ ...published, is_default: true }),
    })

    render(
      <VisitPrepQuestionSetSettings
        access={{ eligible: true, can_manage: true }}
      />,
    )

    fireEvent.click(await screen.findByRole("button", { name: "Make default" }))

    await waitFor(() =>
      expect(api.makeDefault).toHaveBeenCalledWith({
        path: { version_id: published.id },
      }),
    )
  })

  it("keeps authored edits after a stale-write conflict and shows safe copy", async () => {
    const draft = version({ published: false, isDefault: false })
    api.list.mockResolvedValue({
      data: { question_sets: [questionSet([draft])] },
    })
    api.replace.mockResolvedValue({
      error: { detail: "private database constraint and authored text" },
      response: { status: 409 },
    })

    render(
      <VisitPrepQuestionSetSettings
        access={{ eligible: true, can_manage: true }}
      />,
    )

    const prompt = await screen.findByLabelText("Question 1 text")
    fireEvent.change(prompt, { target: { value: "Keep my unsaved wording" } })
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This version changed elsewhere. Your edits are still here",
    )
    expect(screen.getByLabelText("Question 1 text")).toHaveValue(
      "Keep my unsaved wording",
    )
    expect(screen.queryByText(/private database constraint/i)).toBeNull()
  })

  it("refuses to save a draft without its concurrency marker", async () => {
    const draft = version({
      published: false,
      isDefault: false,
      updatedAt: null,
    })
    api.list.mockResolvedValue({
      data: { question_sets: [questionSet([draft])] },
    })

    render(
      <VisitPrepQuestionSetSettings
        access={{ eligible: true, can_manage: true }}
      />,
    )

    const prompt = await screen.findByLabelText("Question 1 text")
    fireEvent.change(prompt, { target: { value: "A safe local edit" } })
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "does not have a concurrency marker",
    )
    expect(api.replace).not.toHaveBeenCalled()
  })
})
