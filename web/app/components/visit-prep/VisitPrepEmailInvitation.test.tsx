import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  VisitPrepEmailInvitation,
  validInvitationReceipt,
} from "./VisitPrepEmailInvitation"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"

const api = vi.hoisted(() => ({ queue: vi.fn(), status: vi.fn() }))
vi.mock("~/configuration/client", () => ({
  visitPrepEmailInvitation: (...args: unknown[]) => api.queue(...args),
  visitPrepEmailStatus: (...args: unknown[]) => api.status(...args),
}))
vi.mock("~/lib/view-as-binding", () => ({
  VIEW_AS_HARD_BLOCK_EVENT: "view-as-hard-block",
  subscribeToViewAsStateChanges: () => () => {},
}))

const token = `vp1_${"A".repeat(43)}`
const preparation = { id: "visit_prep_synthetic", credential_generation: 1 }
const access = { eligible: true, can_manage: true, can_send_email: true }
const receipt = {
  contract: "visit_prep.email_invitation.v1",
  preparation_id: preparation.id,
  credential_generation: 1,
  state: "queued",
  disposition: "queued",
  requested_at: "2099-01-01T12:00:00Z",
  expires_at: "2099-01-02T12:00:00Z",
  dispatch_integration: "default_off_worker",
} as const

function mount(extra = {}) {
  const queued = vi.fn()
  const props = {
    preparation: preparation as never,
    access: access as never,
    scopeKey: "synthetic-owner",
    getAccessUrl: () =>
      `https://intake.example.invalid/visit-prep#access_token=${token}`,
    onQueued: queued,
    ...extra,
  }
  return { ...render(<VisitPrepEmailInvitation {...props} />), queued, props }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.queue.mockResolvedValue({ data: receipt })
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe("explicit provider invitation handoff", () => {
  it("makes no initial send/read, queues the existing link only on action, then clears custody", async () => {
    const page = mount()
    expect(api.queue).not.toHaveBeenCalled()
    expect(api.status).not.toHaveBeenCalled()
    fireEvent.click(
      screen.getByRole("button", { name: "Queue email invitation" }),
    )
    await screen.findByRole("status")
    expect(api.queue).toHaveBeenCalledTimes(1)
    expect(api.queue.mock.calls[0][0]).toMatchObject({
      path: { prep_id: preparation.id },
      body: { expected_credential_generation: 1, access_token: token },
      cache: "no-store",
    })
    expect(api.queue.mock.calls[0][0].headers["Idempotency-Key"]).toMatch(
      /^[0-9a-f-]{36}$/,
    )
    expect(page.queued).toHaveBeenCalledTimes(1)
    expect(
      screen.getByRole("button", { name: "Queue email invitation" }),
    ).toBeDisabled()
    expect(document.body.textContent).not.toContain(token)
  })

  it("lost acknowledgment retries the exact frozen request rather than minting an invitation", async () => {
    api.queue.mockRejectedValueOnce(new Error("invented response lost"))
    mount()
    fireEvent.click(
      screen.getByRole("button", { name: "Queue email invitation" }),
    )
    await screen.findByRole("alert")
    fireEvent.click(
      screen.getByRole("button", { name: "Retry same invitation handoff" }),
    )
    await screen.findByRole("status")
    expect(api.queue.mock.calls[0][0]).toEqual(api.queue.mock.calls[1][0])
  })

  it("concealment discards a late response and prevents a fresh-key recovery", async () => {
    let resolve!: (value: unknown) => void
    api.queue.mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r
        }),
    )
    const page = mount()
    fireEvent.click(
      screen.getByRole("button", { name: "Queue email invitation" }),
    )
    fireEvent(window, new Event("blur"))
    resolve({ data: receipt })
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Retry same invitation handoff" }),
      ).toBeDisabled(),
    )
    expect(page.queued).not.toHaveBeenCalled()
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it("scope replacement discards stale response and support view hides the action", async () => {
    let resolve!: (value: unknown) => void
    api.queue.mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r
        }),
    )
    const page = mount()
    fireEvent.click(
      screen.getByRole("button", { name: "Queue email invitation" }),
    )
    page.rerender(
      <VisitPrepEmailInvitation
        {...page.props}
        scopeKey="other-owner"
        access={{ ...access, can_manage: false } as never}
      />,
    )
    resolve({ data: receipt })
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /Queue email invitation/ }),
      ).not.toBeInTheDocument(),
    )
    expect(page.queued).not.toHaveBeenCalled()
  })

  it("does not replace a missing link or accept an overbroad receipt", async () => {
    const page = mount({ getAccessUrl: () => null })
    fireEvent.click(
      screen.getByRole("button", { name: "Queue email invitation" }),
    )
    await screen.findByRole("alert")
    expect(api.queue).not.toHaveBeenCalled()
    expect(page.queued).not.toHaveBeenCalled()
    expect(validInvitationReceipt(receipt, preparation.id, 1)).toBe(true)
    expect(
      validInvitationReceipt(
        { ...receipt, answer: "invented" } as never,
        preparation.id,
        1,
      ),
    ).toBe(false)
    expect(
      validInvitationReceipt(
        { ...receipt, credential_generation: 2 },
        preparation.id,
        1,
      ),
    ).toBe(false)
  })
})
