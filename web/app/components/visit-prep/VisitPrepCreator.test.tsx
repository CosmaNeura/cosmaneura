import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { visitPrepScheduleFromLocal } from "~/lib/visit-prep-schedule"

import { VisitPrepCreator } from "./VisitPrepCreator"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const api = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  copyLink: vi.fn(),
  revoke: vi.fn(),
  toast: vi.fn(),
  writeText: vi.fn(),
  randomUUID: vi.fn(),
}))

vi.mock("~/configuration/client", () => ({
  visitPrepQuestionSetList: (...args: unknown[]) => api.list(...args),
  visitPrepCreate: (...args: unknown[]) => api.create(...args),
  visitPrepCopyLink: (...args: unknown[]) => api.copyLink(...args),
  visitPrepRevoke: (...args: unknown[]) => api.revoke(...args),
}))

vi.mock("~/hooks/use-toast", () => ({
  toast: (...args: unknown[]) => api.toast(...args),
}))

const TOKEN_ONE = `vp1_${"A".repeat(43)}`
const TOKEN_TWO = `vp1_${"B".repeat(42)}A`
const URL_ONE = `https://chat.example.test/visit-prep#access_token=${TOKEN_ONE}`
const URL_TWO = `https://chat.example.test/visit-prep#access_token=${TOKEN_TWO}`

function version({
  id,
  name,
  published,
  isDefault,
  versionNumber,
}: {
  id: string
  name: string
  published: boolean
  isDefault: boolean
  versionNumber: number
}) {
  return {
    id,
    question_set_id: `set_${id}`,
    version_number: versionNumber,
    name,
    supersedes_version_id: null,
    published_at: published ? "2026-08-25T12:00:00Z" : null,
    is_default: isDefault,
    created_at: "2026-08-25T12:00:00Z",
    updated_at: "2026-08-25T12:00:00Z",
    questions: Array.from({ length: 5 }, (_, index) => ({
      id: `question_${id}_${index}`,
      question_set_version_id: id,
      key: `topic_${index}`,
      position: index + 1,
      prompt: "Synthetic topic",
      helper_text: null,
      kind: "story",
      choices: [],
      show_if: null,
      created_at: null,
      updated_at: null,
    })),
  }
}

const defaultVersion = version({
  id: "visit_prep_question_set_version_default",
  name: "Default five questions",
  published: true,
  isDefault: true,
  versionNumber: 2,
})
const otherVersion = version({
  id: "visit_prep_question_set_version_other",
  name: "Follow-up questions",
  published: true,
  isDefault: false,
  versionNumber: 1,
})
const draftVersion = version({
  id: "visit_prep_question_set_version_draft",
  name: "Unpublished draft",
  published: false,
  isDefault: false,
  versionNumber: 3,
})

function questionSets() {
  return {
    question_sets: [
      {
        id: "visit_prep_question_set_1",
        created_at: "2026-08-25T12:00:00Z",
        updated_at: "2026-08-25T12:00:00Z",
        versions: [draftVersion, otherVersion, defaultVersion],
      },
    ],
  }
}

function prepState(credentialGeneration = 1, active = true) {
  return {
    id: "visit_prep_01",
    question_set_version_id: defaultVersion.id,
    patient_id: null,
    appointment_id: null,
    lifecycle_state: active ? "invited" : "revoked",
    has_email_contact: true,
    has_sms_contact: false,
    physician_display_name: "Dr. Founder",
    practice_display_name: "Test Practice",
    scheduled_at: "2099-01-15T15:00:00Z",
    timezone_name: "America/New_York",
    schedule_generation: 1,
    credential_generation: credentialGeneration,
    credential_expires_at: "2099-01-18T15:00:00Z",
    credential_active: active,
    revoked_at: active ? null : "2026-08-25T13:00:00Z",
    created_at: "2026-08-25T12:00:00Z",
    updated_at: "2026-08-25T12:00:00Z",
  }
}

const enabledAccess = {
  eligible: true,
  can_manage: true,
  can_send_email: false,
  can_send_sms: false,
}

function fillRequiredForm() {
  fireEvent.change(screen.getByLabelText("Recipient first name"), {
    target: { value: "Alex" },
  })
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "alex@example.com" },
  })
  fireEvent.change(screen.getByLabelText("Appointment"), {
    target: { value: "2099-01-15T10:00" },
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

async function createSuccessfully(accessUrl: string | null = URL_ONE) {
  api.create.mockResolvedValue({
    data: {
      visit_prep: prepState(),
      disposition: accessUrl ? "created" : "replayed",
      access_url: accessUrl,
    },
  })
  render(
    <VisitPrepCreator
      access={enabledAccess}
      physicianDisplayName="Dr. Founder"
    />,
  )
  await screen.findByRole("option", {
    name: "Default five questions · v2 · Default",
  })
  fillRequiredForm()
  fireEvent.click(screen.getByRole("button", { name: "Create Visit Prep" }))
  await screen.findByText("Visit Prep ready")
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset())
  api.list.mockResolvedValue({ data: questionSets() })
  api.randomUUID.mockReturnValue("00000000-0000-4000-8000-000000000001")
  api.writeText.mockResolvedValue(undefined)
  vi.stubEnv("VITE_INTAKE_CHAT_DOMAIN", "chat.example.test")
  vi.stubGlobal("crypto", { randomUUID: api.randomUUID })
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: api.writeText },
  })
  vi.spyOn(window, "confirm").mockReturnValue(true)
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe("VisitPrepCreator", () => {
  it("does not load or mutate for an ineligible or support-view account", () => {
    const { rerender } = render(
      <VisitPrepCreator
        access={{ ...enabledAccess, eligible: false, can_manage: false }}
      />,
    )
    expect(api.list).not.toHaveBeenCalled()
    expect(
      screen.queryByRole("button", { name: "Create Visit Prep" }),
    ).not.toBeInTheDocument()

    rerender(
      <VisitPrepCreator access={{ ...enabledAccess, can_manage: false }} />,
    )
    expect(
      screen.getByText(/read-only while using support view/i),
    ).toBeInTheDocument()
    expect(api.list).not.toHaveBeenCalled()
    expect(api.create).not.toHaveBeenCalled()
  })

  it("offers only published versions and selects the default", async () => {
    render(<VisitPrepCreator access={enabledAccess} />)

    const select = await screen.findByLabelText("Question set")
    const timezoneName = Intl.DateTimeFormat().resolvedOptions().timeZone
    expect(select).toHaveValue(defaultVersion.id)
    expect(
      screen.getByText(`Appointment timezone: ${timezoneName}`),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("option", { name: /Unpublished draft/ }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole("option", { name: /Follow-up questions/ }),
    ).toBeInTheDocument()
  })

  it("does not offer legacy published sets without an unconditional fifth story", async () => {
    const legacy = {
      ...otherVersion,
      questions: otherVersion.questions.slice(0, 4),
    }
    api.list.mockResolvedValue({
      data: { question_sets: [{ versions: [legacy, defaultVersion] }] },
    })
    render(<VisitPrepCreator access={enabledAccess} />)
    await screen.findByLabelText("Question set")
    expect(
      screen.queryByRole("option", { name: /Follow-up questions/ }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByText(/Do not send these links to patients/),
    ).toBeInTheDocument()
  })

  it("does not copy noncanonical bearer encodings", async () => {
    await createSuccessfully(
      `https://chat.example.test/visit-prep#access_token=vp1_${"B".repeat(43)}`,
    )
    expect(
      screen.queryByRole("button", { name: "Copy private link" }),
    ).not.toBeInTheDocument()
    expect(api.writeText).not.toHaveBeenCalled()
  })

  it("sends the exact normalized request and reuses its idempotency key", async () => {
    api.create.mockResolvedValue({ response: { status: 503 } })
    render(
      <VisitPrepCreator
        access={enabledAccess}
        physicianDisplayName="  Dr. Founder  "
      />,
    )
    await screen.findByLabelText("Question set")
    fillRequiredForm()

    fireEvent.click(screen.getByRole("button", { name: "Create Visit Prep" }))
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole("button", { name: "Create Visit Prep" }))
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(2))

    const schedule = visitPrepScheduleFromLocal("2099-01-15T10:00")
    expect(schedule.ok).toBe(true)
    if (!schedule.ok) throw new Error("test schedule must be valid")
    expect(api.create.mock.calls[0][0]).toEqual({
      body: {
        recipient: {
          first_name: "Alex",
          role: "patient",
          email: "alex@example.com",
          phone: null,
        },
        physician_display_name: "Dr. Founder",
        scheduled_at: schedule.scheduledAt,
        timezone_name: schedule.timezoneName,
        question_set_version_id: defaultVersion.id,
      },
      headers: {
        "Idempotency-Key": "00000000-0000-4000-8000-000000000001",
      },
    })
    expect(api.create.mock.calls[1][0].headers).toEqual(
      api.create.mock.calls[0][0].headers,
    )
    expect(api.randomUUID).toHaveBeenCalledTimes(1)
  })

  it("copies the one-time link without rendering, storing, or mutating it", async () => {
    const storageSet = vi.spyOn(Storage.prototype, "setItem")
    await createSuccessfully()

    expect(document.body.textContent).not.toContain(TOKEN_ONE)
    expect(document.body.innerHTML).not.toContain(TOKEN_ONE)
    fireEvent.click(screen.getByRole("button", { name: "Copy private link" }))
    await waitFor(() => expect(api.writeText).toHaveBeenCalledWith(URL_ONE))
    expect(api.copyLink).not.toHaveBeenCalled()
    expect(storageSet).not.toHaveBeenCalled()
    expect(JSON.stringify(api.toast.mock.calls)).not.toContain(TOKEN_ONE)
    expect(
      screen.getByText(/link was copied and is no longer held in this tab/i),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Copy private link" }),
    ).not.toBeInTheDocument()
  })

  it("keeps a failed clipboard copy retryable without exposing the error", async () => {
    api.writeText.mockRejectedValueOnce(
      new Error(`clipboard failed for ${TOKEN_ONE}`),
    )
    await createSuccessfully()

    fireEvent.click(screen.getByRole("button", { name: "Copy private link" }))
    expect(
      await screen.findByText(
        "The link could not be copied. Try again in this tab.",
      ),
    ).toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain(TOKEN_ONE)

    fireEvent.click(screen.getByRole("button", { name: "Copy private link" }))
    await waitFor(() => expect(api.writeText).toHaveBeenCalledTimes(2))
    expect(
      screen.getByText(/link was copied and is no longer held in this tab/i),
    ).toBeInTheDocument()
  })

  it("recovers a replay without a bearer by rotating from the current generation", async () => {
    await createSuccessfully(null)
    api.copyLink.mockResolvedValue({
      data: {
        visit_prep: prepState(2),
        disposition: "rotated",
        access_url: URL_TWO,
      },
    })

    fireEvent.click(
      screen.getByRole("button", { name: "Create replacement link" }),
    )
    await waitFor(() =>
      expect(api.copyLink).toHaveBeenCalledWith({
        path: { prep_id: "visit_prep_01" },
        body: { expected_credential_generation: 1 },
      }),
    )
    fireEvent.click(screen.getByRole("button", { name: "Copy private link" }))
    await waitFor(() => expect(api.writeText).toHaveBeenCalledWith(URL_TWO))
    expect(document.body.innerHTML).not.toContain(TOKEN_TWO)
  })

  it("rejects an unexpected returned link without exposing it", async () => {
    const unexpectedUrl = `https://attacker.example/visit-prep#access_token=${TOKEN_ONE}`
    await createSuccessfully(unexpectedUrl)

    expect(
      screen.getByText(/returned link could not be verified/i),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Copy private link" }),
    ).not.toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain(TOKEN_ONE)
    expect(api.writeText).not.toHaveBeenCalled()
  })

  it("ignores a create response that arrives after management access changes", async () => {
    const pending = deferred<unknown>()
    api.create.mockReturnValue(pending.promise)
    const { rerender } = render(
      <VisitPrepCreator
        access={enabledAccess}
        physicianDisplayName="Dr. Founder"
      />,
    )
    await screen.findByLabelText("Question set")
    fillRequiredForm()
    fireEvent.click(screen.getByRole("button", { name: "Create Visit Prep" }))
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1))

    rerender(
      <VisitPrepCreator
        access={{ ...enabledAccess, can_manage: false }}
        physicianDisplayName="Dr. Founder"
      />,
    )
    rerender(
      <VisitPrepCreator
        access={enabledAccess}
        physicianDisplayName="Dr. Founder"
      />,
    )
    pending.resolve({
      data: {
        visit_prep: prepState(),
        disposition: "created",
        access_url: URL_ONE,
      },
    })

    await screen.findByLabelText("Question set")
    await waitFor(() =>
      expect(screen.queryByText("Visit Prep ready")).not.toBeInTheDocument(),
    )
    expect(screen.getByLabelText("Recipient first name")).toHaveValue("")
    expect(screen.getByLabelText("Email")).toHaveValue("")
    expect(
      screen.queryByRole("button", { name: "Copy private link" }),
    ).not.toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain(TOKEN_ONE)
  })

  it("replaces and revokes with current generations and an explicit reason", async () => {
    await createSuccessfully()
    api.copyLink.mockResolvedValue({
      data: {
        visit_prep: prepState(2),
        disposition: "rotated",
        access_url: URL_TWO,
      },
    })
    api.revoke.mockResolvedValue({
      data: {
        visit_prep: prepState(2, false),
        disposition: "revoked",
        access_url: null,
      },
    })

    fireEvent.click(screen.getByRole("button", { name: "Replace link" }))
    await waitFor(() => expect(api.copyLink).toHaveBeenCalledTimes(1))
    fireEvent.change(screen.getByLabelText("Revoke reason"), {
      target: { value: "wrong_recipient" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Revoke Visit Prep" }))

    await waitFor(() =>
      expect(api.revoke).toHaveBeenCalledWith({
        path: { prep_id: "visit_prep_01" },
        body: {
          expected_credential_generation: 2,
          reason: "wrong_recipient",
        },
      }),
    )
    expect(screen.getByText("Visit Prep revoked")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Copy private link" }),
    ).not.toBeInTheDocument()
    expect(document.body.innerHTML).not.toContain(TOKEN_TWO)
  })

  it("keeps delivery disabled and marks the whole surface private", async () => {
    render(<VisitPrepCreator access={enabledAccess} />)
    await screen.findByLabelText("Question set")

    const region = screen.getByRole("region", { name: "Create Visit Prep" })
    expect(region).toHaveAttribute("data-sentry-block")
    expect(region).toHaveAttribute("data-sentry-mask")
    expect(region).toHaveAttribute("data-ph-no-capture")
    expect(screen.getByLabelText("Recipient first name")).toHaveAttribute(
      "autocomplete",
      "off",
    )
    expect(screen.getByLabelText("Recipient first name")).toHaveAttribute(
      "spellcheck",
      "false",
    )
    expect(screen.getByLabelText("Email")).toHaveAttribute(
      "autocomplete",
      "off",
    )
    expect(screen.getByLabelText("Mobile number (optional)")).toHaveAttribute(
      "autocomplete",
      "off",
    )
    expect(
      screen.getByRole("button", { name: "Send unavailable" }),
    ).toBeDisabled()
  })
})
