import { beforeEach, describe, expect, it, vi } from "vitest"

import { type VisitPrepSmsPolicyResponse } from "~/configuration/client"

import { VisitPrepSMSPolicy, validSMSPolicy } from "./VisitPrepSMSPolicy"
import { act, fireEvent, render, screen } from "@testing-library/react"

const api = vi.hoisted(() => ({
  read: vi.fn(),
  decide: vi.fn(),
  subscribe: vi.fn(),
}))
vi.mock("~/configuration/client", () => ({
  visitPrepSmsPolicyRead: (...args: unknown[]) => api.read(...args),
  visitPrepSmsPolicyDecide: (...args: unknown[]) => api.decide(...args),
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
const value: VisitPrepSmsPolicyResponse = {
  contract: "visit_prep.sms_policy.v1",
  template_version: "visit_prep_sms_v1",
  policy_fingerprint: "a".repeat(64),
  policy_text: "Fixed administrative SMS policy.",
  sender_digest: "b".repeat(64),
  templates: {
    booking_sms: "Prepare. {private_preparation_link}",
    unopened_48h_sms: "Open. {private_preparation_link}",
    unfinished_12h_sms:
      "Finish. {private_preparation_link} <script>not executable</script>",
  },
  status: "not_approved",
  event_sequence: 0,
  decision_policy_fingerprint: null,
  decision_sender_digest: null,
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
const approved: VisitPrepSmsPolicyResponse = {
  ...value,
  status: "approved",
  event_sequence: 1,
  decision_policy_fingerprint: value.policy_fingerprint,
  decision_sender_digest: value.sender_digest,
  recorded_at: "2026-10-01T11:00:00Z",
  expires_at: "2099-01-01T12:00:00Z",
}
function mount() {
  return render(<VisitPrepSMSPolicy access={access} scopeKey="synthetic" />)
}
async function load() {
  fireEvent.click(
    screen.getByRole("button", { name: "Load / reload SMS policy" }),
  )
  await screen.findByRole("heading", { name: "Booking invitation preview" })
}
function approve() {
  fireEvent.change(screen.getByLabelText("SMS approval expires at (UTC)"), {
    target: { value: "2099-01-01T12:00" },
  })
  fireEvent.click(screen.getByRole("checkbox"))
  fireEvent.click(
    screen.getByRole("button", { name: "Record physician SMS approval" }),
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
      event_sequence: body.expected_event_sequence + 1,
      current_event_sequence: body.expected_event_sequence + 1,
      event_kind: body.event_kind,
      policy_fingerprint: body.policy_fingerprint,
      expires_at: body.expires_at,
      recorded_at: "2026-10-01T12:01:00Z",
      disposition: "recorded",
    },
  }))
})
describe("physician SMS policy choices", () => {
  it("loads only on request, previews plain text and requires review and future UTC expiry", async () => {
    const { container } = mount()
    expect(api.read).not.toHaveBeenCalled()
    await load()
    expect(api.read).toHaveBeenCalledWith({ cache: "no-store" })
    expect(screen.getByText(/No messages from 21:00 to 08:00/)).toBeVisible()
    expect(container.querySelector("script")).toBeNull()
    expect(
      container.querySelectorAll('input[type="number"],select'),
    ).toHaveLength(0)
    const button = screen.getByRole("button", {
      name: "Record physician SMS approval",
    })
    expect(button).toBeDisabled()
    fireEvent.click(screen.getByRole("checkbox"))
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText("SMS approval expires at (UTC)"), {
      target: { value: "2000-01-01T12:00" },
    })
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText("SMS approval expires at (UTC)"), {
      target: { value: "2099-01-01T12:00" },
    })
    fireEvent.click(button)
    expect(await screen.findByRole("status")).toHaveTextContent(
      "No text was sent",
    )
    expect(api.decide).toHaveBeenCalledTimes(1)
    expect(api.decide.mock.calls[0][0]).toEqual({
      body: {
        expected_event_sequence: 0,
        event_kind: "approved",
        policy_fingerprint: value.policy_fingerprint,
        expected_sender_digest: value.sender_digest,
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
      screen.queryByRole("heading", { name: "Booking invitation preview" }),
    ).toBeNull()
    expect(Object.entries(localStorage)).toEqual([])
    expect(Object.entries(sessionStorage)).toEqual([])
  })
  it("withdraws the retained policy and sender after both change", async () => {
    api.read.mockResolvedValue({
      data: {
        ...approved,
        status: "policy_changed",
        policy_fingerprint: "d".repeat(64),
        sender_digest: "e".repeat(64),
      },
    })
    mount()
    await load()
    fireEvent.click(
      screen.getByRole("button", { name: "Withdraw physician SMS approval" }),
    )
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Decision recorded",
    )
    expect(api.decide.mock.calls[0][0].body).toEqual({
      expected_event_sequence: 1,
      event_kind: "withdrawn",
      policy_fingerprint: approved.decision_policy_fingerprint,
      expected_sender_digest: approved.decision_sender_digest,
      expires_at: null,
    })
  })
  it("shows missing sender and cadence honestly, forbidding new approval but retaining withdrawal", async () => {
    api.read.mockResolvedValue({
      data: {
        ...approved,
        sender_digest: null,
        standard_cadence: null,
        status: "sender_changed",
      },
    })
    mount()
    await load()
    approve()
    expect(screen.getByText(/No SMS sender is configured/)).toBeVisible()
    expect(screen.getByText(/Standard timing is unavailable/)).toBeVisible()
    expect(
      screen.getByRole("button", { name: "Record physician SMS approval" }),
    ).toBeDisabled()
    expect(api.decide).not.toHaveBeenCalled()
    expect(
      screen.getByRole("button", { name: "Withdraw physician SMS approval" }),
    ).toBeEnabled()
  })
  it("treats lost acknowledgment as unknown, clears the snapshot and never retries automatically", async () => {
    api.decide.mockRejectedValue(new Error("Synthetic private failure"))
    mount()
    await load()
    approve()
    expect(await screen.findByRole("status")).toHaveTextContent(
      "outcome is unknown",
    )
    expect(api.decide).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole("checkbox")).toBeNull()
    expect(screen.getByRole("status")).not.toHaveTextContent("private failure")
  })
  it.each(["blur", "pagehide", "hard_block", "scope", "access"])(
    "discards a pending read after %s",
    async (change) => {
      const pending = deferred<{ data: VisitPrepSmsPolicyResponse }>()
      api.read.mockReturnValue(pending.promise)
      const mounted = mount()
      fireEvent.click(
        screen.getByRole("button", { name: "Load / reload SMS policy" }),
      )
      if (change === "scope")
        mounted.rerender(
          <VisitPrepSMSPolicy access={access} scopeKey="other" />,
        )
      else if (change === "access")
        mounted.rerender(
          <VisitPrepSMSPolicy
            access={{ ...access, can_manage: false }}
            scopeKey="synthetic"
          />,
        )
      else
        act(() => {
          window.dispatchEvent(
            new Event(
              change === "hard_block"
                ? "cosmaneura:view-as-hard-block"
                : change,
            ),
          )
        })
      await act(async () => {
        pending.resolve({ data: value })
        await pending.promise
      })
      expect(
        screen.queryByRole("heading", { name: "Booking invitation preview" }),
      ).toBeNull()
      expect(api.decide).not.toHaveBeenCalled()
    },
  )
  it("discards a pending decision acknowledgment after scope invalidation", async () => {
    const pending = deferred<unknown>()
    api.decide.mockReturnValue(pending.promise)
    const mounted = mount()
    await load()
    approve()
    mounted.rerender(<VisitPrepSMSPolicy access={access} scopeKey="other" />)
    await act(async () => {
      pending.resolve({ error: "unavailable" })
      await pending.promise
    })
    expect(screen.queryByRole("status")).toBeNull()
    expect(api.decide).toHaveBeenCalledTimes(1)
  })
  it.each([
    { recipient: "+12025550123" },
    { status: "enabled" },
    { checked_at: "not-time" },
    { template_version: "other" },
    { sender_digest: "invalid" },
    { event_sequence: true },
    {
      standard_cadence: { ...value.standard_cadence, provider_editable: true },
    },
    {
      templates: {
        ...value.templates,
        booking_sms: "https://private.example.test/vp1_secret",
      },
    },
  ])("rejects malformed or expanded response %j", async (changes) => {
    api.read.mockResolvedValue({ data: { ...value, ...changes } })
    mount()
    fireEvent.click(
      screen.getByRole("button", { name: "Load / reload SMS policy" }),
    )
    expect(await screen.findByRole("status")).toHaveTextContent(
      "could not be loaded",
    )
    expect(screen.queryByRole("checkbox")).toBeNull()
    expect(api.decide).not.toHaveBeenCalled()
  })
  it.each([
    { status: "withdrawn", expires_at: null },
    { status: "expired", expires_at: "2026-10-01T11:30:00Z" },
    { status: "sender_changed", sender_digest: "e".repeat(64) },
    { status: "policy_changed", policy_fingerprint: "e".repeat(64) },
    { status: "identity_changed" },
  ])(
    "accepts a consistent retained status %j without claiming delivery",
    (changes) => {
      expect(
        validSMSPolicy({
          ...approved,
          ...changes,
        } as VisitPrepSmsPolicyResponse),
      ).toBe(true)
    },
  )
  it.each([
    { current_event_sequence: 0 },
    { expires_at: null },
    { event_kind: "withdrawn" },
    { operation_id: "unexpected" },
    { recorded_at: "not-time" },
  ])(
    "rejects inconsistent decision receipts %j as unknown",
    async (changes) => {
      api.decide.mockImplementation(async ({ body }) => ({
        data: {
          event_sequence: 1,
          current_event_sequence: 1,
          event_kind: body.event_kind,
          policy_fingerprint: body.policy_fingerprint,
          expires_at: body.expires_at,
          recorded_at: "2026-10-01T12:01:00Z",
          disposition: "recorded",
          ...changes,
        },
      }))
      mount()
      await load()
      approve()
      expect(await screen.findByRole("status")).toHaveTextContent(
        "outcome is unknown",
      )
    },
  )
})
