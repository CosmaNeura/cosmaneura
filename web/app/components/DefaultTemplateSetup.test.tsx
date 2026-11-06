import { beforeEach, expect, it, vi } from "vitest"

import { DefaultTemplateSetup } from "./DefaultTemplateSetup"
import { act, fireEvent, render, screen } from "@testing-library/react"

const state = vi.hoisted(() => ({ initialize: vi.fn(), viewAs: false }))
vi.mock("~/configuration/client", () => ({
  initializeNoteTemplates: state.initialize,
}))
vi.mock("~/components/ViewAsRouteGuard", () => ({
  useViewAsActive: () => state.viewAs,
}))
beforeEach(() => {
  vi.resetAllMocks()
  state.viewAs = false
})

it("initializes the current owner's SOAP without account or template data and refreshes its catalogue", async () => {
  state.initialize.mockResolvedValue({
    data: { status: "available", created: true },
  })
  const onCreated = vi.fn()
  render(<DefaultTemplateSetup onCreated={onCreated} />)
  await act(async () => {})
  expect(state.initialize).toHaveBeenCalledExactlyOnceWith({ body: {} })
  expect(onCreated).toHaveBeenCalledOnce()
  expect(screen.queryByRole("status")).not.toBeInTheDocument()
})

it("checks an uncertain initialization with the identical owner-free request", async () => {
  state.initialize.mockRejectedValueOnce(new Error("Synthetic lost response"))
  const onCreated = vi.fn()
  render(<DefaultTemplateSetup onCreated={onCreated} />)
  await screen.findByRole("alert")
  state.initialize.mockResolvedValue({
    data: { status: "available", created: false },
  })
  fireEvent.click(screen.getByRole("button", { name: "Check template setup" }))
  await act(async () => {})
  expect(state.initialize.mock.calls[1]).toEqual(state.initialize.mock.calls[0])
  expect(onCreated).toHaveBeenCalledOnce()
})

it("honors the retained deletion receipt and leaves custom creation/upload available", async () => {
  state.initialize.mockResolvedValue({
    data: { status: "deleted", created: false },
  })
  const onCreated = vi.fn()
  render(<DefaultTemplateSetup onCreated={onCreated} />)
  await screen.findByText(/Your supplied SOAP template was deleted/)
  expect(screen.queryByRole("button")).not.toBeInTheDocument()
  expect(onCreated).not.toHaveBeenCalled()
})

it("does not initialize in view-as and ignores an earlier reply when the account view changes", async () => {
  state.viewAs = true
  const onCreated = vi.fn()
  const rendered = render(<DefaultTemplateSetup onCreated={onCreated} />)
  expect(state.initialize).not.toHaveBeenCalled()
  let resolve!: (value: unknown) => void
  state.initialize.mockReturnValue(
    new Promise((value) => {
      resolve = value
    }),
  )
  state.viewAs = false
  rendered.rerender(<DefaultTemplateSetup onCreated={onCreated} />)
  expect(state.initialize).toHaveBeenCalledOnce()
  state.viewAs = true
  rendered.rerender(<DefaultTemplateSetup onCreated={onCreated} />)
  await act(async () => {
    resolve({ data: { status: "available", created: true } })
  })
  expect(onCreated).not.toHaveBeenCalled()
  expect(screen.queryByRole("status")).not.toBeInTheDocument()
})
