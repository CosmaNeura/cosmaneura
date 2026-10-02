import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  type BillingHeaderSource,
  prepareBillingHeaderOperation,
} from "~/configuration/client"

import { BillingHeaderButton } from "./BillingHeaderButton"
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"

vi.mock("~/configuration/client", () => ({
  prepareBillingHeaderOperation: vi.fn(),
}))

const source: BillingHeaderSource = {
  schemaVersion: "core.billing-header-source.v1",
  state: "review_required",
  source: {
    noteId: "note_synthetic",
    appointmentId: "appointment_synthetic",
    noteSchemaId: "schema_soap",
    contentSha256: "a".repeat(64),
    textSha256: "b".repeat(64),
    projectionVersion: "ordered-note-markdown.v1",
    excludedRecordingTime: true,
  },
  visit: {
    patientAge: null,
    patientSex: null,
    encounterType: null,
    placeOfService: null,
    providerSpecialty: null,
    serviceDate: null,
  },
  latestOperation: null,
}

function open() {
  render(<BillingHeaderButton noteId="note_synthetic" source={source} />)
  fireEvent.click(screen.getByRole("button", { name: "Billing header" }))
}
function fill() {
  fireEvent.change(screen.getByLabelText("Age at visit"), {
    target: { value: "34" },
  })
  fireEvent.change(screen.getByLabelText("Documented sex"), {
    target: { value: "F" },
  })
  fireEvent.change(screen.getByLabelText("Visit setting and patient type"), {
    target: { value: "office_established" },
  })
  fireEvent.change(screen.getByLabelText("Place of service"), {
    target: { value: "11" },
  })
  fireEvent.change(screen.getByLabelText("Provider specialty"), {
    target: { value: "Family medicine" },
  })
  fireEvent.click(screen.getByRole("checkbox"))
}
function success(options: unknown, overrides = {}) {
  const operationId = (options as { body: { operationId: string } }).body
    .operationId
  return {
    response: new Response(null, { status: 200 }),
    data: {
      schemaVersion: "core.billing-header-operation.v1",
      state: "prepared",
      operationId,
      sourceRevision: 1,
      source: source.source,
      completedAt: "2026-10-02T17:00:00Z",
      transport: { state: "not_configured" },
      replayed: false,
      ...overrides,
    },
  } as never
}

describe("BillingHeaderButton", () => {
  beforeEach(() => vi.clearAllMocks())

  it("requires explicit visit details and confirmation, with no request on open", () => {
    open()
    expect(prepareBillingHeaderOperation).not.toHaveBeenCalled()
    expect(screen.getByLabelText("Age at visit")).toHaveValue("")
    expect(screen.getByLabelText("Documented sex")).toHaveValue("")
    expect(screen.getByRole("checkbox")).not.toBeChecked()
    expect(
      screen.getByRole("button", { name: "Confirm for billing" }),
    ).toBeDisabled()
    expect(
      screen.getByText(/Recording-based care time will be excluded/),
    ).toBeVisible()
  })

  it("submits only confirmed context and exact source hashes, then honestly reports saved preparation", async () => {
    vi.mocked(prepareBillingHeaderOperation).mockImplementation(
      async (options) => success(options),
    )
    open()
    fill()
    fireEvent.click(screen.getByRole("button", { name: "Confirm for billing" }))
    expect(await screen.findByRole("status")).toHaveTextContent(
      "A header has not been generated",
    )
    const request = vi.mocked(prepareBillingHeaderOperation).mock.calls[0][0]!
    expect(request.path).toEqual({ note_id: "note_synthetic" })
    expect(request.body).toEqual({
      operationId: expect.any(String),
      expectedContentSha256: source.source.contentSha256,
      expectedTextSha256: source.source.textSha256,
      confirmedComplete: true,
      visit: {
        patientAge: 34,
        patientSex: "F",
        encounterType: "office_established",
        placeOfService: "11",
        providerSpecialty: "Family medicine",
      },
    })
    expect(JSON.stringify(request.body)).not.toMatch(
      /doctor_id|practice|noteText|transcript|patientName/,
    )
  })

  it("preserves the identical operation and locks form/close after an ambiguous save", async () => {
    vi.mocked(prepareBillingHeaderOperation).mockRejectedValueOnce(
      new Error("response lost"),
    )
    vi.mocked(prepareBillingHeaderOperation).mockImplementationOnce(
      async (options) => success(options, { replayed: true }),
    )
    open()
    fill()
    fireEvent.click(screen.getByRole("button", { name: "Confirm for billing" }))
    const retry = await screen.findByRole("button", {
      name: "Retry same confirmation",
    })
    expect(screen.getByLabelText("Age at visit")).toBeDisabled()
    fireEvent.click(
      within(screen.getByRole("dialog")).getAllByRole("button", {
        name: "Close",
      })[1],
    )
    expect(screen.getByRole("dialog")).toBeVisible()
    fireEvent.click(retry)
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Billing confirmation saved",
    )
    const calls = vi.mocked(prepareBillingHeaderOperation).mock.calls
    expect(calls[1][0]!.body).toEqual(calls[0][0]!.body)
  })

  it("does not claim success for a different operation receipt", async () => {
    vi.mocked(prepareBillingHeaderOperation).mockImplementation(
      async (options) =>
        success(options, {
          operationId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        }),
    )
    open()
    fill()
    fireEvent.click(screen.getByRole("button", { name: "Confirm for billing" }))
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "couldn't confirm",
    )
    expect(screen.queryByRole("status")).not.toBeInTheDocument()
  })

  it("stops after a source or access conflict", async () => {
    vi.mocked(prepareBillingHeaderOperation).mockResolvedValue({
      response: new Response(null, { status: 409 }),
    } as never)
    open()
    fill()
    fireEvent.click(screen.getByRole("button", { name: "Confirm for billing" }))
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Reload the note",
    )
    expect(
      screen.getByRole("button", { name: "Confirm for billing" }),
    ).toBeDisabled()
  })

  it("does not double-submit while the first request is pending", async () => {
    let finish: (value: never) => void = () => {}
    vi.mocked(prepareBillingHeaderOperation).mockImplementation(
      () =>
        new Promise<never>((resolve) => {
          finish = resolve
        }),
    )
    open()
    fill()
    const confirm = screen.getByRole("button", { name: "Confirm for billing" })
    fireEvent.click(confirm)
    fireEvent.click(confirm)
    expect(prepareBillingHeaderOperation).toHaveBeenCalledTimes(1)
    finish(success(vi.mocked(prepareBillingHeaderOperation).mock.calls[0][0]))
    await waitFor(() => expect(screen.getByRole("status")).toBeVisible())
  })

  it("requires renewed confirmation when visit details change", () => {
    open()
    fill()
    fireEvent.change(screen.getByLabelText("Age at visit"), {
      target: { value: "35" },
    })
    expect(screen.getByRole("checkbox")).not.toBeChecked()
    expect(
      screen.getByRole("button", { name: "Confirm for billing" }),
    ).toBeDisabled()
  })
})
