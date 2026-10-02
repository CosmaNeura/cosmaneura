import { beforeEach, expect, it, vi } from "vitest"

import type { BillingHeaderSource } from "~/configuration/client"
import { expected, op, source } from "~/lib/cab-provider-header.test-fixtures"

import { BillingHeaderButton } from "./BillingHeaderButton"
import { fireEvent, render, screen } from "@testing-library/react"

vi.mock("~/configuration/client", () => ({
  prepareBillingHeaderOperation: vi.fn(),
}))
vi.mock("./CabBillingHeaderConnection", () => ({
  CabBillingHeaderConnection: ({
    onUpdateVisit,
  }: {
    onUpdateVisit: (visit: typeof source.visitContext) => void
  }) => (
    <button onClick={() => onUpdateVisit(source.visitContext)}>
      Update visit details
    </button>
  ),
}))
beforeEach(() => vi.clearAllMocks())

it("explains source loss while retaining a correction to an already prepared visit", () => {
  const capability: BillingHeaderSource = {
    schemaVersion: "core.billing-header-source.v1",
    state: "review_required",
    source: expected,
    visit: {},
    latestOperation: {
      operationId: op,
      state: "prepared",
      sourceRevision: 1,
      createdAt: "2026-10-02T17:00:00Z",
    },
  }
  const page = render(
    <BillingHeaderButton noteId={expected.noteId} source={capability} />,
  )
  fireEvent.click(screen.getByRole("button", { name: "Billing header" }))
  fireEvent.click(screen.getByRole("button", { name: "Update visit details" }))
  fireEvent.change(screen.getByLabelText(/Service date/), {
    target: { value: "2026-10-01" },
  })
  page.rerender(
    <BillingHeaderButton
      noteId={expected.noteId}
      source={capability}
      available={false}
    />,
  )
  expect(screen.getByLabelText(/Service date/)).toHaveValue("2026-10-01")
  expect(screen.getByRole("alert")).toHaveTextContent("inputs are retained")
  expect(
    screen.getByRole("button", { name: "Confirm for billing" }),
  ).toBeDisabled()
  expect(
    screen.getByRole("button", { name: "Cancel visit changes" }),
  ).toBeEnabled()
})
