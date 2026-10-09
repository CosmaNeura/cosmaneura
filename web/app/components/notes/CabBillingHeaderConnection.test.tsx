import { webcrypto } from "node:crypto"
import { useState } from "react"
import { RouterProvider, createMemoryRouter } from "react-router"
import { beforeEach, expect, it, vi } from "vitest"

import {
  billingHeaderCab,
  billingHeaderGenerationContinue,
  billingHeaderOperation,
} from "~/configuration/client"
import {
  expected,
  header,
  op,
  reply,
} from "~/lib/cab-provider-header.test-fixtures"

import { CabBillingHeaderConnection } from "./CabBillingHeaderConnection"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

vi.mock("~/configuration/client", () => ({
  billingHeaderCab: vi.fn(),
  billingContinuationAuth: vi.fn(),
  billingHeaderGenerationContinue: vi.fn(),
  billingHeaderOperation: vi.fn(),
}))
beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal("crypto", webcrypto)
  vi.mocked(billingHeaderOperation).mockResolvedValue({
    response: new Response(null, { status: 200 }),
    data: {
      schemaVersion: "core.billing-header-operation.v1",
      operationId: op,
      state: "prepared",
      source: expected,
      transport: { state: "configured" },
    },
  } as never)
  vi.mocked(billingHeaderCab).mockImplementation(
    async ({ body }) =>
      ({
        response: new Response(null, { status: 200 }),
        data: reply(body.action, body.action === "read" ? header() : null),
      }) as never,
  )
})
function mount() {
  const initial = { operationId: op, source: expected, sourceStale: false }
  let update: (change: Partial<typeof initial>) => void = () => {}
  const locked = vi.fn()
  function Page() {
    const [props, setProps] = useState(initial)
    update = (change) => setProps((current) => ({ ...current, ...change }))
    return <CabBillingHeaderConnection {...props} onLocked={locked} />
  }
  render(
    <RouterProvider
      router={createMemoryRouter([{ path: "/", element: <Page /> }])}
    />,
  )
  return {
    change: (value: Partial<typeof initial>) => act(() => update(value)),
    locked,
  }
}
async function edit() {
  fireEvent.click(await screen.findByRole("button", { name: "Edit header" }))
  fireEvent.change(screen.getByLabelText("Visit type"), {
    target: { value: "Retained provider clarification" },
  })
  fireEvent.change(screen.getByLabelText("Reason for edit"), {
    target: { value: "Authored clarification" },
  })
}
it("preserves draft and exact retry through an equal source metadata refresh", async () => {
  const ui = mount()
  await edit()
  vi.mocked(billingHeaderCab).mockRejectedValue(new Error("lost save"))
  fireEvent.click(screen.getByRole("button", { name: "Save header edits" }))
  await screen.findByRole("button", { name: "Retry same request" })
  const first = vi.mocked(billingHeaderCab).mock.calls.at(-1)![0]!.body
  ui.change({ source: { ...expected } })
  expect(screen.getByLabelText("Visit type")).toHaveValue(
    "Retained provider clarification",
  )
  expect(
    screen.getByRole("button", { name: "Retry same request" }),
  ).toBeEnabled()
  expect(billingHeaderOperation).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole("button", { name: "Retry same request" }))
  await waitFor(() => expect(billingHeaderCab).toHaveBeenCalledTimes(4))
  expect(vi.mocked(billingHeaderCab).mock.calls.at(-1)![0]!.body).toEqual(first)
  expect(ui.locked).toHaveBeenLastCalledWith(true)
})
it.each([
  { source: { ...expected, contentSha256: "f".repeat(64) } },
  { operationId: "77777777-7777-4777-8777-777777777777" },
  { sourceStale: true },
])(
  "retains the draft and permits explicit discard after scope changes %j",
  async (change) => {
    const ui = mount()
    await edit()
    ui.change(change)
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "unsaved inputs are retained",
    )
    expect(screen.getByLabelText("visit type")).toHaveValue(
      "Retained provider clarification",
    )
    expect(
      screen.getByRole("button", { name: "Save header edits" }),
    ).toBeDisabled()
    expect(billingHeaderOperation).toHaveBeenCalledTimes(1)
    expect(billingHeaderCab).toHaveBeenCalledTimes(2)
    fireEvent.click(screen.getByRole("button", { name: "Cancel edits" }))
    await waitFor(() => expect(ui.locked).toHaveBeenLastCalledWith(false))
  },
)
it("does not continue acceptance with a read after source authority changes in flight", async () => {
  let finish: (value: never) => void = () => {}
  vi.mocked(billingHeaderCab).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }) as never,
  )
  const ui = mount()
  await waitFor(() => expect(billingHeaderCab).toHaveBeenCalledTimes(1))
  ui.change({ sourceStale: true })
  await act(async () =>
    finish({
      response: new Response(null, { status: 200 }),
      data: reply("accept"),
    } as never),
  )
  expect(billingHeaderCab).toHaveBeenCalledTimes(1)
  expect(
    screen.queryByRole("button", { name: "Generate billing header" }),
  ).toBeNull()
  expect(screen.getByRole("alert")).toHaveTextContent("Reload".toLowerCase())
})
