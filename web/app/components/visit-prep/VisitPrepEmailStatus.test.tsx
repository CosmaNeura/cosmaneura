import { beforeEach, describe, expect, it, vi } from "vitest"

import { VisitPrepEmailStatus } from "./VisitPrepEmailStatus"
import { act, fireEvent, render, screen } from "@testing-library/react"

const api = vi.hoisted(() => ({ read: vi.fn(), subscribe: vi.fn() }))
vi.mock("~/configuration/client", () => ({
  visitPrepEmailStatus: (...args: unknown[]) => api.read(...args),
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
const base = {
  contract: "visit_prep.email_status.v1",
  preparation_id: id,
  status: "queued",
  outcome_code: null,
  current_credential_generation: 1,
  credential_generation: 1,
  context_current: true,
  invitation_expired: false,
  submission_window_elapsed: null,
  requested_at: "2026-10-01T12:00:00Z",
  expires_at: "2026-10-02T12:00:00Z",
  submitted_at: null,
  finalized_at: null,
  checked_at: "2026-10-01T12:01:00Z",
  dispatch_integration: "default_off_worker",
}
function value(status: string) {
  if (status === "not_requested")
    return {
      ...base,
      status,
      credential_generation: null,
      context_current: null,
      invitation_expired: null,
      requested_at: null,
      expires_at: null,
    }
  if (status === "queued") return base
  return {
    ...base,
    status,
    submitted_at: ["submitting", "accepted", "uncertain"].includes(status)
      ? "2026-10-01T12:00:10Z"
      : null,
    finalized_at: status === "submitting" ? null : "2026-10-01T12:00:20Z",
    submission_window_elapsed: status === "submitting" ? false : null,
    outcome_code:
      status === "failed"
        ? "transport_unavailable"
        : status === "suppressed"
          ? "consent_withdrawn"
          : status === "uncertain"
            ? "submission_uncertain"
            : null,
  }
}
function mount() {
  return render(
    <VisitPrepEmailStatus
      preparationId={id}
      access={access}
      scopeKey="synthetic"
    />,
  )
}
async function load() {
  fireEvent.click(
    screen.getByRole("button", { name: "Load / reload invitation status" }),
  )
  await screen.findByText(/recorded state at last load/)
}
beforeEach(() => {
  vi.resetAllMocks()
  api.subscribe.mockReturnValue(() => undefined)
  api.read.mockResolvedValue({ data: base })
})
describe("invitation status snapshot", () => {
  it("reads only on demand with no-store and no mutation or browser persistence", async () => {
    mount()
    expect(api.read).not.toHaveBeenCalled()
    await load()
    expect(api.read).toHaveBeenCalledTimes(1)
    expect(api.read).toHaveBeenCalledWith({
      path: { prep_id: id },
      cache: "no-store",
    })
    expect(screen.getByText("Invitation queued at last load")).toBeVisible()
    expect(screen.queryByRole("button", { name: /send|retry/i })).toBeNull()
    expect(Object.entries(localStorage)).toEqual([])
    expect(Object.entries(sessionStorage)).toEqual([])
  })
  it.each([
    ["not_requested", "No email invitation requested"],
    ["submitting", "Submission claimed — outcome not recorded"],
    ["accepted", "Email provider accepted the invitation"],
    ["failed", "Invitation failed before confirmed acceptance"],
    ["suppressed", "Invitation suppressed"],
    ["uncertain", "Submission outcome uncertain — do not resend"],
  ])("displays honest %s status", async (status, label) => {
    api.read.mockResolvedValue({ data: value(status) })
    mount()
    await load()
    expect(screen.getByText(label)).toBeVisible()
    if (status === "accepted")
      expect(
        screen.getByText(/does not prove the patient received/),
      ).toBeVisible()
    if (["submitting", "uncertain"].includes(status))
      expect(screen.getByText(/original attempt may already/)).toBeVisible()
  })
  it("flags obsolete and expired work without converting it to a terminal outcome", async () => {
    api.read.mockResolvedValue({
      data: {
        ...value("submitting"),
        current_credential_generation: 2,
        context_current: false,
        checked_at: "2026-10-03T12:01:00Z",
        invitation_expired: true,
        submission_window_elapsed: true,
      },
    })
    mount()
    await load()
    expect(screen.getByText(/obsolete or inactive/)).toBeVisible()
    expect(screen.getByText(/sending deadline has passed/)).toBeVisible()
    expect(
      screen.getByText(/final reconciliation is not recorded/),
    ).toBeVisible()
  })
  it.each([
    { status: "delivered" },
    { recipient: "secret@example.invalid" },
    { preparation_id: "visit_prep_other" },
    { dispatch_integration: "connected" },
    { submitted_at: "invalid" },
    { outcome_code: "invented" },
    { invitation_expired: true },
    { checked_at: "2020-01-01T12:00:00Z" },
    { credential_generation: 2 },
    { context_current: "true" },
    { finalized_at: "2026-10-01T12:00:20Z" },
  ])("rejects malformed or overbroad status %j", async (patch) => {
    api.read.mockResolvedValue({ data: { ...base, ...patch } })
    mount()
    fireEvent.click(screen.getByRole("button"))
    await screen.findByRole("alert")
    expect(screen.queryByText(/queued at last load/)).toBeNull()
    expect(screen.queryByText(/secret@example/)).toBeNull()
    expect(api.read).toHaveBeenCalledTimes(1)
  })
  it.each(["blur", "pagehide", "visibility", "support"])(
    "clears the snapshot on %s",
    async (event) => {
      mount()
      await load()
      if (event === "visibility") {
        Object.defineProperty(document, "visibilityState", {
          configurable: true,
          value: "hidden",
        })
        fireEvent(document, new Event("visibilitychange"))
        Object.defineProperty(document, "visibilityState", {
          configurable: true,
          value: "visible",
        })
      } else
        fireEvent(
          window,
          new Event(
            event === "support" ? "cosmaneura:view-as-hard-block" : event,
          ),
        )
      expect(screen.queryByText(/queued at last load/)).toBeNull()
    },
  )
  it("drops a late response after scope changes", async () => {
    let resolve!: (result: unknown) => void
    api.read.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    const view = mount()
    fireEvent.click(screen.getByRole("button"))
    view.rerender(
      <VisitPrepEmailStatus
        preparationId={id}
        access={access}
        scopeKey="other"
      />,
    )
    await act(async () => resolve({ data: base }))
    expect(screen.queryByText(/queued at last load/)).toBeNull()
  })
  it("clears before an unavailable reload and never silently retries", async () => {
    mount()
    await load()
    api.read.mockRejectedValue(new Error("Synthetic private provider detail"))
    fireEvent.click(screen.getByRole("button"))
    expect(screen.queryByText(/queued at last load/)).toBeNull()
    await screen.findByRole("alert")
    expect(api.read).toHaveBeenCalledTimes(2)
    expect(screen.queryByText(/private provider detail/)).toBeNull()
  })
  it("clears an existing snapshot when a different preparation is selected", async () => {
    const view = mount()
    await load()
    view.rerender(
      <VisitPrepEmailStatus
        preparationId="visit_prep_other"
        access={access}
        scopeKey="synthetic"
      />,
    )
    expect(screen.queryByText(/queued at last load/)).toBeNull()
    expect(api.read).toHaveBeenCalledTimes(1)
  })
  it("drops a late response after preparation changes without changing owner scope", async () => {
    let resolve!: (result: unknown) => void
    api.read.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
    const view = mount()
    fireEvent.click(screen.getByRole("button"))
    view.rerender(
      <VisitPrepEmailStatus
        preparationId="visit_prep_other"
        access={access}
        scopeKey="synthetic"
      />,
    )
    await act(async () => resolve({ data: base }))
    expect(screen.queryByText(/queued at last load/)).toBeNull()
  })
  it("renders no control or data without current access", () => {
    render(
      <VisitPrepEmailStatus
        preparationId={id}
        access={{ ...access, eligible: false }}
        scopeKey="synthetic"
      />,
    )
    expect(screen.queryByRole("button")).toBeNull()
    expect(api.read).not.toHaveBeenCalled()
  })
})
