import { beforeEach, describe, expect, it, vi } from "vitest"

import { VisitPrepSMSStatus } from "./VisitPrepSMSStatus"
import { act, fireEvent, render, screen } from "@testing-library/react"

const api = vi.hoisted(() => ({ read: vi.fn(), subscribe: vi.fn() }))
vi.mock("~/configuration/client", () => ({
  visitPrepSmsStatus: (...args: unknown[]) => api.read(...args),
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
const id = "visit_prep_synthetic"
const queued = {
  step: "booking_sms",
  status: "queued",
  outcome_code: null,
  credential_generation: 1,
  context_current: true,
  invitation_expired: false,
  submission_window_elapsed: null,
  requested_at: "2026-10-01T12:00:00Z",
  expires_at: "2026-10-02T12:00:00Z",
  submitted_at: null,
  finalized_at: null,
}
const empty = (step: string) => ({
  ...queued,
  step,
  status: "not_requested",
  credential_generation: null,
  context_current: null,
  invitation_expired: null,
  requested_at: null,
  expires_at: null,
})
const base = {
  contract: "visit_prep.sms_status.v1",
  preparation_id: id,
  current_credential_generation: 1,
  checked_at: "2026-10-01T12:01:00Z",
  dispatch_integration: "not_connected",
  messages: [queued, empty("unopened_48h_sms"), empty("unfinished_12h_sms")],
}
function mount() {
  return render(
    <VisitPrepSMSStatus
      preparationId={id}
      access={access}
      scopeKey="synthetic"
    />,
  )
}
async function load() {
  fireEvent.click(
    screen.getByRole("button", { name: "Load / reload SMS status" }),
  )
  await screen.findByText(/recorded state at last load/)
}
beforeEach(() => {
  vi.resetAllMocks()
  api.subscribe.mockReturnValue(() => undefined)
  api.read.mockResolvedValue({ data: base })
})
describe("SMS invitation and reminder snapshot", () => {
  it("reads three fixed steps only on demand without send permission or persistence", async () => {
    mount()
    expect(api.read).not.toHaveBeenCalled()
    await load()
    expect(api.read).toHaveBeenCalledWith({
      path: { prep_id: id },
      cache: "no-store",
    })
    expect(screen.getByText("Text queued at last load")).toBeVisible()
    expect(screen.getAllByText("No text requested")).toHaveLength(2)
    expect(
      screen.getByRole("heading", { name: "48-hour unopened reminder" }),
    ).toBeVisible()
    expect(
      screen.getByRole("heading", { name: "12-hour unfinished reminder" }),
    ).toBeVisible()
    expect(screen.queryByRole("button", { name: /send|retry/i })).toBeNull()
    expect(Object.entries(localStorage)).toEqual([])
    expect(Object.entries(sessionStorage)).toEqual([])
  })
  it.each([
    ["submitting", null, "Submission claimed — outcome not recorded"],
    ["accepted", null, "SMS service accepted the text"],
    [
      "failed",
      "transport_unavailable",
      "Text failed before confirmed acceptance",
    ],
    ["suppressed", "obsolete_context", "Text suppressed"],
    [
      "uncertain",
      "submission_uncertain",
      "Submission outcome uncertain — do not resend",
    ],
  ])("shows honest %s outcome", async (status, code, label) => {
    api.read.mockResolvedValue({
      data: {
        ...base,
        messages: [
          {
            ...queued,
            status,
            outcome_code: code,
            submitted_at: ["submitting", "accepted", "uncertain"].includes(
              status!,
            )
              ? "2026-10-01T12:00:10Z"
              : null,
            finalized_at:
              status === "submitting" ? null : "2026-10-01T12:00:20Z",
            submission_window_elapsed: status === "submitting" ? false : null,
          },
          ...base.messages.slice(1),
        ],
      },
    })
    mount()
    await load()
    expect(screen.getByText(label!)).toBeVisible()
    expect(
      screen.getByText(/does not prove the patient received/),
    ).toBeVisible()
    if (["submitting", "uncertain"].includes(status!))
      expect(screen.getByText(/original attempt may already/)).toBeVisible()
  })
  it.each([
    { status: "delivered" },
    { recipient: "+12025550123" },
    { outcome_code: "consent_withdrawn" },
    { credential_generation: 2 },
    { requested_at: "invalid" },
    { context_current: "true" },
    { invitation_expired: true },
    { finalized_at: "2026-10-01T12:00:20Z" },
    { expires_at: "2026-10-03T12:00:00Z" },
  ])("rejects overbroad or inconsistent per-message data %j", async (patch) => {
    api.read.mockResolvedValue({
      data: {
        ...base,
        messages: [{ ...queued, ...patch }, ...base.messages.slice(1)],
      },
    })
    mount()
    fireEvent.click(screen.getByRole("button"))
    await screen.findByRole("alert")
    expect(screen.queryByText(/queued at last load/)).toBeNull()
    expect(screen.queryByText(/12025550123/)).toBeNull()
  })
  it.each([
    { preparation_id: "visit_prep_other" },
    { dispatch_integration: "connected" },
    { messages: [queued] },
    { messages: [queued, queued, queued] },
    { checked_at: "2020-01-01T12:00:00Z" },
    { private: "hidden" },
  ])("rejects malformed outer scope or step sequence %j", async (patch) => {
    api.read.mockResolvedValue({ data: { ...base, ...patch } })
    mount()
    fireEvent.click(screen.getByRole("button"))
    await screen.findByRole("alert")
    expect(screen.queryByText(/queued at last load/)).toBeNull()
  })
  it("clears old snapshots before a failed reload without automatically retrying", async () => {
    mount()
    await load()
    api.read.mockRejectedValue(new Error("private provider failure"))
    fireEvent.click(screen.getByRole("button"))
    await screen.findByRole("alert")
    expect(screen.queryByText(/queued at last load/)).toBeNull()
    expect(screen.getByRole("alert")).not.toHaveTextContent("private")
    expect(api.read).toHaveBeenCalledTimes(2)
  })
  it.each(["blur", "pagehide", "cosmaneura:view-as-hard-block"])(
    "ignores a pending response after %s",
    async (signal) => {
      let resolve!: (value: unknown) => void
      api.read.mockReturnValue(
        new Promise((r) => {
          resolve = r
        }),
      )
      mount()
      fireEvent.click(screen.getByRole("button"))
      act(() => window.dispatchEvent(new Event(signal)))
      await act(async () => resolve({ data: base }))
      expect(screen.queryByText(/queued at last load/)).toBeNull()
      expect(api.read).toHaveBeenCalledTimes(1)
    },
  )
  it("discards the snapshot on owner context change", async () => {
    const view = mount()
    await load()
    view.rerender(
      <VisitPrepSMSStatus
        preparationId={id}
        access={access}
        scopeKey="other"
      />,
    )
    expect(screen.queryByText(/queued at last load/)).toBeNull()
  })
  it("support binding changes are sticky and prevent another read", async () => {
    mount()
    await load()
    act(() => api.subscribe.mock.calls[0][0]())
    expect(screen.getByText(/unavailable in this session/)).toBeVisible()
    expect(screen.queryByRole("button")).toBeNull()
    expect(api.read).toHaveBeenCalledTimes(1)
  })
})
