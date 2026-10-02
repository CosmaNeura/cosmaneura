import { beforeEach, describe, expect, it, vi } from "vitest"

import { VisitPrepEmailPolicy } from "./VisitPrepEmailPolicy"
import { act, fireEvent, render, screen } from "@testing-library/react"

const api = vi.hoisted(() => ({
  read: vi.fn(),
  decide: vi.fn(),
  subscribe: vi.fn(),
}))
vi.mock("~/configuration/client", () => ({
  visitPrepEmailPolicyRead: (...args: unknown[]) => api.read(...args),
  visitPrepEmailPolicyDecide: (...args: unknown[]) => api.decide(...args),
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
const value = {
  contract: "visit_prep.email_policy.v1",
  template_version: "visit_prep_invitation_v1",
  policy_fingerprint: "a".repeat(64),
  subject: "Prepare for your visit",
  template_text:
    "Tell your story.\n{private_preparation_link}\n<script>not executable</script>",
  status: "not_approved",
  event_sequence: 0,
  decision_policy_fingerprint: null,
  recorded_at: null,
  expires_at: null,
  checked_at: "2026-10-01T12:00:00Z",
  delivery_status: "not_connected",
  standard_cadence: {
    contract: "visit_prep.standard_cadence.v1",
    managed_by: "cosmaneura",
    provider_editable: false,
    timezone_basis: "current_appointment",
    version: "standard-20261002",
    fingerprint: "c".repeat(64),
    quiet_start_hour: 21,
    quiet_end_hour: 8,
    minimum_channel_spacing_hours: 4,
    unopened_reminder_hours_before_visit: 48,
    unfinished_reminder_hours_before_visit: 12,
  },
}
async function load() {
  fireEvent.click(
    screen.getByRole("button", { name: "Load / reload email policy" }),
  )
  await screen.findByRole("heading", { name: "Invitation preview" })
}
function approve() {
  fireEvent.change(screen.getByLabelText("Approval expires at (UTC)"), {
    target: { value: "2099-01-01T12:00" },
  })
  fireEvent.click(screen.getByRole("checkbox"))
  fireEvent.click(
    screen.getByRole("button", { name: "Record physician approval" }),
  )
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
  api.read.mockResolvedValue({ data: value })
  api.decide.mockImplementation(async ({ body }) => ({
    data: {
      ...body,
      event_sequence: body.expected_event_sequence + 1,
      current_event_sequence: body.expected_event_sequence + 1,
      recorded_at: "2026-10-01T12:01:00Z",
      disposition: "recorded",
    },
  }))
})
describe("physician invitation email policy", () => {
  it("shows unavailable timing without substituting invented rules", async () => {
    api.read.mockResolvedValue({ data: { ...value, standard_cadence: null } })
    render(<VisitPrepEmailPolicy access={access} scopeKey="user_synthetic" />)
    await load()
    expect(screen.getByText(/Standard timing is unavailable/)).toBeVisible()
    expect(screen.queryByText(/No messages from/)).toBeNull()
    expect(api.decide).not.toHaveBeenCalled()
  })

  it.each([
    { provider_editable: true },
    { managed_by: "provider" },
    { quiet_start_hour: 24 },
    { minimum_channel_spacing_hours: 0 },
    { unopened_reminder_hours_before_visit: 1 },
    { fingerprint: "invalid" },
  ])("rejects malformed or provider-editable timing %j", async (changes) => {
    api.read.mockResolvedValue({
      data: {
        ...value,
        standard_cadence: { ...value.standard_cadence, ...changes },
      },
    })
    render(<VisitPrepEmailPolicy access={access} scopeKey="user_synthetic" />)
    fireEvent.click(
      screen.getByRole("button", { name: "Load / reload email policy" }),
    )
    expect(await screen.findByRole("status")).toHaveTextContent(
      "could not be loaded",
    )
    expect(
      screen.queryByRole("heading", { name: "Invitation preview" }),
    ).toBeNull()
  })

  it("loads only on demand, uses plain text, and requires explicit review and UTC deadline", async () => {
    const { container } = render(
      <VisitPrepEmailPolicy access={access} scopeKey="user_synthetic" />,
    )
    expect(api.read).not.toHaveBeenCalled()
    await load()
    expect(api.read).toHaveBeenCalledWith({ cache: "no-store" })
    expect(container.querySelector("script")).toBeNull()
    expect(
      screen.getByText(/Providers cannot edit these settings/),
    ).toBeVisible()
    expect(screen.getByText(/No messages from 21:00 to 08:00/)).toBeVisible()
    expect(screen.getByText(/At least 4 hours/)).toBeVisible()
    expect(
      container.querySelectorAll('input[type="number"],select'),
    ).toHaveLength(0)
    expect(screen.getByText(/not executable/)).toBeVisible()
    const button = screen.getByRole("button", {
      name: "Record physician approval",
    })
    expect(button).toBeDisabled()
    fireEvent.click(screen.getByRole("checkbox"))
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText("Approval expires at (UTC)"), {
      target: { value: "2000-01-01T12:00" },
    })
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText("Approval expires at (UTC)"), {
      target: { value: "2099-01-01T12:00" },
    })
    fireEvent.click(button)
    await screen.findByRole("status")
    expect(api.decide).toHaveBeenCalledTimes(1)
    expect(api.decide.mock.calls[0][0]).toEqual({
      body: {
        expected_event_sequence: 0,
        event_kind: "approved",
        policy_fingerprint: value.policy_fingerprint,
        expires_at: "2099-01-01T12:00:00.000Z",
      },
      headers: {
        "Idempotency-Key": expect.stringMatching(
          /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
        ),
      },
      cache: "no-store",
    })
    expect(
      screen.queryByRole("heading", { name: "Invitation preview" }),
    ).toBeNull()
    expect(screen.getByRole("status")).toHaveTextContent(
      "No invitation was sent",
    )
    expect(api.read).toHaveBeenCalledTimes(1)
  })

  it.each([
    ["approved", "Physician policy approved at last load"],
    ["withdrawn", "Physician approval withdrawn"],
    ["expired", "Physician approval expired"],
    ["policy_changed", "Invitation policy changed — new approval required"],
  ])("shows %s as a snapshot, never send readiness", async (status, label) => {
    api.read.mockResolvedValue({
      data: {
        ...value,
        status,
        event_sequence: 1,
        decision_policy_fingerprint: "b".repeat(64),
        expires_at: "2099-01-01T12:00:00Z",
      },
    })
    render(<VisitPrepEmailPolicy access={access} scopeKey="user_synthetic" />)
    await load()
    expect(screen.getByText(label)).toBeVisible()
    expect(screen.getByText(/Delivery is not connected/)).toBeVisible()
    expect(api.decide).not.toHaveBeenCalled()
  })

  it("withdraws the prior policy fingerprint after the current template changes", async () => {
    api.read.mockResolvedValue({
      data: {
        ...value,
        status: "policy_changed",
        event_sequence: 3,
        decision_policy_fingerprint: "b".repeat(64),
      },
    })
    render(<VisitPrepEmailPolicy access={access} scopeKey="user_synthetic" />)
    await load()
    fireEvent.click(
      screen.getByRole("button", { name: "Withdraw physician approval" }),
    )
    await screen.findByRole("status")
    expect(api.decide.mock.calls[0][0].body).toEqual({
      expected_event_sequence: 3,
      event_kind: "withdrawn",
      policy_fingerprint: "b".repeat(64),
      expires_at: null,
    })
  })

  it("never automatically retries an uncertain decision and recovers only by reading", async () => {
    api.decide.mockRejectedValue(new Error("synthetic_private_provider_error"))
    render(<VisitPrepEmailPolicy access={access} scopeKey="user_synthetic" />)
    await load()
    approve()
    expect(await screen.findByRole("status")).toHaveTextContent(
      "outcome is unknown",
    )
    expect(
      screen.queryByRole("button", { name: "Record physician approval" }),
    ).toBeNull()
    expect(document.body.textContent).not.toContain("synthetic_private")
    api.read.mockResolvedValue({
      data: {
        ...value,
        status: "withdrawn",
        event_sequence: 2,
        decision_policy_fingerprint: value.policy_fingerprint,
      },
    })
    await load()
    expect(screen.getByText("Physician approval withdrawn")).toBeVisible()
    expect(api.decide).toHaveBeenCalledTimes(1)
    expect(screen.getByRole("checkbox")).not.toBeChecked()
  })

  it("prevents concurrent clicks while a decision is pending", async () => {
    const pending = deferred<unknown>()
    api.decide.mockReturnValue(pending.promise)
    render(<VisitPrepEmailPolicy access={access} scopeKey="user_synthetic" />)
    await load()
    approve()
    fireEvent.click(screen.getByRole("button", { name: "Working…" }))
    expect(api.decide).toHaveBeenCalledTimes(1)
    expect(api.read).toHaveBeenCalledTimes(1)
    await act(async () => pending.resolve({ data: {} }))
  })

  it.each(["scope", "blur", "support", "hidden", "access"])(
    "clears state and discards late reads after %s changes",
    async (change) => {
      const pending = deferred<unknown>()
      api.read.mockReturnValue(pending.promise)
      const view = render(
        <VisitPrepEmailPolicy access={access} scopeKey="user_synthetic" />,
      )
      fireEvent.click(
        screen.getByRole("button", { name: "Load / reload email policy" }),
      )
      if (change === "scope")
        view.rerender(
          <VisitPrepEmailPolicy access={access} scopeKey="user_other" />,
        )
      else if (change === "access")
        view.rerender(
          <VisitPrepEmailPolicy
            access={{ ...access, eligible: false }}
            scopeKey="user_synthetic"
          />,
        )
      else if (change === "hidden") {
        const original = Object.getOwnPropertyDescriptor(
          document,
          "visibilityState",
        )
        Object.defineProperty(document, "visibilityState", {
          configurable: true,
          value: "hidden",
        })
        fireEvent(document, new Event("visibilitychange"))
        if (original)
          Object.defineProperty(document, "visibilityState", original)
        else Reflect.deleteProperty(document, "visibilityState")
      } else
        fireEvent(
          window,
          new Event(
            change === "support" ? "cosmaneura:view-as-hard-block" : "blur",
          ),
        )
      await act(async () => pending.resolve({ data: value }))
      expect(
        screen.queryByRole("heading", { name: "Invitation preview" }),
      ).toBeNull()
      expect(screen.queryByRole("status")).toBeNull()
      expect(api.decide).not.toHaveBeenCalled()
    },
  )

  it("discards late decision receipts when support state changes", async () => {
    const pending = deferred<unknown>()
    api.decide.mockReturnValue(pending.promise)
    render(<VisitPrepEmailPolicy access={access} scopeKey="user_synthetic" />)
    await load()
    approve()
    fireEvent(window, new Event("cosmaneura:view-as-hard-block"))
    await act(async () => pending.resolve({ data: {} }))
    expect(screen.queryByRole("status")).toBeNull()
    expect(
      screen.queryByRole("heading", {
        name: "Visit Prep invitation email policy",
      }),
    ).toBeNull()
  })

  it("treats a mismatched receipt as unknown, not a recorded decision", async () => {
    api.decide.mockResolvedValue({
      data: { disposition: "recorded", event_sequence: 99 },
    })
    render(<VisitPrepEmailPolicy access={access} scopeKey="user_synthetic" />)
    await load()
    approve()
    expect(await screen.findByRole("status")).toHaveTextContent(
      "outcome is unknown",
    )
    expect(screen.queryByText(/Decision recorded/)).toBeNull()
  })

  it.each([
    { contract: "unexpected" },
    { event_sequence: -1 },
    { delivery_status: "accepted" },
    { policy_fingerprint: "private_hash" },
    { template_text: "No placeholder" },
  ])(
    "rejects an incompatible response without displaying it",
    async (changes) => {
      api.read.mockResolvedValue({ data: { ...value, ...changes } })
      render(<VisitPrepEmailPolicy access={access} scopeKey="user_synthetic" />)
      fireEvent.click(
        screen.getByRole("button", { name: "Load / reload email policy" }),
      )
      expect(await screen.findByRole("status")).toHaveTextContent(
        "could not be loaded",
      )
      expect(
        screen.queryByRole("heading", { name: "Invitation preview" }),
      ).toBeNull()
    },
  )
})
